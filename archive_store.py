"""Small SQLAlchemy persistence layer for CyberSlooth Stage 1.0A."""

from __future__ import annotations

import os
import secrets
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import JSON, Boolean, Date, DateTime, Index, Integer, String, create_engine, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
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


class AutonomousRun(Base):
    __tablename__ = "autonomous_runs"
    __table_args__ = (
        Index("idx_autonomous_runs_status_started", "status", "started_at"),
        Index("uq_autonomous_runs_active_guard", "active_guard", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_run_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    initial_seed_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seed_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seed_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    seed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    research_public_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    daily_discovery_public_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pages_retrieved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_message_safe: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active_guard: Mapped[str | None] = mapped_column(String(16), nullable=True)


class DailyDiscovery(Base):
    __tablename__ = "daily_discoveries"
    __table_args__ = (Index("idx_daily_discoveries_published", "published_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publication_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    research_run_public_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_autonomous_run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_reason: Mapped[str] = mapped_column(String(400), nullable=False)
    selected_score: Mapped[int] = mapped_column(Integer, nullable=False)


class AutonomousRunConflict(RuntimeError):
    """A completed daily run or active run prevents a new autonomous run."""


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
    """Configure the engine and create/upgrade the bounded prototype schema."""

    global _engine, _session_factory
    selected_url = url or configured_database_url()
    previous_engine = _engine
    _engine = create_engine(selected_url, pool_pre_ping=True)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    _ensure_stage_06_columns()
    _ensure_stage_10a_reliability_columns()
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


def _ensure_stage_10a_reliability_columns() -> None:
    """Add the bounded seed-attempt metadata to an existing Stage 1.0A table."""

    if _engine is None:
        raise RuntimeError("The archive database is not initialized.")
    existing = {column["name"] for column in inspect(_engine).get_columns("autonomous_runs")}
    with _engine.begin() as connection:
        if "initial_seed_id" not in existing:
            connection.execute(text("ALTER TABLE autonomous_runs ADD COLUMN initial_seed_id VARCHAR(64)"))
        if "seed_attempts" not in existing:
            connection.execute(text(
                "ALTER TABLE autonomous_runs ADD COLUMN seed_attempts INTEGER NOT NULL DEFAULT 0"
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


def _new_autonomous_public_id(now: datetime) -> str:
    return f"AR-{now:%Y%m%d}-{secrets.token_hex(3).upper()}"


def create_autonomous_run(now: datetime | None = None) -> AutonomousRun:
    """Create the single active run, rejecting overlap and a completed UTC day."""

    started_at = now or datetime.now(timezone.utc)
    day_start = datetime.combine(started_at.date(), time.min, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    try:
        with database_session() as session:
            if session.scalar(select(AutonomousRun.id).where(AutonomousRun.active_guard == "active")) is not None:
                raise AutonomousRunConflict("An autonomous expedition is already running.")
            if session.scalar(
                select(AutonomousRun.id)
                .where(AutonomousRun.status == "completed")
                .where(AutonomousRun.completed_at >= day_start)
                .where(AutonomousRun.completed_at < day_end)
                .limit(1)
            ) is not None:
                raise AutonomousRunConflict("A completed autonomous expedition already exists for this UTC date.")
            public_run_id = _new_autonomous_public_id(started_at)
            for _ in range(5):
                if session.scalar(select(AutonomousRun.id).where(AutonomousRun.public_run_id == public_run_id)) is None:
                    break
                public_run_id = _new_autonomous_public_id(started_at)
            else:
                raise RuntimeError("Could not generate a unique autonomous run identifier.")
            run = AutonomousRun(
                public_run_id=public_run_id,
                started_at=started_at,
                completed_at=None,
                status="running",
                pages_retrieved=0,
                model_calls_used=0,
                created_at=started_at,
                active_guard="active",
            )
            session.add(run)
            session.flush()
            return run
    except IntegrityError as exc:
        raise AutonomousRunConflict("An autonomous expedition is already running.") from exc


def set_autonomous_run_seed(
    public_run_id: str, seed_id: str, seed_url: str, *, attempt_number: int = 1,
) -> None:
    """Record the initial seed, current/final seed, and bounded attempt count."""

    if attempt_number not in (1, 2):
        raise ValueError("An autonomous run permits only one or two seed attempts.")
    with database_session() as session:
        run = session.scalar(select(AutonomousRun).where(AutonomousRun.public_run_id == public_run_id))
        if run is None or run.status != "running":
            raise RuntimeError("The autonomous run is not active.")
        if attempt_number != run.seed_attempts + 1:
            raise RuntimeError("The autonomous seed attempt sequence is invalid.")
        if run.initial_seed_id is None:
            run.initial_seed_id = seed_id
        run.seed_id = seed_id
        run.seed_url = seed_url
        run.seed_attempts = attempt_number


def autonomous_seed_last_used(seed_ids: list[str]) -> dict[str, datetime]:
    if not seed_ids:
        return {}
    with database_session() as session:
        final_rows = session.execute(
            select(AutonomousRun.seed_id, func.max(AutonomousRun.started_at))
            .where(AutonomousRun.seed_id.in_(seed_ids))
            .group_by(AutonomousRun.seed_id)
        ).all()
        initial_rows = session.execute(
            select(AutonomousRun.initial_seed_id, func.max(AutonomousRun.started_at))
            .where(AutonomousRun.initial_seed_id.in_(seed_ids))
            .group_by(AutonomousRun.initial_seed_id)
        ).all()
        last_used: dict[str, datetime] = {}
        for seed_id, used_at in (*final_rows, *initial_rows):
            if seed_id is not None and (seed_id not in last_used or used_at > last_used[seed_id]):
                last_used[seed_id] = used_at
        return last_used


def complete_autonomous_run(
    public_run_id: str, *, research_public_id: str, daily_discovery_public_id: str,
    pages_retrieved: int, model_calls_used: int, completed_at: datetime | None = None,
) -> AutonomousRun:
    finished = completed_at or datetime.now(timezone.utc)
    with database_session() as session:
        run = session.scalar(select(AutonomousRun).where(AutonomousRun.public_run_id == public_run_id))
        if run is None or run.status != "running":
            raise RuntimeError("The autonomous run is not active.")
        run.status = "completed"
        run.completed_at = finished
        run.research_public_id = research_public_id
        run.daily_discovery_public_id = daily_discovery_public_id
        run.pages_retrieved = pages_retrieved
        run.model_calls_used = model_calls_used
        run.active_guard = None
        session.flush()
        return run


def fail_autonomous_run(
    public_run_id: str, *, failure_stage: str, failure_message_safe: str,
    pages_retrieved: int, model_calls_used: int, research_public_id: str | None = None,
    completed_at: datetime | None = None,
) -> AutonomousRun:
    finished = completed_at or datetime.now(timezone.utc)
    with database_session() as session:
        run = session.scalar(select(AutonomousRun).where(AutonomousRun.public_run_id == public_run_id))
        if run is None:
            raise RuntimeError("The autonomous run does not exist.")
        run.status = "failed"
        run.completed_at = finished
        run.research_public_id = research_public_id
        run.pages_retrieved = pages_retrieved
        run.model_calls_used = model_calls_used
        run.failure_stage = failure_stage[:32]
        run.failure_message_safe = failure_message_safe[:300]
        run.active_guard = None
        session.flush()
        return run


def get_autonomous_run(public_run_id: str) -> AutonomousRun | None:
    with database_session() as session:
        return session.scalar(select(AutonomousRun).where(AutonomousRun.public_run_id == public_run_id))


def get_latest_autonomous_run() -> AutonomousRun | None:
    with database_session() as session:
        return session.scalar(select(AutonomousRun).order_by(AutonomousRun.started_at.desc(), AutonomousRun.id.desc()).limit(1))


def publish_daily_discovery(
    *, research_public_id: str, source_autonomous_run_id: str, selection_reason: str,
    selected_score: int, published_at: datetime | None = None,
) -> DailyDiscovery:
    now = published_at or datetime.now(timezone.utc)
    try:
        with database_session() as session:
            if session.scalar(select(ResearchRun.id).where(ResearchRun.public_id == research_public_id)) is None:
                raise RuntimeError("The selected research record does not exist.")
            discovery = DailyDiscovery(
                publication_date=now.date(),
                research_run_public_id=research_public_id,
                published_at=now,
                source_autonomous_run_id=source_autonomous_run_id,
                selection_reason=selection_reason[:400],
                selected_score=selected_score,
            )
            session.add(discovery)
            session.flush()
            return discovery
    except IntegrityError as exc:
        raise AutonomousRunConflict("A daily discovery already exists for this UTC date.") from exc


def get_current_daily_discovery() -> DailyDiscovery | None:
    with database_session() as session:
        return session.scalar(
            select(DailyDiscovery)
            .order_by(DailyDiscovery.publication_date.desc(), DailyDiscovery.published_at.desc())
            .limit(1)
        )


configure_database()
