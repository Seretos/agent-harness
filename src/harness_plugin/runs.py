"""Run helpers shared by the MCP server and the `wait-run` subcommand.

Deliberately free of mcp/FastMCP imports: the subcommand must start without them."""
from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path
from typing import Any

from lib_python_harness import FileRunStore, Harness

_HARNESS: Harness | None = None


def artifacts_root() -> Path:
    override = os.environ.get("HARNESS_ARTIFACTS_DIR")
    root = Path(override) if override else Path.home() / ".agent-harness" / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def claude_argv() -> list[str]:
    raw = os.environ.get("HARNESS_CLAUDE_ARGV")
    return [str(a) for a in json.loads(raw)] if raw else ["claude"]


def harness() -> Harness:
    """Lazy singleton: poll/wait/stop depend on the in-process Popen map."""
    global _HARNESS
    if _HARNESS is None:
        _HARNESS = Harness(store=FileRunStore(artifacts_root()), claude_argv=claude_argv())
    return _HARNESS


def jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Path):
        return str(value)
    return value


def run_to_dict(result: Any, **extra: Any) -> dict[str, Any]:
    out = {
        "run_id": result.run_id,
        "session_id": result.session_id,
        "state": jsonable(result.state),
        "text": result.text,
        "is_error": result.is_error,
        "subtype": result.subtype,
        "structured_output": result.structured_output,
        "usage": result.usage,
        "cost": result.cost,
        "transcript_path": jsonable(result.transcript_path),
        "duration_s": result.duration_s,
        "event_count": result.event_count,
        "last_event_at": result.last_event_at,
    }
    out.update(extra)
    return out


def summary_to_dict(summary: Any) -> dict[str, Any]:
    """Compact list row: identifying fields only, deliberately no text/usage."""
    return {
        "run_id": summary.run_id,
        "state": jsonable(summary.state),
        "model": summary.model,
        "cwd": jsonable(summary.cwd),
        "created_at": summary.created_at,
        "label": summary.label,
    }
