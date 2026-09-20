"""MCP-level tests: a real `python -m harness_plugin` server over stdio,
talking to the fake claude CLI from tests/fixtures/fake_claude.py."""
import json
import os
import time
from contextlib import asynccontextmanager

import anyio
import pytest
from conftest import SESSION_ID, plant_session_file
from mcp import ClientSession
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "harness_list_agents",
    "harness_start_agent",
    "harness_start_prompt",
    "harness_poll_run",
    "harness_list_runs",
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
    desc = {t.name: (t.description or "").lower() for t in tools}
    assert "run_id" in desc["harness_list_runs"], "list description must explain lost-run_id recovery"
    assert "lost" in desc["harness_list_runs"]
    assert "event_count" in desc["harness_poll_run"]
    assert "last_event_at" in desc["harness_poll_run"]
    assert "label" in desc["harness_start_agent"]
    assert "label" in desc["harness_start_prompt"]


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


# --- harness_list_runs + progress fields (#6) -------------------------------------

LIST_ROW_KEYS = {"run_id", "state", "model", "cwd", "created_at", "label"}


def test_list_runs_lists_runs_same_server_and_after_restart(server_params, project_dir):
    async def session1(session):
        e1, t1, agent = await _call(
            session,
            "harness_start_agent",
            agent="demo",
            cwd=str(project_dir),
            model="sonnet",
            label="agent-a",
        )
        assert not e1, t1
        e2, t2, prompt = await _call(
            session, "harness_start_prompt", prompt="Say OK.", model="sonnet", label="prompt-b"
        )
        assert not e2, t2
        e3, t3, sleeper = await _call(
            session, "harness_start_prompt", prompt="SLEEP:30", model="sonnet", label="sleeper-c"
        )
        assert not e3, t3
        await _poll_until_terminal(session, agent["run_id"])
        await _poll_until_terminal(session, prompt["run_id"])
        listed = await _call(session, "harness_list_runs")
        return agent["run_id"], prompt["run_id"], sleeper["run_id"], listed

    agent_id, prompt_id, sleeper_id, same = _run(session1, server_params)

    async def session2(session):
        listed = await _call(session, "harness_list_runs")
        await _call(session, "harness_stop_run", run_id=sleeper_id)
        return listed

    restarted = _run(session2, server_params)

    for is_error, text, payload in (same, restarted):
        assert not is_error, text
        rows = {r["run_id"]: r for r in payload["runs"]}
        assert {agent_id, prompt_id, sleeper_id} <= set(rows)
        for row in rows.values():
            assert set(row) == LIST_ROW_KEYS, row
            assert "text" not in row and "usage" not in row
        assert rows[agent_id]["label"] == "agent-a"
        assert rows[prompt_id]["label"] == "prompt-b"
        assert rows[sleeper_id]["label"] == "sleeper-c"
        assert rows[agent_id]["state"] == "COMPLETED"
        assert rows[prompt_id]["state"] == "COMPLETED"
        assert rows[sleeper_id]["state"] == "RUNNING"
        for row in rows.values():
            assert row["model"]
            assert row["cwd"]
            assert isinstance(row["created_at"], (int, float))


def test_list_runs_drops_cleaned_up_run(server_params):
    async def scenario(session):
        _, _, started = await _call(session, "harness_start_prompt", prompt="Say OK.", model="sonnet")
        await _poll_until_terminal(session, started["run_id"])
        await _call(session, "harness_cleanup_run", run_id=started["run_id"])
        return started["run_id"], await _call(session, "harness_list_runs")

    run_id, (is_error, text, payload) = _run(scenario, server_params)
    assert not is_error, text
    assert run_id not in {r["run_id"] for r in payload["runs"]}


def test_poll_reports_growing_progress_while_running(server_params):
    async def scenario(session):
        is_error, text, started = await _call(
            session, "harness_start_prompt", prompt="TICK:8:0.4", model="sonnet"
        )
        assert not is_error, text
        run_id = started["run_id"]
        await anyio.sleep(0.6)
        first = await _call(session, "harness_poll_run", run_id=run_id)
        await anyio.sleep(1.0)
        second = await _call(session, "harness_poll_run", run_id=run_id)
        await _call(session, "harness_stop_run", run_id=run_id)
        return first, second

    first, second = _run(scenario, server_params)
    assert first[0] is False, first[1]
    assert second[0] is False, second[1]
    a, b = first[2], second[2]
    assert a["state"] == "RUNNING" and b["state"] == "RUNNING"
    assert b["event_count"] > a["event_count"]
    assert b["last_event_at"] >= a["last_event_at"]


# --- parent-session HostContext wiring (#1) ---------------------------------------


def _argv_records(log):
    assert log.exists(), "fake claude was never invoked (no argv log)"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def _flag(argv, name):
    assert name in argv, f"{name} missing from CLI argv: {argv}"
    return argv[argv.index(name) + 1]


def _start_and_finish(params, **arguments):
    async def scenario(session):
        is_error, text, started = await _call(session, "harness_start_agent", **arguments)
        assert not is_error, text
        return started, await _poll_until_terminal(session, started["run_id"])

    return _run(scenario, params)


def test_start_agent_inherits_session_context(server_params, session_context, argv_log):
    started, final = _start_and_finish(server_params, agent="demo")
    assert final["state"] == "COMPLETED"
    assert started["context_source"] == "session"
    (record,) = _argv_records(argv_log)
    assert _flag(record["argv"], "--permission-mode") == "acceptEdits"
    assert _flag(record["argv"], "--effort") == "high"
    assert _flag(record["argv"], "--model") == "opus"
    assert os.path.samefile(record["cwd"], session_context["cwd"])
    assert os.path.samefile(started["cwd"], session_context["cwd"])


def test_start_agent_explicit_arguments_override_session_context(
    server_params, session_context, argv_log
):
    _start_and_finish(
        server_params, agent="demo", permission_mode="plan", model="sonnet", effort="low"
    )
    (record,) = _argv_records(argv_log)
    assert _flag(record["argv"], "--permission-mode") == "plan"
    assert _flag(record["argv"], "--effort") == "low"
    assert _flag(record["argv"], "--model") == "sonnet"


def test_start_agent_falls_back_to_newest_context_of_project_dir(
    server_params, session_context, argv_log
):
    env = dict(server_params.env)
    del env["CLAUDE_CODE_SESSION_ID"]
    env["CLAUDE_PROJECT_DIR"] = session_context["project_dir"]
    params = server_params.model_copy(update={"env": env})

    started, final = _start_and_finish(params, agent="demo")
    assert final["state"] == "COMPLETED"
    assert started["context_source"] == "fallback"
    (record,) = _argv_records(argv_log)
    assert _flag(record["argv"], "--permission-mode") == "acceptEdits"


@pytest.mark.parametrize("explicit_mode", [None, "acceptEdits"], ids=["no-args", "explicit-mode"])
def test_start_agent_refuses_without_session_context(
    server_params_no_context, project_dir, argv_log, explicit_mode
):
    args = {"agent": "demo", "cwd": str(project_dir), "model": "sonnet"}
    if explicit_mode:
        args["permission_mode"] = explicit_mode

    async def scenario(session):
        return await _call(session, "harness_start_agent", **args)

    is_error, text, _ = _run(scenario, server_params_no_context)
    assert is_error, "start_agent must refuse when no session context can be resolved"
    assert "CLAUDE_CODE_SESSION_ID" in text
    assert not argv_log.exists(), "a child was started on unconfirmed rights"


def test_start_agent_refuses_context_without_permission_mode(
    server_params, tmp_path, project_dir, argv_log
):
    # A SessionStart-only snapshot resolves, but carries no permission_mode.
    plant_session_file(tmp_path / "plugin-data", SESSION_ID, cwd=str(project_dir))

    async def scenario(session):
        return await _call(
            session, "harness_start_agent", agent="demo", cwd=str(project_dir), model="sonnet"
        )

    is_error, text, _ = _run(scenario, server_params)
    assert is_error, "a resolved context lacking permission_mode must not start a child"
    assert "permission_mode" in text
    assert not argv_log.exists()


def test_start_agent_explicit_permission_mode_overrides_file_lacking_one(
    server_params, tmp_path, project_dir, argv_log
):
    plant_session_file(tmp_path / "plugin-data", SESSION_ID, cwd=str(project_dir))
    started, final = _start_and_finish(
        server_params, agent="demo", cwd=str(project_dir), model="sonnet", permission_mode="plan"
    )
    assert final["state"] == "COMPLETED"
    (record,) = _argv_records(argv_log)
    assert _flag(record["argv"], "--permission-mode") == "plan"


def test_list_agents_and_start_prompt_work_without_context(server_params_no_context, project_dir):
    async def scenario(session):
        listed = await _call(session, "harness_list_agents", cwd=str(project_dir))
        prompt = await _call(session, "harness_start_prompt", prompt="Say OK.", model="sonnet")
        return listed, prompt

    listed, prompt = _run(scenario, server_params_no_context)
    assert listed[0] is False, listed[1]
    assert prompt[0] is False, prompt[1]


def test_probe_warning_goes_to_stderr_not_stdout(server_params_no_context, tmp_path):
    errlog_path = tmp_path / "server-stderr.log"

    async def main():
        with anyio.fail_after(60):
            with open(errlog_path, "w", encoding="utf-8") as errlog:
                async with stdio_client(server_params_no_context, errlog=errlog) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()  # stdout stayed valid JSON-RPC

    anyio.run(main)
    assert "CLAUDE_CODE_SESSION_ID" in errlog_path.read_text(encoding="utf-8")


def test_list_agents_uses_session_cwd_and_respects_disabled_plugins(
    server_params, session_context, project_dir, tmp_path
):
    config = tmp_path / "claude-config"
    installs = {}
    for name in ("alpha", "beta"):
        agents = tmp_path / "plugins" / name / "agents"
        agents.mkdir(parents=True)
        (agents / "helper.md").write_text(
            f"---\nname: helper\ndescription: {name} helper\n---\nDo it.\n",
            encoding="utf-8",
        )
        installs[f"{name}@mk"] = [
            {"scope": "user", "installPath": str(tmp_path / "plugins" / name)}
        ]
    (config / "plugins").mkdir()
    (config / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": installs}), encoding="utf-8"
    )
    (config / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"alpha@mk": True, "beta@mk": True}}), encoding="utf-8"
    )
    (project_dir / ".claude" / "settings.local.json").write_text(
        json.dumps({"enabledPlugins": {"beta@mk": False}}), encoding="utf-8"
    )

    (tmp_path / "elsewhere").mkdir()

    async def scenario(session):
        implicit = await _call(session, "harness_list_agents")
        explicit = await _call(session, "harness_list_agents", cwd=str(tmp_path / "elsewhere"))
        return implicit, explicit

    implicit, explicit = _run(scenario, server_params)
    assert implicit[0] is False, implicit[1]
    by_name = {a["qualified_name"]: a for a in implicit[2]["agents"]}
    assert os.path.samefile(implicit[2]["cwd"], session_context["cwd"])
    assert by_name["demo"]["source_scope"] == "project"
    assert by_name["alpha:helper"]["source_scope"] == "plugin"
    assert "beta:helper" not in by_name
    assert explicit[0] is False, explicit[1]
    assert os.path.samefile(explicit[2]["cwd"], tmp_path / "elsewhere")
