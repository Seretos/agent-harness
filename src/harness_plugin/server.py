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
    RunState,
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
from harness_plugin.runs import artifacts_root, harness, inspect_run, run_to_dict, summary_to_dict

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
    prompt: str | None = None,
) -> dict[str, Any]:
    """Start a discovered subagent (by qualified_name) as a background run and return its
    run_id immediately; use harness_poll_run or harness_wait_run for the result. The run
    inherits the parent session's permission mode, model, effort and cwd (collected by the
    plugin hook); explicit arguments override them. Refuses when the session context cannot
    be determined. `context_source`, `cwd`, `permission_mode` and `model` are echoed back.
    An optional short `label` names the run and shows up in harness_list_runs. An optional
    `prompt` is the run's task (the user message); the agent definition's body stays the
    system prompt. Without `prompt` the run gets a default task."""
    if prompt is not None and not prompt.strip():
        raise HarnessError("prompt must not be empty")
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
    spec = resolve(definition, ctx, task=prompt)
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
def harness_send_message(run_id: str, prompt: str) -> dict[str, Any]:
    """Send a follow-up `prompt` to a run that has already finished (only finished runs;
    a RUNNING run is refused) and return immediately. It resumes the origin run's session
    and returns a new run: a new `run_id` (with `resumed_from` naming the origin run_id) in
    state RUNNING. The origin run's isolation is preserved: the follow-up runs with the same
    clean/agent flags and cwd as the origin. Afterwards use harness_wait_run or
    harness_poll_run on the new run_id to get the reply."""
    if not prompt.strip():
        raise HarnessError("prompt must not be empty")
    return run_to_dict(harness().start_resume(run_id, prompt), resumed_from=run_id)


@mcp.tool()
@_tool_errors
async def harness_wait_run(run_id: str, timeout_seconds: float = 300.0) -> dict[str, Any]:
    """Wait up to `timeout_seconds` (default 300) for the run to finish and return its
    result. The time limit ends only the waiting, never the run: nothing is cancelled. If
    it expires first the run is still RUNNING and the answer says so, with liveness fields
    (`duration_s`, `event_count`, `last_event_at`, `last_activity` = the last tool/command
    the run used) and a `next_step` hint. Only harness_stop_run cancels a run. To keep
    waiting past a tool call, run the shell command `harness wait <run_id>` in the
    background (blocks until the run ends; see the `harness-wait` skill), or call this
    tool again; harness_poll_run checks without waiting."""
    h = harness()
    result = await anyio.to_thread.run_sync(partial(h.wait, run_id, timeout_seconds))
    if result.state == RunState.RUNNING:
        return run_to_dict(
            result,
            next_step=(
                f"The run is still RUNNING; the timeout only ended the waiting and nothing "
                f"was cancelled. Keep waiting with `harness wait {run_id}` (run it via Bash "
                f"in the background) or call harness_wait_run again. Only harness_stop_run "
                f"cancels the run."
            ),
        )
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


@mcp.tool()
@_tool_errors
def harness_inspect_run(run_id: str) -> dict[str, Any]:
    """Answer, for one run, what it announced, what the harness asked for, and what
    system prompt was actually sent -- so a harness-vs-native discrepancy (e.g. a
    tool/skill/agent count mismatch) can be diagnosed without hand-reading a Claude
    transcript. `announced.{mcp_servers,tools,skills,agents}` are the name lists this
    run's own CLI process announced at startup, read from its events.jsonl init event
    (`init_event`, verbatim, or null if none was seen yet), with `announced_counts`
    alongside. `requested.{mcp_servers,tools,skills,agents}` are the same categories
    the harness itself put into this run, read back from its own recorded argv --
    populated even when the init event announced nothing, since it comes from a
    different artifact. `system_prompt` carries the exact text sent, with `source`
    naming which carrier supplied it (--system-prompt, --agents, or the materialized
    agent file), `chars`, `sha256`, and `recorded_sha256` (the run's own recorded
    digest) alongside. Works on a still-RUNNING run; errors on an unknown or
    cleaned-up run_id."""
    return inspect_run(run_id)


def main() -> None:
    warning = probe_warning()
    if warning:
        print(warning, file=sys.stderr, flush=True)  # stdout is the MCP transport
    mcp.run()
