"""The `hook` subcommand: hook stdin -> <CLAUDE_PLUGIN_DATA>/sessions/<session_id>.json.

Run as a real subprocess (`python -m harness_plugin hook`), the way the plugin's
hooks.json runs the frozen binary."""
import json
import os
import subprocess
import sys

import pytest

PRE_TOOL_USE = {
    "session_id": "sess-abc",
    "transcript_path": "/tmp/transcripts/sess-abc.jsonl",
    "cwd": "/work/project/subdir",  # differs from CLAUDE_PROJECT_DIR on purpose
    "permission_mode": "acceptEdits",
    "effort": "high",
    "hook_event_name": "PreToolUse",
    "tool_name": "mcp__harness__harness_start_agent",
    "tool_input": {"agent": "demo"},
}


def run_hook(stdin_text, plugin_data, project_dir="/work/project"):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID")
    }
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
    env["CLAUDE_PROJECT_DIR"] = project_dir
    return subprocess.run(
        [sys.executable, "-m", "harness_plugin", "hook"],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_hook_writes_session_context(tmp_path):
    proc = run_hook(json.dumps(PRE_TOOL_USE), tmp_path)
    assert proc.returncode == 0, proc.stderr
    written = tmp_path / "sessions" / "sess-abc.json"
    assert written.is_file(), f"no session file; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert json.loads(written.read_text(encoding="utf-8")) == {
        "session_id": "sess-abc",
        "cwd": "/work/project/subdir",
        "project_dir": "/work/project",
        "permission_mode": "acceptEdits",
        "effort": "high",
        "transcript_path": "/tmp/transcripts/sess-abc.jsonl",
    }


def test_session_start_payload_without_permission_mode_is_written(tmp_path):
    payload = {
        "session_id": "sess-start",
        "cwd": "/work/project",
        "hook_event_name": "SessionStart",
        "source": "startup",
    }
    proc = run_hook(json.dumps(payload), tmp_path)
    assert proc.returncode == 0, proc.stderr
    data = json.loads((tmp_path / "sessions" / "sess-start.json").read_text(encoding="utf-8"))
    assert data["session_id"] == "sess-start"
    assert data["cwd"] == "/work/project"
    assert "permission_mode" not in data


@pytest.mark.parametrize(
    "stdin_text",
    ["{not json", "", json.dumps({"cwd": "/work/project", "permission_mode": "plan"})],
    ids=["malformed", "empty", "no-session-id"],
)
def test_malformed_stdin_exits_zero_without_writing(tmp_path, stdin_text):
    # Control: the hook exists and does write for a good payload.
    control = run_hook(json.dumps(PRE_TOOL_USE), tmp_path / "control")
    assert control.returncode == 0, control.stderr
    assert (tmp_path / "control" / "sessions" / "sess-abc.json").is_file()

    bad_data = tmp_path / "bad"
    proc = run_hook(stdin_text, bad_data)
    assert proc.returncode == 0, proc.stderr
    sessions = bad_data / "sessions"
    assert not sessions.exists() or list(sessions.iterdir()) == []


def test_unwritable_sessions_dir_exits_zero(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("occupied", encoding="utf-8")  # mkdir under a file must fail
    proc = run_hook(json.dumps(PRE_TOOL_USE), blocker)
    assert proc.returncode == 0, proc.stderr
