"""Read-only provider schema validation plus local deployment invariants."""
import json
from pathlib import Path
import sys

import httpx
import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text())
    response = httpx.get("https://render.com/schema/render.yaml.json", timeout=30, follow_redirects=True)
    response.raise_for_status()
    jsonschema.Draft202012Validator(response.json()).validate(blueprint)
    services = blueprint["services"]
    assert {item["type"] for item in services} == {"web", "pserv"}
    assert all(item["autoDeployTrigger"] == "off" for item in services)
    assert all("preDeployCommand" not in item and "initialDeployHook" not in item for item in services)
    assert not any("value" in env for item in services for env in item["envVars"]
                   if env["key"] in {"DATABASE_URL", "JWT_SECRET_KEY", "GROQ_API_KEY", "SMTP_PASSWORD", "STAGING_ACCESS_PASSWORD"})
    for item in services:
        dockerfile = (ROOT / item["dockerfilePath"]).read_text()
        assert "upgrade head" not in dockerfile and "create_all" not in dockerfile
        assert "USER " in dockerfile
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert all(item in ignored for item in (".env", "**/.env.*", "backups", "tmp", "**/node_modules"))
    print(json.dumps({"render_schema_valid": True, "deployment_invariants": "pass", "deployed": False}))


if __name__ == "__main__":
    main()
