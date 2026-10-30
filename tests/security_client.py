import httpx


POSTGRES_TEST_SECRET = "postgres-idempotency-test-secret-with-sufficient-length"


def cleanup_postgres_bootstrap(db):
    from unittest.mock import patch
    from sqlalchemy import delete
    from backend.core.auth_rate_limit import digest
    from backend.core.config import settings
    from backend.db.auth_security_models import AuthRateLimit

    with patch.object(settings, "JWT_SECRET_KEY", POSTGRES_TEST_SECRET):
        prefix = "bootstrap-ip:" + digest("bootstrap-ip", "127.0.0.1") + ":%"
    db.execute(delete(AuthRateLimit).where(AuthRateLimit.bucket_key.like(prefix)))


class SecurityClient(httpx.AsyncClient):
    async def request(self, method, url, **kwargs):
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            headers = httpx.Headers(kwargs.get("headers"))
            if "X-CSRF-Token" not in headers:
                bootstrap_headers = {"X-CSRF-Request": "1"}
                for name in ("Origin", "Cookie"):
                    if name in headers:
                        bootstrap_headers[name] = headers[name]
                response = await super().request("GET", "/api/v1/auth/csrf", headers=bootstrap_headers)
                if response.status_code == 200:
                    field = "auth_csrf_token" if str(url).split("?")[0].endswith(("/login", "/register")) else "csrf_token"
                    token = response.json().get(field)
                    if token:
                        headers["X-CSRF-Token"] = token
            kwargs["headers"] = headers
        return await super().request(method, url, **kwargs)
