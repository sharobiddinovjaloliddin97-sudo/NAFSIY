import argparse
import json
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.core.ai_operations import snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", choices=("test", "primary"), default="test")
    args = parser.parse_args()
    load_dotenv()
    variable = "TEST_DATABASE_URL" if args.database == "test" else "DATABASE_URL"
    try:
        value = os.environ[variable]
        url = make_url(value)
        if args.database == "test":
            primary = make_url(os.environ["DATABASE_URL"])
            if ((url.host or "").replace("-pooler", ""), url.port or 5432, url.database) == (
                (primary.host or "").replace("-pooler", ""), primary.port or 5432, primary.database,
            ):
                raise ValueError("Test target must differ")
        engine = create_engine(value.replace("postgresql://", "postgresql+psycopg://", 1), connect_args={"connect_timeout": 10})
        try:
            with Session(engine) as db:
                print(json.dumps(snapshot(db), sort_keys=True))
        finally:
            engine.dispose()
    except Exception as error:
        print(json.dumps({"status": "unavailable", "category": type(error).__name__}))
        sys.exit(1)


if __name__ == "__main__":
    main()
