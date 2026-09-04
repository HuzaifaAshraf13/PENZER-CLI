"""Child-process runner for validated generated plugins."""

from __future__ import annotations

import importlib
import json
import resource
import sys


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    _limits()
    module = importlib.import_module(f"tools.plugins.{sys.argv[1]}")
    function = getattr(module, sys.argv[2])
    arguments = json.load(sys.stdin)
    try:
        result = function(**arguments)
        print(json.dumps({"ok": True, "result": str(result)}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())