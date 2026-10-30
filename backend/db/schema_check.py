
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from backend.db.database import Base, engine
from backend.db import (
    models,
    auth_models,
    user_data_models,
    ai_models,
    idempotency_models,
    lease_models,
    auth_security_models,
)


def main():
    with engine.connect() as connection:
        inspector = inspect(connection)

        actual_tables = set(
            inspector.get_table_names()
        ) - {"alembic_version"}

        model_tables = set(
            Base.metadata.tables.keys()
        )

        print("MODEL TABLES:")
        print(sorted(model_tables))

        print("\nDATABASE TABLES:")
        print(sorted(actual_tables))

        print("\nMISSING TABLES:")
        print(sorted(model_tables - actual_tables))

        print("\nUNEXPECTED TABLES:")
        print(sorted(actual_tables - model_tables))

        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "target_metadata": Base.metadata,
            },
        )

        changes = compare_metadata(
            context, Base.metadata
        )

        print("\nSCHEMA DIFFERENCES:")
        for change in changes:
            print(repr(change))

        if not changes:
            print("\nPASS: Schema matches models")
        else:
            print("\nREVIEW REQUIRED: Schema differences found")
            raise SystemExit(1)


if __name__ == "__main__":
    main()

