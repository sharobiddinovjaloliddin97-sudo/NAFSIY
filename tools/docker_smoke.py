"""Linux CI: default Docker commands, non-root users, health and idle SIGTERM exit.

Only synthetic SQLite data; no production settings, credentials, migrations or mail.
Images must already exist locally. Nothing is pushed to an image registry.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, env=None):
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, env=env, timeout=180)
    except FileNotFoundError:
        raise RuntimeError("Docker CLI executable not found on PATH. Docker is required for container smoke tests.")
    if result.returncode:
        err = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Docker smoke operation failed ({' '.join(args)}): {err}")
    return result.stdout.strip()


def port(name):
    ports = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", name))
    return "http://127.0.0.1:" + ports["10000/tcp"][0]["HostPort"]


def ready(client, url, path, name=None):
    for _ in range(100):
        try:
            response = client.get(url + path)
            if response.status_code == 200:
                return response
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    logs = ""
    if name:
        try:
            logs = "\nContainer logs:\n" + docker("logs", "--tail", "50", name)
        except Exception:
            pass
    raise RuntimeError(f"Docker image readiness failed for {name or url + path}{logs}")


def main():
    prefix = "nafsiy-ci-" + uuid.uuid4().hex[:12]
    network, api, web = prefix, prefix + "-api", prefix + "-web"
    created = []
    result = {"scope": "Linux Docker default CMD, synthetic SQLite; external services NOT TESTED"}
    docker("network", "create", network)
    try:
        docker("run", "-d", "--name", api, "--network", network,
               "-p", "127.0.0.1::10000", "-e", "DATABASE_URL=sqlite:////tmp/synthetic-smoke.db",
               "-e", "APP_ENV=development", "-e", "ACCOUNT_DELETION_ENABLED=false",
               "-e", "EMAIL_MODE=disabled", "-e", "GROQ_API_KEY=",
               "-e", "JWT_SECRET_KEY=synthetic-local-ci-signing-key-not-for-real-users",
               "nafsiy-backend:ci")
        created.append(api)
        env = dict(os.environ, STAGING_ACCESS_PASSWORD=secrets.token_urlsafe(32))
        docker("run", "-d", "--name", web, "--network", network,
               "-p", "127.0.0.1::10000", "-e", "APP_ENV=staging",
               "-e", "API_INTERNAL_URL=http://" + api + ":10000",
               "-e", "STAGING_ACCESS_USER=synthetic", "-e", "STAGING_ACCESS_PASSWORD",
               "nafsiy-frontend:ci", env=env)
        created.append(web)
        with httpx.Client(timeout=10, trust_env=False) as client:
            ready(client, port(api), "/api/v1/health", name=api)
            ready(client, port(web), "/healthz", name=web)
            assert int(docker("exec", api, "python", "-c", "import os; print(os.getuid())")) > 0
            assert int(docker("exec", web, "node", "-p", "process.getuid()")) > 0
            # The image never receives the checkout's local env or private backups.
            docker("exec", api, "python", "-c",
                   "from pathlib import Path; assert not any(Path('/app',p).exists() for p in ['.env','backups','tmp'])")
            docker("exec", web, "node", "-e",
                   "const fs=require('fs'); for(const p of ['.env','.env.local','backups']) if(fs.existsSync('/app/'+p)) process.exit(1)")
            response = client.get(port(web) + "/")
            assert response.status_code == 401 and "strict-transport-security" in response.headers
            auth = ("synthetic", env["STAGING_ACCESS_PASSWORD"])
            assert client.get(port(web) + "/", auth=auth).status_code == 200
            assert client.get(port(web) + "/api/v1/health", auth=auth).status_code == 200
            assert client.get(port(web) + "/api/v1/auth/me", auth=auth).status_code == 401
        result.update(non_root_default_startup="PASS", health_private_network_proxy="PASS",
                      gate_runtime_hsts="PASS", image_private_files_absent="PASS")
        exits = {}
        for name in reversed(created):
            docker("stop", "--time", "15", name)
            state = json.loads(docker("inspect", "--format", "{{json .State}}", name))
            assert not state["Running"] and state["ExitCode"] == 0, "Non-graceful idle shutdown"
            exits["frontend" if name == web else "backend"] = state["ExitCode"]
        result["idle_sigterm_exit_codes"] = exits
        (ROOT / "evaluations/docker_smoke_results.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
    finally:
        for name in reversed(created):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=30)


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
