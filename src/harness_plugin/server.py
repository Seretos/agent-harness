"""MCP tool surface over lib_python_harness: one tool per public lib entry point."""
from __future__ import annotations

import functools
import inspect
import os
import sys
from functools import partial
from typing import Any

import anyio.to_thread
from lib_python_harness import (
    HarnessError,
    HostContext,
    Isolation,
    RunSpec,
    discover,
    resolve,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from harness_plugin.host_context import (
    build_host_context,
    load_session_context,
    probe_warning,
    sessions_dir,
)
from harness_plugin.runs import artifacts_root, harness, run_to_dict, summary_to_dict

mcp = FastMCP("harness")

def _tool_errors(fn):
    """Re-raise lib errors as ToolError carrying the exception class name."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except HarnessError as exc:
                raise ToolError(f"{type(exc).__name__}: {exc}") from exc

    else:

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except HarnessError as exc:
                raise ToolError(f"{type(exc).__name__}: {exc}") from exc

    return wrapper


@mcp.tool()
@_tool_errors
def harness_list_agents(cwd: str | None = None) -> dict[str, Any]:
    """List the subagent definitions (project, user and plugin scope) visible from `cwd`
    (default: the parent session's cwd, else CLAUDE_PROJECT_DIR, else the server's working
    directory). The used cwd and the `context_source` are echoed back."""
    data, source = load_session_context()
    used = (
        cwd
        or (data or {}).get("cwd")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.getcwd()
    )
    found = discover(HostContext(cwd=used))
    return {
        "cwd": used,
        "context_source": source,
        "agents": [
            {
                "qualified_name": name,
                "name": d.name,
                "description": d.description,
                "source_scope": d.source_scope,
                "path": str(d.path),
                "model": d.model,
            }
            for name, d in found.items()
        ],
    }


@mcp.tool()
@_tool_errors
def harness_start_agent(
    agent: str,
    cwd: str | None = None,
    model: str | None = None,
    permission_mode: str | None = None,
    effort: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Start a discovered subagent (by qualified_name) as a background run and return its
    run_id immediately; use harness_poll_run or harness_wait_run for the result. The run
    inherits the parent session's permission mode, model, effort and cwd (collected by the
    plugin hook); explicit arguments override them. Refuses when the session context cannot
    be determined. `context_source`, `cwd`, `permission_mode` and `model` are echoed back.
    An optional short `label` names the run and shows up in harness_list_runs."""
    data, source = load_session_context()
    if data is None:
        raise ToolError(
            "cannot determine the parent session's context: CLAUDE_CODE_SESSION_ID is not "
            "set or no matching session file exists in the sessions dir "
            f"({sessions_dir()}); refusing to start a child on unconfirmed rights"
        )
    # Refusal rules: no session file at all (above) refuses unconditionally, even with an
    # explicit permission_mode. A SessionStart-only snapshot refuses only when the EFFECTIVE
    # mode (explicit argument, else file value) is missing. No cwd => cannot resolve the agent.
    if not (permission_mode or data.get("permission_mode")):
        raise ToolError(
            "the parent session's context carries no permission_mode yet (only a "
            "SessionStart snapshot exists); refusing to start a child on unconfirmed rights"
        )
    used = cwd or data.get("cwd") or data.get("project_dir")
    if not used:
        raise ToolError("the parent session's context carries no cwd; pass `cwd`")
    ctx = build_host_context(
        data, used, model=model, permission_mode=permission_mode, effort=effort
    )
    definitions = discover(ctx)
    definition = definitions.get(agent)
    if definition is None:
        known = ", ".join(sorted(definitions)) or "(none)"
        raise ToolError(f"unknown agent {agent!r}; known agents: {known}")
    spec = resolve(definition, ctx)
    if model:
        spec.model = model
    if spec.model is None:
        raise ToolError(
            f"no model for agent {agent!r}: pass `model` or set `model:` in its definition"
        )
    spec.cwd = used
    spec.label = label
    spec.artifacts_dir = artifacts_root()
    return run_to_dict(
        harness().start(spec),
        cwd=used,
        context_source=source,
        permission_mode=ctx.permission_mode,
        model=spec.model,
    )


@mcp.tool()
@_tool_errors
def harness_start_prompt(
    prompt: str,
    model: str,
    effort: str | None = None,
    system_prompt: str | None = None,
    cwd: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Start an ad-hoc prompt in a clean (no memory, no project config) run and return its
    run_id immediately. With `cwd` unset the run gets a fresh empty temp directory; a given
    `cwd` must exist, be empty and not sit inside a git repository. An optional short `label` names the run and shows up in
    harness_list_runs."""
    spec = RunSpec(
        prompt=prompt,
        isolation=Isolation.CLEAN,
        model=model,
        effort=effort,
        system_prompt=system_prompt,
        cwd=cwd,
        label=label,
        artifacts_dir=artifacts_root(),
    )
    return run_to_dict(harness().start(spec))


@mcp.tool()
@_tool_errors
def harness_poll_run(run_id: str) -> dict[str, Any]:
    """Return the current state (and result, once finished) of a run without blocking.
    While the run is RUNNING, `event_count` and `last_event_at` show progress: they only
    advance while `state` is RUNNING, so a growing count means the run is working and a
    frozen one means it may be hung; once the run is terminal they reset to 0 and None."""
    return run_to_dict(harness().poll(run_id))


@mcp.tool()
@_tool_errors
def harness_list_runs() -> dict[str, Any]:
    """List all recorded runs as compact rows (run_id, state, model, cwd, created_at, label).
    Use it when you lost the run_id (e.g. after context compaction) to find a run again,
    then pass it to harness_stop_run, harness_poll_run or harness_cleanup_run. Rows
    deliberately carry no result text or usage; poll a run for those."""
    return {"runs": [summary_to_dict(s) for s in harness().list_runs()]}


@mcp.tool()
@_tool_errors
async def harness_wait_run(run_id: str, timeout_seconds: float = 300.0) -> dict[str, Any]:
    """Block until the run finishes and return its result. WARNING: if `timeout_seconds`
    (default 300) expires first, the deadline cancels the run (terminal state CANCELLED)
    rather than just giving up waiting; use harness_poll_run to check without cancelling.
    For a run that may outlast a tool call, do not block here: run the shell command
    `harness wait-run --run-id <id> --timeout <s>` in the background instead (see the
    `harness-wait` skill); its timeout never cancels the run."""
    result = await anyio.to_thread.run_sync(partial(harness().wait, run_id, timeout_seconds))
    return run_to_dict(result)


@mcp.tool()
@_tool_errors
def harness_stop_run(run_id: str) -> dict[str, Any]:
    """Stop a RUNNING run (terminal state CANCELLED). Errors on an already finished run."""
    return run_to_dict(harness().stop(run_id))


@mcp.tool()
@_tool_errors
def harness_cleanup_run(run_id: str) -> dict[str, Any]:
    """Forget a run's record. Artifacts on disk are kept; the run cannot be polled afterwards."""
    harness().cleanup(run_id)
    return {"run_id": run_id, "cleaned": True}


def main() -> None:
    warning = probe_warning()
    if warning:
        print(warning, file=sys.stderr, flush=True)  # stdout is the MCP transport
    mcp.run()
