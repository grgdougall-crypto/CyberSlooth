"""Small SQLAlchemy persistence layer for CyberSlooth Stage 0.6."""

from __future__ import annotations

import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, create_engine, inspect, select, text, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_DATABASE_PATH = PROJECT_ROOT / "data" / "cyberslooth.db"
DUPLICATE_WINDOW = timedelta(minutes=10)


class Base(DeclarativeBase):
    pass


class ResearchRun(Base):
    __tablename__ = "research_runs"
    __table_args__ = (
        Index("idx_research_runs_fingerprint_created", "payload_fingerprint", "created_at"),
        Index("idx_research_runs_created", "created_at"),
        Index("idx_research_runs_daily_selected", "daily_candidate_selected", "daily_candidate_evaluated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    starting_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    final_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    archive_decision: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    research_value: Mapped[str | None] = mapped_column(String(16), nullable=True)
    exploration_performed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    original_evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    original_analysis_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    exploration_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    synthesis_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    daily_candidate_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_candidate_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_candidate_selected: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    daily_candidate_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


_engine = None
_session_factory: sessionmaker[Session] | None = None


def configured_database_url() -> str:
    """Return a SQLAlchemy URL, requiring Postgres when Railway is detected."""

    raw = os.environ.get("DATABASE_URL", "").strip()
    if raw:
        if raw.startswith("postgres://"):
            return "postgresql+psycopg://" + raw.removeprefix("postgres://")
        if raw.startswith("postgresql://"):
            return "postgresql+psycopg://" + raw.removeprefix("postgresql://")
        return raw
    railway_markers = (
        "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_ID", "RAILWAY_ENVIRONMENT_NAME",
        "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID",
    )
    if any(os.environ.get(name) for name in railway_markers):
        raise RuntimeError("DATABASE_URL is required when CyberSlooth runs on Railway.")
    LOCAL_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + LOCAL_DATABASE_PATH.as_posix()


def configure_database(url: str | None = None) -> str:
    """Configure the engine and apply the additive Stage 0.6 schema upgrade."""

    global _engine, _session_factory
    selected_url = url or configured_database_url()
    previous_engine = _engine
    _engine = create_engine(selected_url, pool_pre_ping=True)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    _ensure_stage_06_columns()
    if _engine.dialect.name == "sqlite":
        with _engine.begin() as connection:
            connection.execute(text("PRAGMA optimize"))
    if previous_engine is not None:
        previous_engine.dispose()
    return selected_url


def _ensure_stage_06_columns() -> None:
    """Add only the nullable Stage 0.6 fields to an existing Stage 0.5 table."""

    if _engine is None:
        raise RuntimeError("The archive database is not initialized.")
    existing = {column["name"] for column in inspect(_engine).get_columns("research_runs")}
    additions = {
        "daily_candidate_score": Integer(),
        "daily_candidate_rank": Integer(),
        "daily_candidate_selected": Boolean(),
        "daily_candidate_evaluated_at": DateTime(timezone=True),
    }
    with _engine.begin() as connection:
        for name, column_type in additions.items():
            if name not in existing:
                compiled = column_type.compile(dialect=_engine.dialect)
                connection.execute(text(f"ALTER TABLE research_runs ADD COLUMN {name} {compiled}"))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_research_runs_daily_selected "
            "ON research_runs (daily_candidate_selected, daily_candidate_evaluated_at)"
        ))


@contextmanager
def database_session() -> Iterator[Session]:
    if _session_factory is None:
        raise RuntimeError("The archive database is not initialized.")
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _new_public_id(now: datetime) -> str:
    return f"CS-{now:%Y%m%d}-{secrets.token_hex(3).upper()}"


def create_research_run(data: dict[str, Any], fingerprint: str) -> tuple[ResearchRun, bool]:
    """Create one run, or return the recent identical record without duplicating it."""

    now = datetime.now(timezone.utc)
    with database_session() as session:
        duplicate = session.scalar(
            select(ResearchRun)
            .where(ResearchRun.payload_fingerprint == fingerprint)
            .where(ResearchRun.created_at >= now - DUPLICATE_WINDOW)
            .order_by(ResearchRun.created_at.desc())
            .limit(1)
        )
        if duplicate is not None:
            return duplicate, True

        public_id = _new_public_id(now)
        for _ in range(5):
            if session.scalar(select(ResearchRun.id).where(ResearchRun.public_id == public_id)) is None:
                break
            public_id = _new_public_id(now)
        else:
            raise RuntimeError("Could not generate a unique public archive identifier.")
        record = ResearchRun(
            public_id=public_id,
            created_at=now,
            updated_at=now,
            payload_fingerprint=fingerprint,
            **data,
        )
        session.add(record)
        session.flush()
        return record, False


def list_research_runs() -> list[ResearchRun]:
    with database_session() as session:
        return list(session.scalars(select(ResearchRun).order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())))


def list_recent_research_runs(limit: int = 10) -> list[ResearchRun]:
    bounded_limit = max(0, min(int(limit), 10))
    with database_session() as session:
        return list(session.scalars(
            select(ResearchRun)
            .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
            .limit(bounded_limit)
        ))


def persist_daily_candidate_evaluation(
    ranked: list[dict[str, Any]], evaluated_at: datetime,
) -> None:
    """Atomically replace the current selection after all scoring is validated."""

    public_ids = [item["public_id"] for item in ranked]
    with database_session() as session:
        records = list(session.scalars(select(ResearchRun).where(ResearchRun.public_id.in_(public_ids))))
        by_public_id = {record.public_id: record for record in records}
        if len(by_public_id) != len(public_ids):
            raise RuntimeError("One or more evaluated archive records no longer exist.")
        session.execute(
            update(ResearchRun)
            .where(ResearchRun.daily_candidate_selected.is_(True))
            .values(daily_candidate_selected=False)
        )
        for item in ranked:
            record = by_public_id[item["public_id"]]
            record.daily_candidate_score = item["total_score"]
            record.daily_candidate_rank = item["rank"]
            record.daily_candidate_selected = item["rank"] == 1
            record.daily_candidate_evaluated_at = evaluated_at
            record.updated_at = evaluated_at


def get_current_daily_candidate() -> ResearchRun | None:
    with database_session() as session:
        return session.scalar(
            select(ResearchRun)
            .where(ResearchRun.daily_candidate_selected.is_(True))
            .order_by(ResearchRun.daily_candidate_evaluated_at.desc(), ResearchRun.id.desc())
            .limit(1)
        )


def list_current_daily_ranking() -> list[ResearchRun]:
    with database_session() as session:
        selected = session.scalar(
            select(ResearchRun)
            .where(ResearchRun.daily_candidate_selected.is_(True))
            .order_by(ResearchRun.daily_candidate_evaluated_at.desc(), ResearchRun.id.desc())
            .limit(1)
        )
        if selected is None or selected.daily_candidate_evaluated_at is None:
            return []
        return list(session.scalars(
            select(ResearchRun)
            .where(ResearchRun.daily_candidate_evaluated_at == selected.daily_candidate_evaluated_at)
            .order_by(ResearchRun.daily_candidate_rank.asc(), ResearchRun.id.desc())
        ))


def get_research_run(public_id: str) -> ResearchRun | None:
    with database_session() as session:
        return session.scalar(select(ResearchRun).where(ResearchRun.public_id == public_id))


configure_database()
