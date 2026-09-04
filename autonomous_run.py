"""Short-lived CLI entry point for one bounded CyberSlooth expedition."""

from __future__ import annotations

import re
import sys
from typing import Any, Callable


run_autonomous_expedition: Callable[[], dict[str, Any]] | None = None


def _orchestrator() -> Callable[[], dict[str, Any]]:
    """Load the same Stage 1.0A orchestrator after CLI startup."""

    global run_autonomous_expedition
    if run_autonomous_expedition is None:
        from autonomy import run_autonomous_expedition as shared_orchestrator

        run_autonomous_expedition = shared_orchestrator
    return run_autonomous_expedition


def _safe_identifier(value: Any, prefix: str) -> str | None:
    if not isinstance(value, str):
        return None
    pattern = rf"{prefix}-\d{{8}}-[A-F0-9]{{6}}"
    return value if re.fullmatch(pattern, value) else None


def _safe_label(value: Any, fallback: str) -> str:
    if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,31}", value):
        return value
    return fallback


def _bounded_counter(value: Any, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(parsed, maximum))


def _print_failure(*, run_id: Any = None, failure_stage: Any = None, failure_code: Any = None) -> None:
    print("CyberSlooth autonomous run failed")
    safe_run_id = _safe_identifier(run_id, "AR")
    if safe_run_id:
        print(f"run_id={safe_run_id}")
    print(f"failure_stage={_safe_label(failure_stage, 'execution')}")
    print(f"failure_code={_safe_label(failure_code, 'run_failed')}")


def main() -> int:
    try:
        result = _orchestrator()()
    except Exception as exc:
        code = getattr(exc, "code", None)
        stage = getattr(exc, "failure_stage", None)
        if code == "run_blocked" and stage is None:
            stage = "idempotency"
        elif isinstance(exc, RuntimeError) and stage is None:
            stage = "configuration"
        _print_failure(
            run_id=getattr(exc, "public_run_id", None),
            failure_stage=stage,
            failure_code=code,
        )
        return 1

    if result.get("status") != "completed":
        _print_failure(
            run_id=result.get("public_run_id"),
            failure_stage=result.get("failure_stage"),
            failure_code="run_failed",
        )
        return 1

    print("CyberSlooth autonomous run completed")
    for label, prefix, key in (
        ("run_id", "AR", "public_run_id"),
        ("research_id", "CS", "research_public_id"),
        ("daily_discovery_id", "CS", "daily_discovery_public_id"),
    ):
        value = _safe_identifier(result.get(key), prefix)
        if value:
            print(f"{label}={value}")
    print(f"pages_retrieved={_bounded_counter(result.get('pages_retrieved'), 3)}")
    print(f"model_calls={_bounded_counter(result.get('model_calls_used'), 6)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
