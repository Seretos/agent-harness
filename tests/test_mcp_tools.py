"""MCP-level tests: a real `python -m harness_plugin` server over stdio,
talking to the fake claude CLI from tests/fixtures/fake_claude.py."""
import json
import time
from contextlib import asynccontextmanager

import anyio
from mcp import ClientSession
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "harness_list_agents",
    "harness_start_agent",
    "harness_start_prompt",
    "harness_poll_run",
    "harness_wait_run",
    "harness_stop_run",
    "harness_cleanup_run",
}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


@asynccontextmanager
async def _session(params):
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _run(scenario, params):
    async def main():
        with anyio.fail_after(60):
            async with _session(params) as session:
                return await scenario(session)

    return anyio.run(main)


async def _call(session, name, **arguments):
    """Returns (is_error, text, payload). payload is the JSON object the tool
    returned (None for errors)."""
    result = await session.call_tool(name, arguments)
    text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    payload = None
    if not result.isError:
        payload = result.structuredContent
        if payload is None:
            payload = json.loads(text)
    return bool(result.isError), text, payload


async def _poll_until_terminal(session, run_id, budget=20.0):
    deadline = time.monotonic() + budget
    while True:
        is_error, text, payload = await _call(session, "harness_poll_run", run_id=run_id)
        assert not is_error, text
        if payload["state"] in TERMINAL:
            return payload
        assert time.monotonic() < deadline, f"run never finished: {payload}"
        await anyio.sleep(0.2)


def test_tools_list_exposes_harness_tools_and_no_ping(server_params):
    async def scenario(session):
        return (await session.list_tools()).tools

    tools = _run(scenario, server_params)
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names
    assert "ping" not in names
    for t in tools:
        assert len((t.description or "").strip()) >= 20, f"{t.name} has no real description"
    wait_desc = next(t for t in tools if t.name == "harness_wait_run").description.lower()
    assert "cancel" in wait_desc, "wait description must warn that the deadline cancels the run"


def test_list_agents_returns_project_agent(server_params, project_dir):
    async def scenario(session):
        return await _call(session, "harness_list_agents", cwd=str(project_dir))

    is_error, text, payload = _run(scenario, server_params)
    assert not is_error, text
    by_name = {a["qualified_name"]: a for a in payload["agents"]}
    assert "demo" in by_name
    assert by_name["demo"]["source_scope"] == "project"
    assert by_name["demo"]["description"] == "Demo agent for tests"


def test_list_agents_empty_cwd_is_not_an_error(server_params, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    async def scenario(session):
        return await _call(session, "harness_list_agents", cwd=str(empty))

    is_error, text, payload = _run(scenario, server_params)
    assert not is_error, text
    assert payload["agents"] == []


def test_start_agent_completes_and_reports_result(server_params, project_dir):
    async def scenario(session):
        is_error, text, started = await _call(
            session, "harness_start_agent", agent="demo", cwd=str(project_dir), model="sonnet"
        )
        assert not is_error, text
        return started, await _poll_until_terminal(session, started["run_id"])

    started, final = _run(scenario, server_params)
    assert started["run_id"]
    assert final["state"] == "COMPLETED"
    assert final["text"] == "OK"
    assert final["session_id"]


def test_start_prompt_completes_then_cleanup_forgets_run(server_params):
    async def scenario(session):
        is_error, text, started = await _call(
            session, "harness_start_prompt", prompt="Say OK.", model="sonnet"
        )
        assert not is_error, text
        final = await _poll_until_terminal(session, started["run_id"])
        cleaned = await _call(session, "harness_cleanup_run", run_id=started["run_id"])
        after = await _call(session, "harness_poll_run", run_id=started["run_id"])
        return final, cleaned, after

    final, cleaned, after = _run(scenario, server_params)
    assert final["state"] == "COMPLETED"
    assert final["text"] == "OK"
    assert cleaned[0] is False, cleaned[1]
    assert after[0] is True
    assert "HarnessError" in after[1]


def test_wait_run_returns_completed_result(server_params, project_dir):
    async def scenario(session):
        is_error, text, started = await _call(
            session, "harness_start_agent", agent="demo", cwd=str(project_dir), model="sonnet"
        )
        assert not is_error, text
        return await _call(
            session, "harness_wait_run", run_id=started["run_id"], timeout_seconds=30
        )

    is_error, text, final = _run(scenario, server_params)
    assert not is_error, text
    assert final["state"] == "COMPLETED"
    assert final["text"] == "OK"
    assert final["session_id"]


def test_wait_run_deadline_cancels_and_server_stays_responsive(server_params, project_dir):
    async def scenario(session):
        is_error, text, started = await _call(
            session, "harness_start_prompt", prompt="SLEEP:30", model="sonnet"
        )
        assert not is_error, text
        waited = await _call(
            session, "harness_wait_run", run_id=started["run_id"], timeout_seconds=1
        )
        after = await _call(session, "harness_list_agents", cwd=str(project_dir))
        return waited, after

    waited, after = _run(scenario, server_params)
    assert waited[0] is False, waited[1]
    assert waited[2]["state"] == "CANCELLED"
    assert after[0] is False, after[1]


def test_stop_cancels_running_run_then_errors_on_finished_run(server_params, project_dir):
    async def scenario(session):
        is_error, text, started = await _call(
            session, "harness_start_prompt", prompt="SLEEP:30", model="sonnet"
        )
        assert not is_error, text
        run_id = started["run_id"]
        first = await _call(session, "harness_stop_run", run_id=run_id)
        second = await _call(session, "harness_stop_run", run_id=run_id)
        polled = await _call(session, "harness_poll_run", run_id=run_id)
        alive = await _call(session, "harness_list_agents", cwd=str(project_dir))
        return first, second, polled, alive

    first, second, polled, alive = _run(scenario, server_params)
    assert first[0] is False, first[1]
    assert first[2]["state"] == "CANCELLED"
    assert second[0] is True
    assert "IllegalTransitionError" in second[1]
    assert "illegal run state transition: CANCELLED -> CANCELLED" in second[1]
    assert polled[2]["state"] == "CANCELLED"
    assert alive[0] is False, alive[1]


def test_error_paths_return_structured_errors(server_params, project_dir, tmp_path):
    git_cwd = tmp_path / "gitrepo"
    (git_cwd / ".git").mkdir(parents=True)

    async def scenario(session):
        unknown_agent = await _call(
            session, "harness_start_agent", agent="nope", cwd=str(project_dir), model="sonnet"
        )
        unsafe = await _call(
            session,
            "harness_start_prompt",
            prompt="hi",
            model="sonnet",
            cwd=str(git_cwd),
        )
        unknown_run = await _call(session, "harness_poll_run", run_id="does-not-exist")
        alive = await _call(session, "harness_list_agents", cwd=str(project_dir))
        return unknown_agent, unsafe, unknown_run, alive

    unknown_agent, unsafe, unknown_run, alive = _run(scenario, server_params)
    assert unknown_agent[0] is True
    assert "nope" in unknown_agent[1]
    assert "demo" in unknown_agent[1]  # lists the known qualified_names
    assert unsafe[0] is True
    assert "UnsafeCwdError" in unsafe[1]
    assert unknown_run[0] is True
    assert "HarnessError" in unknown_run[1]
    assert alive[0] is False, alive[1]


def test_wait_run_does_not_block_other_tool_calls(server_params, project_dir):
    """A wait in flight must not wedge the server: another call has to be answered
    while the wait is still blocked (proves the thread offload)."""

    async def scenario(session):
        is_error, text, started = await _call(
            session, "harness_start_prompt", prompt="SLEEP:30", model="sonnet"
        )
        assert not is_error, text
        wait_done = anyio.Event()
        box = {}

        async def waiter():
            box["wait"] = await _call(
                session, "harness_wait_run", run_id=started["run_id"], timeout_seconds=4
            )
            wait_done.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(waiter)
            await anyio.sleep(0.5)
            listed = await _call(session, "harness_list_agents", cwd=str(project_dir))
            still_waiting = not wait_done.is_set()
        return listed, still_waiting, box["wait"]

    listed, still_waiting, waited = _run(scenario, server_params)
    assert listed[0] is False, listed[1]
    assert still_waiting, "list_agents only returned after the wait finished"
    assert waited[2]["state"] == "CANCELLED"
