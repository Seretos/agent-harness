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
