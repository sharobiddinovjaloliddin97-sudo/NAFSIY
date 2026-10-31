"""Report only locations and counts; never print secret values."""
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit
from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parents[1]


def main():
    values=dotenv_values(ROOT/".env")
    needles=set()
    for key,value in values.items():
        if value and any(part in key for part in ("SECRET","PASSWORD","API_KEY","DATABASE_URL")):
            if len(value)>=12: needles.add(value.encode())
            if "DATABASE_URL" in key:
                password=urlsplit(value).password
                if password and len(password)>=8: needles.add(password.encode())
    tracked=subprocess.check_output(["git","ls-files","-z"],cwd=ROOT).decode().split("\0")
    changed=subprocess.check_output(["git","ls-files","--others","--exclude-standard","-z"],cwd=ROOT).decode().split("\0")
    hits=[]
    for name in set(tracked+changed):
        path=ROOT/name
        if name and path.is_file() and any(secret in path.read_bytes() for secret in needles): hits.append(name)
    history_hits=[]
    for commit in subprocess.check_output(["git","rev-list","--all"],cwd=ROOT).decode().splitlines():
        archive=subprocess.check_output(["git","archive",commit],cwd=ROOT)
        if any(secret in archive for secret in needles): history_hits.append(commit[:12])
    ignored={name:subprocess.run(["git","check-ignore","-q",name],cwd=ROOT).returncode==0
             for name in (".env","frontend/.env.local","backups/example.dump","tmp/example")}
    result={"tracked_and_untracked_configured_secret_matches":sorted(hits),"history_configured_secret_matches":history_hits,
            "ignored_private_paths":ignored,"scope":"Configured current secrets only; not a universal credential detector"}
    print(json.dumps(result,indent=2))
    return int(bool(hits or history_hits or not all(ignored.values())))


if __name__=="__main__": raise SystemExit(main())
