"""Manual CLI entry point for one bounded autonomous CyberSlooth expedition."""

from __future__ import annotations

import json
import sys

from autonomy import AutonomyError, run_autonomous_expedition


def main() -> int:
    try:
        result = run_autonomous_expedition()
    except AutonomyError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": exc.message}}))
        return 1
    print(json.dumps({"ok": True, "run": result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
