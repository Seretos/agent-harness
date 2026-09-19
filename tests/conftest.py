import json
import sys
from pathlib import Path

import pytest
from mcp import StdioServerParameters

FAKE_CLAUDE = Path(__file__).parent / "fixtures" / "fake_claude.py"


@pytest.fixture
def server_params(tmp_path) -> StdioServerParameters:
    """A real `python -m harness_plugin` server wired to the fake claude CLI,
    with artifacts and user-level config isolated under tmp_path."""
    config_dir = tmp_path / "claude-config"
    home = tmp_path / "home"
    config_dir.mkdir()
    home.mkdir()
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "harness_plugin"],
        env={
            "HARNESS_CLAUDE_ARGV": json.dumps([sys.executable, str(FAKE_CLAUDE)]),
            "HARNESS_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "HOME": str(home),
            "USERPROFILE": str(home),
        },
    )


@pytest.fixture
def project_dir(tmp_path) -> Path:
    """A project cwd with one agent definition, `demo`."""
    agents = tmp_path / "project" / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "demo.md").write_text(
        "---\nname: demo\ndescription: Demo agent for tests\n---\nSay OK.\n",
        encoding="utf-8",
    )
    return tmp_path / "project"
