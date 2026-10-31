"""Local-only built Next gateway probe. Synthetic random credentials never leave memory."""
import base64
import os
from pathlib import Path
import secrets
import subprocess
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    user, password = "synthetic-reviewer", secrets.token_urlsafe(32)
    env = dict(os.environ, APP_ENV="staging", API_INTERNAL_URL="http://127.0.0.1:8102",
               STAGING_ACCESS_USER=user, STAGING_ACCESS_PASSWORD=password)
    process = subprocess.Popen(["node", "node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", "3001"],
                               cwd=ROOT / "frontend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url="http://127.0.0.1:3001", timeout=10) as client:
            for _ in range(50):
                if process.poll() is not None:
                    raise RuntimeError("Local gateway fixture failed to start")
                try:
                    if client.get("/healthz").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            assert client.get("/").status_code == 401
            assert client.get("/api/v1/auth/me").status_code == 401
            assert client.get("/_next/static/example.js").status_code == 401
            assert client.get("/", headers={"Authorization": "Basic invalid"}).status_code == 401
            headers = {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}
            assert client.get("/", headers=headers).status_code == 200
            response = client.get("/api/v1/auth/me", headers=headers)
            assert response.status_code == 401 and response.json()["detail"] == "Not authenticated"
            boot = client.get("/api/v1/auth/csrf", headers={**headers, "X-CSRF-Request": "1"})
            assert boot.status_code == 200 and boot.json()["auth_csrf_token"]
        print("PASS: local built staging gate protects pages/assets/API; valid gate proxies cookies and CSRF to isolated backend")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
