import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

if os.getenv("NAFSIY_BROWSER_TEST") != "1":
    raise SystemExit("Set NAFSIY_BROWSER_TEST=1 to start the isolated browser fixture")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
import uvicorn

from backend.main import app
from backend.api import ai, auth
from backend.core import leases
from backend.db.database import Base, get_db
from backend.db.user_data_models import ChatMessage


leases.LEASE_SECONDS = 1
leases.RENEW_INTERVAL_SECONDS = 0.1
root = tempfile.TemporaryDirectory(prefix="nafsiy-operations-browser-")
engine = create_engine("sqlite:///" + str(Path(root.name) / "fixture.db"), connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def foreign_keys(connection, _):
    connection.execute("PRAGMA foreign_keys=ON")


Base.metadata.create_all(engine)


def local_db():
    with Session(engine) as db:
        yield db


app.dependency_overrides[get_db] = local_db
app.state.auth_maintenance_engine = engine
auth.settings.AUTH_COOKIE_SECURE = False
auth.settings.ACCOUNT_DELETION_ENABLED = True
auth.settings.JWT_SECRET_KEY = "isolated-browser-fixture-secret-not-for-production"
calls = 0


async def generate(question, history=None):
    global calls
    calls += 1
    if question.startswith("Slow"):
        await asyncio.sleep(3)
    if question.startswith(("Source", "Unsupported")):
        from backend.ai.retriever import source
        from backend.ai.citation_guard import validate_grounded_answer
        from backend.ai.agent import INVALID_CITATIONS
        record = source("quran", "1:1", "AlQuran Cloud", translation="Synthetic browser source text.")
        raw = {"explanation": "Cautious explanation [1:1]", "quotations": []}
        if question.startswith("Source quotation"):
            raw["quotations"] = [{"source_id": record["source_id"], "reference": "1:1", "language": "en",
                                  "edition": "en.itani", "text": record["translation"]}]
        if question.startswith("Unsupported"):
            raw["explanation"] = '"Invented quotation" [1:1]'
        checked = validate_grounded_answer(json.dumps(raw), [record], "en")
        return {"answer": checked["answer"] if checked["valid"] else INVALID_CITATIONS["en"],
                "sources": checked.get("sources", []), "model": "browser-fixture"}
    return {"answer": "Fixture answer", "sources": [], "model": "browser-fixture"}


ai.ask_nafsiy = generate


@app.get("/api/v1/__test/state")
def state():
    with Session(engine) as db:
        from backend.db.auth_models import User
        return {"model_calls": calls, "messages": db.scalar(select(func.count()).select_from(ChatMessage)),
                "accounts": db.scalar(select(func.count()).select_from(User))}


from backend.core import recovery
outbox = []


class CaptureEmail:
    def send(self, recipient, purpose, token):
        outbox.append({"recipient": recipient, "purpose": purpose, "token": token})


recovery.delivery = lambda: CaptureEmail()


@app.get("/api/v1/__test/outbox")
def recovery_outbox():
    return outbox


try:
    uvicorn.run(app, host="127.0.0.1", port=8102, log_level="warning", proxy_headers=False)
finally:
    engine.dispose()
    root.cleanup()
