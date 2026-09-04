"""Bounded, manually triggered autonomous expedition orchestration."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import app as cyberslooth
from archive_store import (
    AutonomousRunConflict,
    autonomous_seed_last_used,
    complete_autonomous_run,
    create_autonomous_run,
    create_research_run,
    fail_autonomous_run,
    get_autonomous_run,
    list_recent_research_runs,
    persist_daily_candidate_evaluation,
    publish_daily_discovery,
    set_autonomous_run_seed,
)


SEED_POOL_PATH = Path(__file__).resolve().parent / "data" / "autonomy_seeds.json"
MAX_SEED_FILE_BYTES = 32 * 1024
MAX_STARTING_SEED_ATTEMPTS = 2
MAX_AUTONOMOUS_MODEL_CALLS = 6
RETRYABLE_SEED_RETRIEVAL_CODES = frozenset({
    "dns_failed", "source_status", "source_timeout", "source_unavailable", "retrieval_failed",
})


class AutonomyError(Exception):
    """A safe, expected autonomous-run failure."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def load_seed_pool(path: Path | None = None) -> list[dict[str, Any]]:
    """Load and validate the small application-owned seed pool without network work."""

    selected_path = path or SEED_POOL_PATH
    try:
        raw = selected_path.read_bytes()
    except OSError as exc:
        raise AutonomyError("seed_pool_unavailable", "The autonomous seed pool is unavailable.", 503) from exc
    if len(raw) > MAX_SEED_FILE_BYTES:
        raise AutonomyError("seed_pool_invalid", "The autonomous seed pool exceeds its size limit.", 500)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutonomyError("seed_pool_invalid", "The autonomous seed pool is invalid.", 500) from exc
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise AutonomyError("seed_pool_invalid", "The autonomous seed pool is invalid.", 500)

    seeds: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "url", "label", "category", "enabled"}:
            raise AutonomyError("seed_pool_invalid", "The autonomous seed pool is invalid.", 500)
        seed_id = item.get("id")
        if not isinstance(seed_id, str) or not seed_id.startswith("seed-") or len(seed_id) > 64 or seed_id in seen:
            raise AutonomyError("seed_pool_invalid", "The autonomous seed pool is invalid.", 500)
        if not isinstance(item.get("label"), str) or not item["label"].strip() or len(item["label"]) > 120:
            raise AutonomyError("seed_pool_invalid", "The autonomous seed pool is invalid.", 500)
        if not isinstance(item.get("category"), str) or not item["category"].strip() or len(item["category"]) > 80:
            raise AutonomyError("seed_pool_invalid", "The autonomous seed pool is invalid.", 500)
        if not isinstance(item.get("enabled"), bool):
            raise AutonomyError("seed_pool_invalid", "The autonomous seed pool is invalid.", 500)
        try:
            url = cyberslooth.validate_public_url(item.get("url"), resolve=False)
        except cyberslooth.IngestError as exc:
            raise AutonomyError("seed_pool_invalid", "The autonomous seed pool contains an invalid public URL.", 500) from exc
        seen.add(seed_id)
        seeds.append({**item, "url": url, "label": item["label"].strip(), "category": item["category"].strip()})
    return seeds


def select_seed(
    seeds: list[dict[str, Any]], *, excluded_ids: set[str] | None = None,
    excluded_urls: set[str] | None = None,
) -> dict[str, Any]:
    """Choose the least-recently-used enabled seed, then stable ID order."""

    blocked_ids = excluded_ids or set()
    blocked_urls = excluded_urls or set()
    enabled = sorted(
        (
            seed for seed in seeds
            if seed["enabled"] and seed["id"] not in blocked_ids and seed["url"] not in blocked_urls
        ),
        key=lambda seed: seed["id"],
    )
    if not enabled:
        raise AutonomyError("no_enabled_seeds", "No autonomous starting seeds are enabled.", 409)
    last_used = autonomous_seed_last_used([seed["id"] for seed in enabled])
    never_used = [seed for seed in enabled if seed["id"] not in last_used]
    if never_used:
        return never_used[0]
    return min(enabled, key=lambda seed: (last_used[seed["id"]], seed["id"]))


def public_run_view(run: Any) -> dict[str, Any]:
    """Return only sanitized autonomous-run metadata."""

    return {
        "public_run_id": run.public_run_id,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "status": run.status,
        "research_public_id": run.research_public_id,
        "daily_discovery_public_id": run.daily_discovery_public_id,
        "pages_retrieved": run.pages_retrieved,
        "model_calls_used": run.model_calls_used,
        "failure_stage": run.failure_stage,
        "failure_message": run.failure_message_safe,
    }


def _transition(logger: logging.Logger, public_run_id: str, stage: str, **metadata: Any) -> None:
    fields = " ".join(f"{key}={value}" for key, value in metadata.items() if value is not None)
    logger.info("autonomous_run=%s stage=%s%s", public_run_id, stage, f" {fields}" if fields else "")


def _retrieval_outcome(error: cyberslooth.IngestError | None) -> str:
    if error is None:
        return "success"
    if error.code == "source_status":
        for status in (403, 404, 410):
            if f"HTTP {status}" in error.message:
                return f"http_{status}"
        return "http_status"
    return {
        "dns_failed": "dns_failure",
        "source_timeout": "timeout",
        "source_unavailable": "unavailable",
        "retrieval_failed": "unavailable",
    }.get(error.code, "not_retryable")


def _log_seed_retrieval(
    logger: logging.Logger, public_run_id: str, attempt_number: int, seed_id: str,
    error: cyberslooth.IngestError | None,
) -> None:
    logger.info(
        "autonomous_run=%s seed_attempt=%s seed_id=%s retrieval_outcome=%s",
        public_run_id, attempt_number, seed_id, _retrieval_outcome(error),
    )


def run_autonomous_expedition(*, logger: logging.Logger | None = None) -> dict[str, Any]:
    """Execute one complete, bounded expedition after a manual authorized trigger."""

    log = logger or cyberslooth.app.logger
    try:
        run = create_autonomous_run()
    except AutonomousRunConflict as exc:
        raise AutonomyError("run_blocked", str(exc), 409) from exc

    public_run_id = run.public_run_id
    pages_retrieved = 0
    model_calls_used = 0
    research_public_id: str | None = None
    stage = "seed"
    _transition(log, public_run_id, "started")
    try:
        seeds = load_seed_pool()
        seed = select_seed(seeds)
        stage = "retrieval"
        attempted_ids: set[str] = set()
        attempted_urls: set[str] = set()
        fetched = None
        for attempt_number in range(1, MAX_STARTING_SEED_ATTEMPTS + 1):
            set_autonomous_run_seed(
                public_run_id, seed["id"], seed["url"], attempt_number=attempt_number,
            )
            attempted_ids.add(seed["id"])
            attempted_urls.add(seed["url"])
            try:
                fetched = cyberslooth.fetch_public_page(seed["url"])
            except cyberslooth.IngestError as exc:
                _log_seed_retrieval(log, public_run_id, attempt_number, seed["id"], exc)
                if exc.code not in RETRYABLE_SEED_RETRIEVAL_CODES or attempt_number >= MAX_STARTING_SEED_ATTEMPTS:
                    raise
                try:
                    seed = select_seed(
                        seeds, excluded_ids=attempted_ids, excluded_urls=attempted_urls,
                    )
                except AutonomyError:
                    raise exc
                continue
            _log_seed_retrieval(log, public_run_id, attempt_number, seed["id"], None)
            break
        if fetched is None:
            raise AutonomyError("retrieval_failed", "The starting source could not be retrieved.", 502)
        pages_retrieved = 1
        evidence = cyberslooth.build_research_evidence(fetched)

        stage = "analysis"
        initial_budget = cyberslooth.ModelCallBudget(maximum=1)
        try:
            analysis = cyberslooth.analyze_evidence(evidence, initial_budget)
        finally:
            model_calls_used += initial_budget.used
        _transition(log, public_run_id, "starting_page_analyzed", model_calls=model_calls_used)

        exploration = None
        if analysis["candidate_follow_ups"]:
            stage = "exploration"
            exploration_budget = cyberslooth.ModelCallBudget()
            try:
                exploration, exploration_status = cyberslooth.explore_evidence(
                    evidence, analysis, exploration_budget,
                )
            finally:
                model_calls_used += exploration_budget.used
            pages_retrieved += sum(
                1 for item in (exploration or {}).get("explored", [])
                if item.get("retrieval", {}).get("status") == "success"
            )
            if exploration_status != 200:
                failure = exploration.get("failure", {})
                raise AutonomyError(
                    failure.get("code", "exploration_failed"),
                    failure.get("message", "The bounded follow-up exploration could not be completed."),
                    exploration_status,
                )
            _transition(
                log, public_run_id, "exploration_completed",
                pages_retrieved=pages_retrieved, model_calls=model_calls_used,
            )

        if model_calls_used > MAX_AUTONOMOUS_MODEL_CALLS - 1:
            raise AutonomyError("model_call_budget", "The autonomous model-call budget was reached.", 503)

        stage = "archive"
        archive_payload = {"evidence": evidence, "analysis": analysis}
        if exploration is not None:
            archive_payload["exploration"] = exploration
        storage, fingerprint = cyberslooth.validate_archive_payload(archive_payload)
        archived, _duplicate = create_research_run(storage, fingerprint)
        research_public_id = archived.public_id
        _transition(log, public_run_id, "archived", research_public_id=research_public_id)

        stage = "scoring"
        candidates = list_recent_research_runs(cyberslooth.MAX_DAILY_CANDIDATES)
        if len(candidates) < 2:
            raise AutonomyError("insufficient_archive", "At least two archived records are required for daily scoring.", 409)
        model_calls_used += 1
        ranked, selection_reason = cyberslooth.score_daily_candidates(candidates)
        evaluated_at = datetime.now(timezone.utc)
        persist_daily_candidate_evaluation(ranked, evaluated_at)
        winner = ranked[0]
        _transition(log, public_run_id, "scored", model_calls=model_calls_used)

        stage = "publication"
        publish_daily_discovery(
            research_public_id=winner["public_id"],
            source_autonomous_run_id=public_run_id,
            selection_reason=selection_reason,
            selected_score=winner["total_score"],
            published_at=evaluated_at,
        )
        _transition(log, public_run_id, "published", daily_discovery_public_id=winner["public_id"])

        completed = complete_autonomous_run(
            public_run_id,
            research_public_id=research_public_id,
            daily_discovery_public_id=winner["public_id"],
            pages_retrieved=pages_retrieved,
            model_calls_used=model_calls_used,
        )
        _transition(
            log, public_run_id, "completed", pages_retrieved=pages_retrieved,
            model_calls=model_calls_used, research_public_id=research_public_id,
            daily_discovery_public_id=winner["public_id"],
        )
        return public_run_view(completed)
    except Exception as exc:
        if isinstance(exc, AutonomyError):
            safe_message = exc.message
            failure = exc
        elif isinstance(exc, (
            cyberslooth.IngestError, cyberslooth.AnalysisError, cyberslooth.ExplorationError,
            cyberslooth.ArchiveError, cyberslooth.DailySelectionError,
        )):
            safe_message = exc.message
            failure = AutonomyError(exc.code, exc.message, exc.status)
        elif isinstance(exc, AutonomousRunConflict):
            safe_message = str(exc)
            failure = AutonomyError("run_conflict", safe_message, 409)
        else:
            safe_message = "The autonomous expedition could not complete safely."
            failure = AutonomyError("autonomous_run_failed", safe_message, 503)
        try:
            failed = fail_autonomous_run(
                public_run_id,
                failure_stage=stage,
                failure_message_safe=safe_message,
                pages_retrieved=pages_retrieved,
                model_calls_used=min(model_calls_used, MAX_AUTONOMOUS_MODEL_CALLS),
                research_public_id=research_public_id,
            )
            failure.public_run_id = public_run_id
            failure.failure_stage = failed.failure_stage
            _transition(
                log, public_run_id, "failed", category=failure.code,
                pages_retrieved=failed.pages_retrieved, model_calls=failed.model_calls_used,
                research_public_id=research_public_id,
            )
        except Exception as persistence_exc:
            log.error("autonomous_run=%s stage=failure_persistence category=%s", public_run_id, type(persistence_exc).__name__)
        raise failure from exc


def get_public_run(public_run_id: str) -> dict[str, Any] | None:
    run = get_autonomous_run(public_run_id)
    return public_run_view(run) if run is not None else None
