"""PreToolUse hook: block host-run dev tooling (AGENTS.md docker-only rule).

Rejects Bash commands that run pytest/ruff/mypy/pip install/uv sync|run|pip
directly on the host. Commands invoking docker are allowed through — that is
the sanctioned path for tests, lint, and package installs.
"""

import json
import re
import sys

TOOLING_PATTERNS = [
    (r"(^|[\s;&|(])pytest\b", "pytest"),
    (r"(^|[\s;&|(])ruff\b", "ruff"),
    (r"(^|[\s;&|(])mypy\b", "mypy"),
    (r"\bpip3?\s+install\b", "pip install"),
    (r"\buv\s+(sync|run|pip)\b", "uv sync/run/pip"),
    (r"\bpython3?\s+-m\s+(pytest|ruff|mypy|pip)\b", "python -m <tooling>"),
]

BLOCK_MESSAGE = (
    "BLOCKED by AGENTS.md docker-only rule: '{name}' must not run on the host.\n"
    "Run tests/lint/installs inside docker instead, e.g.:\n"
    '  docker run --rm -v "$PWD":/w -w /w python:3.11-slim '
    "sh -c 'pip install -q -e . pytest && pytest -q'\n"
    '  docker run --rm -v "$PWD":/w -w /w ghcr.io/astral-sh/ruff:latest check .\n'
    "(Sole exception: `uv tool install --editable .` for the szejo CLI itself.)"
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    command = payload.get("tool_input", {}).get("command", "")
    if not command:
        return 0
    # Anything routed through docker is the sanctioned path.
    if re.search(r"\bdocker\b", command):
        return 0
    # Strip quoted strings so commit messages etc. don't false-positive.
    stripped = re.sub(r"'[^']*'|\"[^\"]*\"", "", command)
    for pattern, name in TOOLING_PATTERNS:
        if re.search(pattern, stripped):
            print(BLOCK_MESSAGE.format(name=name), file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
