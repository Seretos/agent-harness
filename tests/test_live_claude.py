"""Live test against the real `claude` CLI. Deselected by default; run with
`python -m pytest -m live`."""
import json
import os
import re
import shutil
import subprocess

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


# Hardcoded, independent of _ACCEPTED_VALUES: the minimum this repo already commits
# to elsewhere (test_mcp_tools.py's argv assertions, scripts/build.ps1, the ticket).
# An _ACCEPTED_VALUES that is empty, truncated, or has silently drifted away from
# what the app actually documents must fail this test on its own -- looping only
# over _ACCEPTED_VALUES itself can't detect that, since an empty list makes the loop
# body never run.
_MIN_PERMISSION_MODE = {
    "default", "acceptEdits", "auto", "bypassPermissions", "dontAsk", "manual", "plan",
}


def _help_section(help_text, flag_name):
    """The block of `claude --help` between this flag's own entry and the next
    flag's entry, so a token check can be scoped to the right flag's help instead
    of the whole document -- a token invented for, or only appearing under, an
    unrelated flag (e.g. a documented permission mode that is really the name of a
    different flag like --verbose) must not be able to satisfy the check by simply
    occurring somewhere else in the output."""
    pattern = re.compile(
        rf"^[ \t]*(?:-\w,\s*)?{re.escape(flag_name)}\b.*?"
        rf"(?=^[ \t]*(?:-\w,\s*)?-{{1,2}}[\w-]|\nCommands:|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(help_text)
    assert match, f"{flag_name} not found in `claude --help` output:\n{help_text}"
    return match.group(0)


def _help_choices(section):
    """The quoted tokens inside a `(choices: "a", "b", ...)` parenthetical in a
    `--help` section (e.g. --permission-mode's own block), with the surrounding
    quotes stripped. `_help_section` only slices the flag's own block of `--help`
    text; this turns that text into the actual list of tokens the CLI's own
    parser prints, as data, so the reverse check (every printed choice must be
    documented) has something to iterate instead of a bare substring test."""
    match = re.search(r"\(choices:\s*(.*?)\)", section, re.DOTALL)
    assert match, f"no '(choices: ...)' parenthetical found in section:\n{section}"
    return set(re.findall(r'"([^"]*)"', match.group(1)))


# Flag + real init-event field for a token whose category's --help choices
# don't list it (currently just permission_mode's "default" -- see server.py's
# _ACCEPTED_VALUES comment for why it stays documented anyway: a real run
# with `--permission-mode default` completes and its stream-json init event
# reports the value straight back). There is no generic way to know which
# init-event field mirrors an arbitrary flag, so this map only covers the
# category the fallback probe below is actually used for; extend it before
# relying on the fallback for a new category.
_LIVE_PROBE_FLAG = {"permission_mode": "--permission-mode"}
_LIVE_PROBE_INIT_FIELD = {"permission_mode": "permissionMode"}


def _probe_cli_accepts(category, token):
    """Real accept/reject probe against the live CLI for a `category` token
    that `--help`'s printed choices don't list -- proof of genuine
    acceptance instead of trusting `--help`'s incomplete text. Mirrors the
    manual probe that established "default" is genuinely accepted by
    permission_mode despite --help omitting it: runs a trivial real prompt
    with the token set and inspects the stream-json `init` event. Raises
    AssertionError (with the CLI's own error text/exit code, or the init
    event's mismatched field) on rejection."""
    assert category in _LIVE_PROBE_FLAG, (
        f"no live-probe flag/field mapping for category {category!r} -- add "
        f"one to _LIVE_PROBE_FLAG/_LIVE_PROBE_INIT_FIELD before relying on "
        f"the fallback probe for it"
    )
    flag = _LIVE_PROBE_FLAG[category]
    result = subprocess.run(
        [
            "claude", flag, token,
            "-p", "reply with just the word done",
            "--model", "haiku",
            "--output-format", "stream-json",
            "--verbose",
        ],
        capture_output=True, text=True, timeout=90,
    )
    init_event = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            init_event = event
            break
    assert result.returncode == 0 and init_event is not None, (
        f"claude {flag} {token!r} was rejected by the live CLI "
        f"(exit {result.returncode}): stderr={result.stderr!r} stdout={result.stdout!r}"
    )
    field = _LIVE_PROBE_INIT_FIELD[category]
    assert init_event.get(field) == token, (
        f"claude {flag} {token!r} ran but the init event reports "
        f"{field}={init_event.get(field)!r}, not {token!r}: {init_event}"
    )
    return init_event


def test_accepted_values_match_the_cli():
    """R4: the `permission_mode` value list this plugin documents in its tool
    schemas (server.py's _ACCEPTED_VALUES) matches the real CLI's, not invented.
    `effort` and `model` are no longer checked here -- their authority is the
    pinned lib_python_harness's own hard validator, checked offline against the
    lib's own constants by test_mcp_tools.py::test_documented_values_match_lib_
    validator, which needs no live CLI. permission_mode has no lib validator, so
    it stays checked against the live CLI, in both directions since --help's
    printed choices are a lower bound, not an upper bound ("default" is
    genuinely accepted but not printed -- see server.py's _ACCEPTED_VALUES
    comment):
    1. every choice --help prints under --permission-mode (_help_choices,
       parsed as data, not a substring test) must be documented -- and the
       parsed set must be non-empty, so an empty/failed parse can't vacuously
       pass this check;
    2. every documented token --help does *not* print must pass a real
       accept/reject probe against the live CLI (_probe_cli_accepts), instead
       of trusting --help's incomplete text.
    Expected RED before the change: AssertionError naming auto/dontAsk/manual
    as printed by --help but not (yet) in _ACCEPTED_VALUES['permission_mode']."""
    if shutil.which("claude") is None:
        pytest.skip("the real `claude` CLI is not on PATH")

    from harness_plugin.server import _ACCEPTED_VALUES

    help_text = subprocess.run(
        ["claude", "--help"], capture_output=True, text=True, timeout=30
    ).stdout

    documented_permission_mode = set(_ACCEPTED_VALUES["permission_mode"])

    assert _MIN_PERMISSION_MODE <= documented_permission_mode, (
        "_ACCEPTED_VALUES['permission_mode'] must cover at least "
        f"{_MIN_PERMISSION_MODE}, got {documented_permission_mode}"
    )

    permission_mode_section = _help_section(help_text, "--permission-mode")
    printed_permission_mode = _help_choices(permission_mode_section)
    assert printed_permission_mode, (
        f"--permission-mode help text parsed to no choices at all: "
        f"{permission_mode_section!r}"
    )

    undocumented = printed_permission_mode - documented_permission_mode
    assert not undocumented, (
        f"--permission-mode help prints {undocumented} that "
        f"_ACCEPTED_VALUES['permission_mode'] does not document"
    )

    for token in documented_permission_mode:
        if token in printed_permission_mode:
            continue
        # Not among --help's printed choices for --permission-mode (e.g.
        # "default") -- fall back to a real accept/reject probe against the
        # live CLI instead of failing on --help's incomplete text.
        _probe_cli_accepts("permission_mode", token)


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
