"""The `wait-run` subcommand, run as a real second process against runs that a real
`python -m harness_plugin` MCP server (a different process) started.

Exit codes: 0 COMPLETED, 1 FAILED, 2 wait timeout (run left alone), 3 CANCELLED,
4 config/usage error. stdout is exactly one JSON object."""
import json
import subprocess
import sys

import anyio
import pytest
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


def _finish_bounded(proc):
    """communicate(15); report a "blocked" sentinel instead of hanging if the CLI keeps waiting."""
    try:
        out, err = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return "blocked", out, err
    return proc.returncode, out, err


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
    assert 2.5 <= payload["waited_s"] < 60, "waited_s must be the measured wait, not the flag"


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
    timed_out = _one_json(out)
    assert timed_out["state"] == "RUNNING"
    assert timed_out["waited_s"] < 10, "waited_s must be the measured wait, not a constant"
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


# Upstream limit, not a harness bug: skipped on Windows only; exit 3 is covered on Linux.
@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "lib-python-harness v0.0.4 (re-verified, still failing 5/5 on Windows): Harness.stop() "
        "inside the stdio MCP server does not win the race on Windows, so wait_for "
        "finalises the run FAILED before CANCELLED is written; exit 3 is covered on Linux"
    ),
)
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


def test_missing_timeout_exits_four_even_for_a_live_run(server_params, wait_run_env, wait_run_cmd):
    """--timeout is REQUIRED. The run id is real and RUNNING, so an implementation that
    defaulted --timeout would block polling (subprocess timeout below) instead of exiting 4."""

    async def scenario(session):
        run_id = await _start(session, "SLEEP:30")
        proc = _spawn(wait_run_cmd, wait_run_env, "--run-id", run_id)
        result = await anyio.to_thread.run_sync(_finish_bounded, proc)
        await _call(session, "harness_stop_run", run_id=run_id)
        return result

    code, out, err = _run(scenario, server_params)
    assert code == 4, (code, out, err)
    assert out.strip() == ""
    assert "--timeout" in err, "usage error must name the missing --timeout flag"


def test_malformed_flag_exits_four_not_two(server_params, wait_run_env, wait_run_cmd):
    """Real RUNNING run id, so exit 4 cannot come from the unknown-run path. An implementation
    that swallowed --bogus (parse_known_args) would wait out --timeout 5 and exit 2."""

    async def scenario(session):
        run_id = await _start(session, "SLEEP:30")
        proc = _spawn(wait_run_cmd, wait_run_env, "--run-id", run_id, "--timeout", "5", "--bogus")
        result = await anyio.to_thread.run_sync(_finish_bounded, proc)
        await _call(session, "harness_stop_run", run_id=run_id)
        return result

    code, out, err = _run(scenario, server_params)
    assert code == 4, (code, out, err)
    assert out.strip() == ""
    assert "--bogus" in err, "usage error must name the unrecognised flag"
