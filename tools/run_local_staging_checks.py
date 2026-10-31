"""Owned loopback HTTP/HTTPS integration and synthetic load; no real credentials."""
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]


class TLSBridge(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def forward(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        with httpx.Client(timeout=140, trust_env=False) as client:
            result = client.request(self.command, "http://127.0.0.1:3002" + self.path,
                                    content=body, headers=dict(self.headers))
        self.send_response(result.status_code)
        for key, value in result.headers.multi_items():
            if key.lower() not in {"transfer-encoding", "content-length", "connection", "content-encoding"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(result.content)))
        self.end_headers()
        self.wfile.write(result.content)

    do_GET = do_POST = do_DELETE = do_OPTIONS = forward


def certificate(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


def wait(url, processes):
    with httpx.Client(trust_env=False) as client:
        for _ in range(100):
            assert all(p.poll() is None for p in processes), "Fixture failed to start"
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.1)
    raise RuntimeError("Fixture readiness timeout")


async def probe(cert, user, password):
    context = ssl.create_default_context(cafile=str(cert))
    args = dict(base_url="https://localhost:3443", verify=context, trust_env=False,
                auth=(user, password), timeout=140, headers={"Origin": "https://localhost:3443"})
    clients = [httpx.AsyncClient(**args) for _ in range(4)]
    result = {}
    try:
        async with httpx.AsyncClient(base_url=args["base_url"], verify=context, trust_env=False) as anon:
            blocked = await anon.get("/")
            assert blocked.status_code == 401
            assert blocked.headers["strict-transport-security"] == "max-age=31536000"
            assert (await anon.get("/healthz")).status_code == 200
        page = await clients[0].get("/")
        assert page.status_code == 200
        assert page.headers["strict-transport-security"] == "max-age=31536000"
        tokens = []
        chats = []
        for i, client in enumerate(clients):
            boot = (await client.get("/api/v1/auth/csrf", headers={"X-CSRF-Request": "1"})).json()
            login = await client.post("/api/v1/auth/login", json={"email": f"load{i}@example.com",
                                     "password": "Synthetic local password 123!"},
                                      headers={"X-CSRF-Token": boot["auth_csrf_token"]})
            assert login.status_code == 200, login.status_code
            cookie = login.headers["set-cookie"]
            assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
            boot = (await client.get("/api/v1/auth/csrf", headers={"X-CSRF-Request": "1"})).json()
            tokens.append(boot["csrf_token"])
            chat = await client.post("/api/v1/user/chats", json={"title": "Synthetic load"},
                                     headers={"X-CSRF-Token": tokens[-1]})
            assert chat.status_code == 201
            chats.append(chat.json()["id"])
        c = clients[0]
        assert (await c.get("/api/v1/health")).status_code == 200
        assert (await c.post("/api/v1/user/chats", json={})).status_code == 403
        assert (await c.post("/api/v1/user/chats", json={}, headers={"X-CSRF-Token": tokens[0],
                                                                    "Origin": "https://evil.invalid"})).status_code == 403
        assert (await clients[1].get("/api/v1/user/chats/"+chats[0])).status_code == 404
        deletion = await c.request("DELETE", "/api/v1/user/account", json={
            "password": "Synthetic local password 123!", "confirmation": "DELETE MY ACCOUNT"},
            headers={"X-CSRF-Token": tokens[0]})
        assert deletion.status_code == 503
        durations = []
        semaphore = asyncio.Semaphore(8)
        async def read(i):
            async with semaphore:
                start = time.perf_counter()
                response = await clients[i % 4].get("/api/v1/auth/me")
                durations.append((time.perf_counter()-start)*1000)
                return response.status_code
        statuses = await asyncio.gather(*(read(i) for i in range(100)))
        assert set(statuses) == {200}, Counter(statuses)
        ordered = sorted(durations)
        result["authenticated_reads"] = {"requests": 100, "concurrency": 8,
            "statuses": dict(Counter(statuses)), "p50_ms": round(ordered[49], 2),
            "p95_ms": round(ordered[94], 2), "p99_ms": round(ordered[98], 2)}
        keys = [str(uuid.uuid4()) for _ in range(4)]
        async def send(i, key, message):
            return await clients[i].post(f"/api/v1/ai/chats/{chats[i]}/send", json={"message": message},
                headers={"X-CSRF-Token": tokens[i], "Idempotency-Key": key})
        responses = await asyncio.gather(*(send(i, keys[i], "synthetic load") for i in range(4)))
        codes = Counter(r.status_code for r in responses)
        assert codes[200] >= 1 and set(codes) <= {200, 429}, codes
        result["ai_capacity_burst"] = {"requests": 4, "statuses": dict(codes), "configured_capacity": 2}
        winner = next(i for i, r in enumerate(responses) if r.status_code == 200)
        assert (await send(winner, keys[winner], "synthetic load")).json() == responses[winner].json()
        start = time.perf_counter()
        slow = await send(0, str(uuid.uuid4()), "slow proxy probe")
        assert slow.status_code == 200, slow.status_code
        result["proxy_beyond_old_30s_timeout"] = {"status": slow.status_code, "seconds": round(time.perf_counter()-start, 2)}
        failed = await send(1, str(uuid.uuid4()), "upstream failure")
        assert failed.status_code == 500 and "synthetic-private-upstream-detail" not in failed.text
        assert (await send(1, str(uuid.uuid4()), "recovery after failure")).status_code == 200
        await c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": tokens[0]})
        assert (await c.get("/api/v1/auth/me")).status_code == 401
        result.update(secure_cookie_csrf_origin_ownership="PASS", runtime_hsts="PASS", deletion="DISABLED",
                      idempotency_failure_recovery_logout="PASS", tls="local certificate verified",
                      external_email_offsite_neon_render="NOT TESTED", database="temporary synthetic SQLite")
        result["measured_at_utc"] = datetime.now(timezone.utc).isoformat()
        result["platform"] = sys.platform
        return result
    finally:
        for client in clients:
            await client.aclose()


def main():
    processes = []
    bridge = None
    with tempfile.TemporaryDirectory(prefix="nafsiy-local-staging-") as directory:
        directory = Path(directory)
        cert, key = certificate(directory)
        user, password = "synthetic", secrets.token_urlsafe(32)
        try:
            env = dict(os.environ, NAFSIY_LOCAL_STAGING_CHECK="1", NAFSIY_FIXTURE_DB=str(directory/"fixture.db"))
            with (directory/"backend.log").open("w") as log:
                processes.append(subprocess.Popen([sys.executable, "tests/local_staging_server.py"], cwd=ROOT,
                                                  env=env, stdout=log, stderr=log))
                wait("http://127.0.0.1:8103/api/v1/health", processes)
                env.update(APP_ENV="staging", API_INTERNAL_URL="http://127.0.0.1:8103",
                           STAGING_ACCESS_USER=user, STAGING_ACCESS_PASSWORD=password)
                processes.append(subprocess.Popen(["node", "node_modules/next/dist/bin/next", "start", "--hostname",
                    "127.0.0.1", "--port", "3002"], cwd=ROOT/"frontend", env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                wait("http://127.0.0.1:3002/healthz", processes)
                bridge = ThreadingHTTPServer(("127.0.0.1", 3443), TLSBridge)
                tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                tls.load_cert_chain(cert, key)
                bridge.socket = tls.wrap_socket(bridge.socket, server_side=True)
                threading.Thread(target=bridge.serve_forever, daemon=True).start()
                result = asyncio.run(probe(cert, user, password))
                (ROOT/"evaluations/local_staging_results.json").write_text(json.dumps(result, indent=2)+"\n")
                print(json.dumps(result, indent=2))
        finally:
            if bridge:
                bridge.shutdown()
                bridge.server_close()
            for process in reversed(processes):
                if os.name == "nt" and process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
