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


# --- parent_mcp_servers / build_host_context (#38 R2) ------------------------------


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_plugin_manifest(install_dir, name, mcp_servers):
    _write_json(
        install_dir / ".claude-plugin" / "plugin.json",
        {"name": name, "mcpServers": mcp_servers},
    )


def _register_plugin(config_dir, key, install_dir, *, enabled=True):
    """Read-merge-write into config_dir's installed_plugins.json/settings.json --
    same read-merge-write pattern test_mcp_tools.py's
    test_list_agents_uses_session_cwd_and_respects_disabled_plugins already uses to
    add a second plugin registration on top of one a fixture already wrote."""
    installed_path = config_dir / "plugins" / "installed_plugins.json"
    installed = (
        json.loads(installed_path.read_text()) if installed_path.exists() else {"version": 2, "plugins": {}}
    )
    installed.setdefault("plugins", {})[key] = [{"scope": "user", "installPath": str(install_dir)}]
    _write_json(installed_path, installed)
    settings_path = config_dir / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    settings.setdefault("enabledPlugins", {})[key] = enabled
    _write_json(settings_path, settings)


def test_parent_mcp_servers_rebuilds_all_sources(monkeypatch, tmp_path):
    """R2: parent_mcp_servers(cwd) rebuilds the parent's active MCP-server set from
    <CLAUDE_CONFIG_DIR>/.claude.json (user scope at the top level, local scope at
    projects[cwd].mcpServers, projects[cwd].disabledMcpServers dropped from the
    merged result), the project's own .mcp.json (approved via the project's
    .claude/settings.json enableAllProjectMcpServers), and enabled plugins' own
    manifests -- keyed plugin_<plugin>_<server>, with placeholders expanded and
    each server's env carrying that plugin's own CLAUDE_PLUGIN_ROOT/
    CLAUDE_PLUGIN_DATA -- except the agent-harness plugin's own `harness` server,
    which keeps the literal bare key `harness` (AC1). A disabled plugin (off@mk)
    contributes nothing.

    Expected RED reason: AttributeError -- parent_mcp_servers does not exist yet
    on host_context.
    """
    config_dir = tmp_path / "claude-config"
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    fixture_dir = tmp_path / "plugins" / "fixture"
    harness_dir = tmp_path / "plugins" / "agent-harness"
    off_dir = tmp_path / "plugins" / "off"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    _write_json(
        config_dir / ".claude.json",
        {
            "mcpServers": {
                "user-srv": {"command": "user-cmd"},
                "user-disabled-srv": {"command": "disabled-cmd"},
            },
            "projects": {
                str(project_dir): {
                    "mcpServers": {"local-srv": {"command": "local-cmd"}},
                    "disabledMcpServers": ["user-disabled-srv"],
                    "hasTrustDialogAccepted": True,
                }
            },
        },
    )
    _write_json(
        project_dir / ".mcp.json",
        {"mcpServers": {"proj-srv": {"command": "proj-cmd"}}},
    )
    _write_json(
        project_dir / ".claude" / "settings.json",
        {"enableAllProjectMcpServers": True},
    )
    _write_plugin_manifest(
        fixture_dir,
        "fixture",
        {
            "fsrv": {
                "command": "${CLAUDE_PLUGIN_ROOT}/bin/fsrv",
                "env": {
                    "ROOT_MARKER": "${CLAUDE_PLUGIN_ROOT}",
                    "DATA_MARKER": "${CLAUDE_PLUGIN_DATA}",
                },
            }
        },
    )
    _write_plugin_manifest(
        harness_dir, "agent-harness", {"harness": {"command": "${CLAUDE_PLUGIN_ROOT}/bin/harness", "args": []}}
    )
    _write_plugin_manifest(off_dir, "off", {"offsrv": {"command": "off-cmd"}})
    _register_plugin(config_dir, "fixture@mk", fixture_dir)
    _register_plugin(config_dir, "agent-harness@mk", harness_dir)
    _register_plugin(config_dir, "off@mk", off_dir, enabled=False)

    from harness_plugin import host_context as hc

    result = hc.parent_mcp_servers(str(project_dir))  # AttributeError today.

    assert "user-disabled-srv" not in result
    assert "offsrv" not in result
    assert not any(name.startswith("plugin_off_") for name in result)

    data_fixture = result["plugin_fixture_fsrv"]["env"]["CLAUDE_PLUGIN_DATA"]
    data_harness = result["harness"]["env"]["CLAUDE_PLUGIN_DATA"]
    assert data_fixture and isinstance(data_fixture, str)
    assert data_harness and isinstance(data_harness, str)
    assert data_fixture != data_harness, "each plugin must get its own CLAUDE_PLUGIN_DATA"

    expected = {
        "user-srv": {"command": "user-cmd"},
        "proj-srv": {"command": "proj-cmd"},
        "local-srv": {"command": "local-cmd"},
        "plugin_fixture_fsrv": {
            "command": f"{fixture_dir}/bin/fsrv",
            "env": {
                "ROOT_MARKER": str(fixture_dir),
                "DATA_MARKER": data_fixture,
                "CLAUDE_PLUGIN_ROOT": str(fixture_dir),
                "CLAUDE_PLUGIN_DATA": data_fixture,
            },
        },
        "harness": {
            "command": f"{harness_dir}/bin/harness",
            "args": [],
            "env": {
                "CLAUDE_PLUGIN_ROOT": str(harness_dir),
                "CLAUDE_PLUGIN_DATA": data_harness,
            },
        },
    }
    assert result == expected

    ctx = hc.build_host_context({}, str(project_dir))
    assert ctx.dispatch_mcp_server_name == "harness"
    assert ctx.mcp_servers == expected
    assert ctx.available_mcp_servers == expected


def test_parent_mcp_servers_excludes_unapproved_project_server(monkeypatch, tmp_path):
    """R2 behaviour: a project .mcp.json server not named in
    projects[cwd].enabledMcpjsonServers is not approved and stays absent -- the
    opt-in-by-name form of approval, distinct from the blanket
    enableAllProjectMcpServers the main scenario above uses."""
    config_dir = tmp_path / "claude-config"
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    _write_json(
        config_dir / ".claude.json",
        {"projects": {str(project_dir): {"enabledMcpjsonServers": ["approved"]}}},
    )
    _write_json(
        project_dir / ".mcp.json",
        {
            "mcpServers": {
                "approved": {"command": "ok-cmd"},
                "unapproved": {"command": "no-cmd"},
            }
        },
    )

    from harness_plugin import host_context as hc

    result = hc.parent_mcp_servers(str(project_dir))
    assert result == {"approved": {"command": "ok-cmd"}}


def test_parent_mcp_servers_local_scope_requires_trust_dialog_accepted(monkeypatch, tmp_path):
    """R2 (round-2 reviewer fix): local-scope mcpServers (projects[cwd].mcpServers)
    are only forwarded when that project entry's hasTrustDialogAccepted is true --
    the same gate the real CLI applies before auto-connecting local-scope servers.
    A hand-crafted entry with no trust flag (or an explicit false) is silently
    skipped, matching test_live_claude.py's R1 live-verified comment that an
    untrusted lslow entry never appeared anywhere in the transcript even though
    the file entry itself was correct. An entry with hasTrustDialogAccepted: True
    still forwards its local servers."""
    config_dir = tmp_path / "claude-config"
    untrusted_dir = tmp_path / "untrusted-project"
    trusted_dir = tmp_path / "trusted-project"
    untrusted_dir.mkdir(parents=True)
    trusted_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    _write_json(
        config_dir / ".claude.json",
        {
            "projects": {
                str(untrusted_dir): {
                    "mcpServers": {"local-srv": {"command": "local-cmd"}},
                    # no hasTrustDialogAccepted key at all
                },
                str(trusted_dir): {
                    "mcpServers": {"local-srv": {"command": "local-cmd"}},
                    "hasTrustDialogAccepted": True,
                },
            },
        },
    )

    from harness_plugin import host_context as hc

    untrusted_result = hc.parent_mcp_servers(str(untrusted_dir))
    assert "local-srv" not in untrusted_result
    assert untrusted_result == {}

    trusted_result = hc.parent_mcp_servers(str(trusted_dir))
    assert trusted_result == {"local-srv": {"command": "local-cmd"}}


def test_parent_mcp_servers_local_scope_explicit_false_is_untrusted(monkeypatch, tmp_path):
    """Same gate, but with hasTrustDialogAccepted explicitly False rather than
    absent -- both must be treated as untrusted."""
    config_dir = tmp_path / "claude-config"
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    _write_json(
        config_dir / ".claude.json",
        {
            "projects": {
                str(project_dir): {
                    "mcpServers": {"local-srv": {"command": "local-cmd"}},
                    "hasTrustDialogAccepted": False,
                },
            },
        },
    )

    from harness_plugin import host_context as hc

    result = hc.parent_mcp_servers(str(project_dir))
    assert result == {}


def test_parent_mcp_servers_skips_corrupt_claude_json(monkeypatch, tmp_path):
    """R2 additional edge-case B: a corrupt (unparseable) .claude.json is skipped,
    not raised. The user scope (top-level mcpServers) and local scope
    (projects[cwd].mcpServers) both live in that one file, so a corrupt copy drops
    both -- but the project's own approval (`enableAllProjectMcpServers`) lives in
    a *separate* file, `<cwd>/.claude/settings.json`, so it is unaffected by the
    corruption and the project's own .mcp.json server is still approved and
    present; a plugin's own manifest lives elsewhere too and is likewise
    unaffected. Per the plan (R2 edge B): "A with a corrupt `.claude.json` gives
    A's dict minus user/local" -- i.e. proj-srv and the plugin server survive,
    only user-srv/local-srv (which only exist inside the corrupt file) are gone.
    (Fixed per round-1 test-critic tautology::F1: the previous version of this
    test wrongly asserted proj-srv absent, contradicting the plan's own stated
    case-B behaviour and the env-injection requirement.)"""
    config_dir = tmp_path / "claude-config"
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    fixture_dir = tmp_path / "plugins" / "fixture"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".claude.json").write_text("{not json", encoding="utf-8")
    _write_json(project_dir / ".mcp.json", {"mcpServers": {"proj-srv": {"command": "proj-cmd"}}})
    _write_json(project_dir / ".claude" / "settings.json", {"enableAllProjectMcpServers": True})
    _write_plugin_manifest(fixture_dir, "fixture", {"fsrv": {"command": "fsrv-cmd"}})
    _register_plugin(config_dir, "fixture@mk", fixture_dir)

    from harness_plugin import host_context as hc

    result = hc.parent_mcp_servers(str(project_dir))
    data_fixture = result["plugin_fixture_fsrv"]["env"]["CLAUDE_PLUGIN_DATA"]
    assert result == {
        "proj-srv": {"command": "proj-cmd"},
        "plugin_fixture_fsrv": {
            "command": "fsrv-cmd",
            "env": {
                "CLAUDE_PLUGIN_ROOT": str(fixture_dir),
                "CLAUDE_PLUGIN_DATA": data_fixture,
            },
        },
    }


def test_parent_mcp_servers_with_no_files_is_empty_and_dispatch_is_none(monkeypatch, tmp_path):
    """R2 additional edge-case C: no files anywhere gives {} and build_host_context
    leaves dispatch_mcp_server_name None (no `harness` key present)."""
    config_dir = tmp_path / "claude-config"
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    from harness_plugin import host_context as hc

    result = hc.parent_mcp_servers(str(project_dir))
    assert result == {}

    ctx = hc.build_host_context({}, str(project_dir))
    assert ctx.dispatch_mcp_server_name is None
    assert ctx.mcp_servers == {}
