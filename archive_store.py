"""Small SQLAlchemy persistence layer for CyberSlooth Stage 0.5."""

from __future__ import annotations

import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, create_engine, select, text
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
    """Configure the single application engine and create the Stage 0.5 table."""

    global _engine, _session_factory
    selected_url = url or configured_database_url()
    previous_engine = _engine
    _engine = create_engine(selected_url, pool_pre_ping=True)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    if _engine.dialect.name == "sqlite":
        with _engine.begin() as connection:
            connection.execute(text("PRAGMA optimize"))
    if previous_engine is not None:
        previous_engine.dispose()
    return selected_url


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


def get_research_run(public_id: str) -> ResearchRun | None:
    with database_session() as session:
        return session.scalar(select(ResearchRun).where(ResearchRun.public_id == public_id))


configure_database()
