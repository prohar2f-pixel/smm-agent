"""Railway entrypoint that refuses unsafe configuration before bot startup."""

from __future__ import annotations

import os
import sys

from deployment.preflight import validate_environment


def main() -> int:
    result = validate_environment(dict(os.environ))
    if not result.ok:
        for error in result.errors:
            print(f"preflight: {error}", file=sys.stderr)
        return 2
    if result.mode == "health-only":
        print("content-factory preflight ok: health-only")
        return 0
    os.execv(sys.executable, [sys.executable, "main.py"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
