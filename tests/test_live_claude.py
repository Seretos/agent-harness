"""Live test against the real `claude` CLI. Deselected by default; run with
`python -m pytest -m live`."""
import os
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


def test_live_start_plugin_agent_colon_qualified(live_server_params, tmp_path):
    """R4: the colon-qualified plugin-agent start (R1/R2's fake-CLI mechanism) against
    the real `claude` CLI, with `tools:` proven as an enforced allowlist rather than
    just a JSON-payload field the CLI happens to ignore.

    Skipped unless a human has provisioned `HARNESS_LIVE_PLUGIN_CONFIG_DIR` to point at
    a real `CLAUDE_CONFIG_DIR` that holds:
      - `plugins/installed_plugins.json`, registering a `harness-live-fixture@<any>`
        plugin install;
      - that install's `agents/echo.md`, with frontmatter `model: haiku` and
        `tools: Read`, whose body asks the model to reply with the user message
        reversed, character by character, and nothing else;
      - `settings.json` with `enabledPlugins: {"harness-live-fixture@<any>": true}`;
      - real credentials (this dir's own `.credentials.json` / equivalent) -- the
        isolated `CLAUDE_CONFIG_DIR` `live_server_params` otherwise builds carries
        none, and this test writes no config dir of its own.

    Expected failure modes for a human running this provisioned, against a CLI that
    regresses: the real CLI rejects the `:` in the agent name (run ends FAILED, or
    never reaches "cba"), or the tools allowlist leaks (the non-`mcp__` entries of
    `announced["tools"]` grow beyond `{"Read"}`).
    """
    if shutil.which("claude") is None:
        pytest.skip("the real `claude` CLI is not on PATH")
    live_config_dir = os.environ.get("HARNESS_LIVE_PLUGIN_CONFIG_DIR")
    if not live_config_dir:
        pytest.skip("HARNESS_LIVE_PLUGIN_CONFIG_DIR is not set")

    env = dict(live_server_params.env)
    env["CLAUDE_CONFIG_DIR"] = live_config_dir
    params = live_server_params.model_copy(update={"env": env})

    async def scenario(session):
        listed = await _call(session, "harness_list_agents", cwd=str(tmp_path))
        is_error, text, started = await _call(
            session,
            "harness_start_agent",
            agent="harness-live-fixture:echo",
            cwd=str(tmp_path),
            model="haiku",
            prompt="abc",
        )
        assert not is_error, text
        final = await _poll_until_terminal(session, started["run_id"], budget=240.0)
        inspected = await _call(session, "harness_inspect_run", run_id=started["run_id"])
        return listed, final, inspected

    listed, final, inspected = _run(scenario, params)

    is_error, text, payload = listed
    assert not is_error, text
    by_name = {a["qualified_name"]: a for a in payload["agents"]}
    assert by_name["harness-live-fixture:echo"]["source_scope"] == "plugin"

    assert final["state"] == "COMPLETED"
    assert "cba" in final["text"].lower()

    is_error2, text2, inspected_payload = inspected
    assert not is_error2, text2
    assert inspected_payload["requested"]["tools"] == ["Read"]
    announced_tools = {
        name for name in inspected_payload["announced"]["tools"] if not name.startswith("mcp__")
    }
    assert announced_tools == {"Read"}, (
        "the tools allowlist must be enforced -- the default tool set must not leak"
    )


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
