"""The parent session's collected context: where the hook writes it, how the server finds it.

Deliberately free of `mcp`/`lib_python_harness` imports at module level so the per-tool-call
`hook` subcommand stays cheap."""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
PROJECT_ENV = "CLAUDE_PROJECT_DIR"
PLUGIN_DATA_ENV = "CLAUDE_PLUGIN_DATA"


def sessions_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get(PLUGIN_DATA_ENV)
    root = Path(base) if base else Path.home() / ".agent-harness"
    return root / "sessions"


def _safe_name(session_id: Any) -> str | None:
    if not isinstance(session_id, str) or not session_id:
        return None
    if session_id != Path(session_id).name or session_id in (".", ".."):
        return None
    return session_id


def write_session_context(payload: dict[str, Any], env: Mapping[str, str] | None = None) -> Path:
    """Atomically write `payload` to sessions/<session_id>.json (last write wins)."""
    name = _safe_name(payload.get("session_id"))
    if name is None:
        raise ValueError("payload has no usable session_id")
    directory = sessions_dir(env)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{name}.json"
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_session_context(
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Return (context, source): "session" (exact file for CLAUDE_CODE_SESSION_ID),
    "fallback" (newest file whose cwd/project_dir equals CLAUDE_PROJECT_DIR; mtime ties go
    to the lexicographically greatest filename) or "none"."""
    env = os.environ if env is None else env
    directory = sessions_dir(env)
    session_id = _safe_name(env.get(SESSION_ENV))
    if session_id:
        data = _read(directory / f"{session_id}.json")
        if data is not None:
            return data, "session"
    project = env.get(PROJECT_ENV)
    if project and directory.is_dir():
        best: tuple[float, str] | None = None
        best_data: dict[str, Any] | None = None
        for path in directory.glob("*.json"):
            data = _read(path)
            if data is None or project not in (data.get("cwd"), data.get("project_dir")):
                continue
            try:
                key = (path.stat().st_mtime, path.name)
            except OSError:
                continue
            if best is None or key > best:
                best, best_data = key, data
        if best_data is not None:
            return best_data, "fallback"
    return None, "none"


def probe_warning(env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    if env.get(SESSION_ENV):
        return None
    return (
        f"agent-harness: {SESSION_ENV} is not set; the parent session cannot be identified "
        f"exactly. Falling back to the newest context in {sessions_dir(env)} matching "
        f"{PROJECT_ENV}; harness_start_agent refuses if none resolves."
    )


def build_host_context(data: Mapping[str, Any], cwd: str, **overrides: str | None):
    """A completed lib HostContext from a session file; non-None overrides win."""
    from lib_python_harness import HostContext

    def pick(key: str) -> str | None:
        return overrides.get(key) or data.get(key)

    ctx = HostContext(
        cwd=cwd,
        session_id=data.get("session_id"),
        model=pick("model"),
        permission_mode=pick("permission_mode"),
        effort=pick("effort"),
    )
    ctx.complete()
    return ctx
