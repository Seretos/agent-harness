"""The `wait-run` subcommand, run as a real second process against runs that a real
`python -m harness_plugin` MCP server (a different process) started.

Exit codes: 0 COMPLETED, 1 FAILED, 2 wait timeout (run left alone), 3 CANCELLED,
4 config/usage error. stdout is exactly one JSON object."""
import json
import subprocess

import anyio
from test_mcp_tools import _call, _run

CLI_TIMEOUT = 90


def _spawn(cmd, env, *args):
    return subprocess.Popen(
        [*cmd, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _finish(proc):
    out, err = proc.communicate(timeout=CLI_TIMEOUT)
    return proc.returncode, out, err


async def _start(session, prompt):
    is_error, text, started = await _call(
        session, "harness_start_prompt", prompt=prompt, model="sonnet"
    )
    assert not is_error, text
    return started["run_id"]


def _one_json(out):
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout must be exactly one JSON object, got {out!r}"
    return json.loads(lines[0])


def test_wait_run_blocks_until_completed_and_exits_zero(server_params, wait_run_env, wait_run_cmd):
    async def scenario(session):
        # No harness_poll_run between start and the CLI's exit: the CLI must be what sees the end.
        run_id = await _start(session, "SLEEP:3")
        proc = _spawn(wait_run_cmd, wait_run_env, "--run-id", run_id, "--timeout", "60")
        return await anyio.to_thread.run_sync(_finish, proc)

    code, out, err = _run(scenario, server_params)
    assert code == 0, (out, err)
    payload = _one_json(out)
    assert payload["state"] == "COMPLETED"
    assert payload["text"] == "OK"
    assert isinstance(payload["waited_s"], (int, float))
    assert payload["waited_s"] >= 2.5, "returned without waiting for the sleeping run"


def test_timeout_exits_two_and_leaves_run_running(server_params, wait_run_env, wait_run_cmd):
    async def scenario(session):
        run_id = await _start(session, "SLEEP:30")
        proc = _spawn(
            wait_run_cmd, wait_run_env, "--run-id", run_id, "--timeout", "2", "--interval", "0.5"
        )
        result = await anyio.to_thread.run_sync(_finish, proc)
        polled = await _call(session, "harness_poll_run", run_id=run_id)
        await _call(session, "harness_stop_run", run_id=run_id)
        return result, polled

    (code, out, err), polled = _run(scenario, server_params)
    assert code == 2, (out, err)
    assert _one_json(out)["state"] == "RUNNING"
    assert polled[0] is False, polled[1]
    assert polled[2]["state"] == "RUNNING", "the wait timeout must not cancel the run"


def test_failed_run_exits_one(server_params, wait_run_env, wait_run_cmd):
    async def scenario(session):
        run_id = await _start(session, "NO_RESULT")
        proc = _spawn(wait_run_cmd, wait_run_env, "--run-id", run_id, "--timeout", "60")
        return await anyio.to_thread.run_sync(_finish, proc)

    code, out, err = _run(scenario, server_params)
    assert code == 1, (out, err)
    assert _one_json(out)["state"] == "FAILED"


def test_cancelled_run_exits_three(server_params, wait_run_env, wait_run_cmd):
    async def scenario(session):
        run_id = await _start(session, "SLEEP:30")
        proc = _spawn(
            wait_run_cmd, wait_run_env, "--run-id", run_id, "--timeout", "60", "--interval", "0.5"
        )
        await anyio.sleep(2)
        is_error, text, _ = await _call(session, "harness_stop_run", run_id=run_id)
        assert not is_error, text
        return await anyio.to_thread.run_sync(_finish, proc)

    code, out, err = _run(scenario, server_params)
    assert code == 3, (out, err)
    assert _one_json(out)["state"] == "CANCELLED"


def test_unknown_run_id_exits_four(wait_run_env, wait_run_cmd):
    proc = _spawn(wait_run_cmd, wait_run_env, "--run-id", "no-such-run", "--timeout", "5")
    code, out, err = _finish(proc)
    assert code == 4, (out, err)
    assert out.strip() == ""
    assert "no-such-run" in err


def test_malformed_flag_exits_four_not_two(wait_run_env, wait_run_cmd):
    proc = _spawn(wait_run_cmd, wait_run_env, "--run-id", "x", "--timeout", "5", "--bogus")
    code, out, _ = _finish(proc)
    assert code == 4
    assert out.strip() == ""


def test_missing_timeout_exits_four(wait_run_env, wait_run_cmd):
    proc = _spawn(wait_run_cmd, wait_run_env, "--run-id", "x")
    code, out, _ = _finish(proc)
    assert code == 4
    assert out.strip() == ""
