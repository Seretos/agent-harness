"""Live test against the real `claude` CLI. Deselected by default; run with
`python -m pytest -m live`."""
import shutil

import anyio
import pytest
from test_mcp_tools import _call, _poll_until_terminal, _run

pytestmark = pytest.mark.live


def test_live_start_agent_prompt_becomes_user_message(live_server_params, tmp_path):
    if shutil.which("claude") is None:
        pytest.skip("the real `claude` CLI is not on PATH")
    agents = tmp_path / "live-project" / ".claude" / "agents"
    agents.mkdir(parents=True)
    # The body only makes sense as instructions about the user message, so the output
    # differs depending on where `prompt` lands: user message -> "cba".
    (agents / "harness-echo.md").write_text(
        "---\nname: harness-echo\ndescription: Reverses the user message\nmodel: haiku\n---\n"
        "Reply with the user message reversed, character by character, and nothing else.\n",
        encoding="utf-8",
    )

    async def scenario(session):
        is_error, text, started = await _call(
            session,
            "harness_start_agent",
            agent="harness-echo",
            cwd=str(tmp_path / "live-project"),
            model="haiku",
            prompt="abc",
        )
        assert not is_error, text
        return await _poll_until_terminal(session, started["run_id"], budget=240.0)

    final = _run(scenario, live_server_params)
    assert final["state"] == "COMPLETED"
    assert "cba" in final["text"].lower()


def test_live_wait_run_timeout_keeps_run_alive(live_server_params):
    if shutil.which("claude") is None:
        pytest.skip("the real `claude` CLI is not on PATH")

    async def scenario(session):
        is_error, text, started = await _call(
            session,
            "harness_start_prompt",
            prompt="Count from 1 to 200, one number per line, then reply DONE.",
            model="haiku",
        )
        assert not is_error, text
        run_id = started["run_id"]
        waited = await _call(session, "harness_wait_run", run_id=run_id, timeout_seconds=3)
        final = await _poll_until_terminal(session, run_id, budget=240.0)
        return run_id, waited, final

    run_id, waited, final = _run(scenario, live_server_params)
    assert waited[0] is False, waited[1]
    if waited[2]["state"] == "RUNNING":
        assert run_id in waited[2]["next_step"]
        assert "harness wait" in waited[2]["next_step"]
        assert waited[2]["duration_s"] > 0
    assert waited[2]["state"] != "CANCELLED"
    assert final["state"] == "COMPLETED"
