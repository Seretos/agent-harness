"""Resolution of the parent session's collected context file, and the startup probe."""
import json
import os

import pytest


@pytest.fixture
def hc(monkeypatch, tmp_path):
    """The host_context module, with CLAUDE_PLUGIN_DATA pointed at tmp_path (both via
    os.environ and, where a function takes one, the explicit env mapping)."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    from harness_plugin import host_context

    return host_context


def _plant(tmp_path, name, mtime, **fields):
    sessions = tmp_path / "sessions"
    sessions.mkdir(exist_ok=True)
    path = sessions / name
    path.write_text(json.dumps(fields), encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _env(tmp_path, **extra):
    return {"CLAUDE_PLUGIN_DATA": str(tmp_path), **extra}


def test_resolution_prefers_session_id_then_newest_matching_project_dir(hc, tmp_path):
    _plant(tmp_path, "sess-a.json", 1000, session_id="sess-a", cwd="/proj/a")
    _plant(tmp_path, "sess-b.json", 2000, session_id="sess-b", project_dir="/proj/b")
    _plant(tmp_path, "sess-c.json", 3000, session_id="sess-c", cwd="/proj/b")
    # Lexicographically greatest but oldest: only an mtime-aware resolver skips it.
    _plant(tmp_path, "sess-z.json", 500, session_id="sess-z", cwd="/proj/b")

    exact, source = hc.load_session_context(
        _env(tmp_path, CLAUDE_CODE_SESSION_ID="sess-a", CLAUDE_PROJECT_DIR="/proj/b")
    )
    assert source == "session"
    assert exact["session_id"] == "sess-a"

    fallback, source = hc.load_session_context(_env(tmp_path, CLAUDE_PROJECT_DIR="/proj/b"))
    assert source == "fallback"
    assert fallback["session_id"] == "sess-c"  # newest by mtime of the matching /proj/b files (sess-z is older)

    nothing, source = hc.load_session_context(_env(tmp_path, CLAUDE_PROJECT_DIR="/proj/none"))
    assert (nothing, source) == (None, "none")

    nothing, source = hc.load_session_context(_env(tmp_path))
    assert (nothing, source) == (None, "none")


def test_corrupt_context_file_is_skipped_not_raised(hc, tmp_path):
    sessions = tmp_path / "sessions"
    _plant(tmp_path, "good.json", 1000, session_id="good", cwd="/proj/x")
    bad = sessions / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    os.utime(bad, (5000, 5000))  # newest, so it would win if not skipped

    data, source = hc.load_session_context(_env(tmp_path, CLAUDE_PROJECT_DIR="/proj/x"))
    assert source == "fallback"
    assert data["session_id"] == "good"


def test_equal_mtime_ties_pick_lexicographically_greatest_filename(hc, tmp_path):
    for name in ("s-1.json", "s-3.json", "s-2.json"):
        _plant(tmp_path, name, 1000, session_id=name[:-5], cwd="/proj/t")

    data, source = hc.load_session_context(_env(tmp_path, CLAUDE_PROJECT_DIR="/proj/t"))
    assert source == "fallback"
    assert data["session_id"] == "s-3"


def test_probe_warns_when_session_id_missing(hc):
    warning = hc.probe_warning({})
    assert warning and "CLAUDE_CODE_SESSION_ID" in warning
    assert hc.probe_warning({"CLAUDE_CODE_SESSION_ID": "abc"}) is None
