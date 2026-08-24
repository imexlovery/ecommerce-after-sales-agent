from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from after_sales_agent.storage.models import Base


def test_initial_alembic_migration_builds_authoritative_schema(tmp_path: Path):
    repository_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "migration.db"
    configuration = Config(repository_root / "alembic.ini")
    configuration.set_main_option("script_location", str(repository_root / "migrations"))
    configuration.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(configuration, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        expected_tables = {
            "action_executions",
            "action_proposals",
            "alembic_version",
            "conversations",
            "events",
            "investigation_cases",
            "messages",
            "policy_decisions",
            "runs",
            "tickets",
            "tool_calls",
            "triage_records",
        }
        assert set(inspector.get_table_names()) == expected_tables
        assert {
            "case_state",
            "case_outcome",
        }.issubset({column["name"] for column in inspector.get_columns("investigation_cases")})
        assert "run_state" in {column["name"] for column in inspector.get_columns("runs")}
        assert "proposal_state" in {
            column["name"] for column in inspector.get_columns("action_proposals")
        }
        assert "action_state" in {
            column["name"] for column in inspector.get_columns("action_executions")
        }
        assert {
            "event_type",
            "schema_version",
            "sequence",
        }.issubset({column["name"] for column in inspector.get_columns("events")})
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()
