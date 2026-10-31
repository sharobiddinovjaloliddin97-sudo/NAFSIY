"""Read-only provider schema validation plus local deployment invariants."""
import json
from pathlib import Path
import sys

import httpx
import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    response = httpx.get("https://render.com/schema/render.yaml.json", timeout=30, follow_redirects=True)
    response.raise_for_status()
    schema = response.json()
    validator = jsonschema.Draft202012Validator(schema)

    # 1. Paid Blueprint validation (render.yaml)
    paid_blueprint = yaml.safe_load((ROOT / "render.yaml").read_text())
    validator.validate(paid_blueprint)
    paid_services = paid_blueprint["services"]
    assert {item["type"] for item in paid_services} == {"web", "pserv"}
    assert all(item["autoDeployTrigger"] == "off" for item in paid_services)
    assert all("preDeployCommand" not in item and "initialDeployHook" not in item for item in paid_services)
    assert not any("value" in env for item in paid_services for env in item["envVars"]
                   if env["key"] in {"DATABASE_URL", "JWT_SECRET_KEY", "GROQ_API_KEY", "SMTP_PASSWORD", "STAGING_ACCESS_PASSWORD"})
    for item in paid_services:
        dockerfile = (ROOT / item["dockerfilePath"]).read_text()
        assert "upgrade head" not in dockerfile and "create_all" not in dockerfile
        assert "USER " in dockerfile

    # 2. Zero-budget Free Blueprint validation (render.free.yaml)
    free_blueprint = yaml.safe_load((ROOT / "render.free.yaml").read_text())
    validator.validate(free_blueprint)
    free_services = free_blueprint["services"]
    assert {item["type"] for item in free_services} == {"web"}
    assert all(item["plan"] == "free" for item in free_services)
    assert all(item["autoDeployTrigger"] == "off" for item in free_services)
    assert all("preDeployCommand" not in item and "initialDeployHook" not in item for item in free_services)
    assert not any("value" in env for item in free_services for env in item["envVars"]
                   if env["key"] in {"DATABASE_URL", "JWT_SECRET_KEY", "GROQ_API_KEY", "RESEND_API_KEY",
                                     "STAGING_ACCESS_PASSWORD", "STAGING_PROXY_SECRET"})
    for item in free_services:
        dockerfile = (ROOT / item["dockerfilePath"]).read_text()
        assert "upgrade head" not in dockerfile and "create_all" not in dockerfile
        assert "USER " in dockerfile

    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert all(item in ignored for item in (".env", "**/.env.*", "backups", "tmp", "**/node_modules"))
    print(json.dumps({"render_schema_valid": True, "deployment_invariants": "pass", "deployed": False}))


if __name__ == "__main__":
    main()
