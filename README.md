# agent-harness

Provider-independent subagent system for coding agents - replaces the host's built-in subagent tool calls.

## Quick install

**Claude Code:**

```
/plugin marketplace add Seretos/agent-marketplace
/plugin install agent-harness@agent-marketplace
```

Self-contained binary — no Python, no `pip install`, no dependencies. The release zip ships native binaries for both Windows (`harness.exe`) and Linux (`harness`); the host OS auto-selects the right one.

## Alternative installs

### From the GitHub Releases page

1. Download `agent-harness-<version>.zip` from [Releases](https://github.com/Seretos/agent-harness/releases).
2. Unpack to a stable folder (e.g. `C:\Users\<you>\.claude\plugins\agent-harness\` on Windows, `~/.claude/plugins/agent-harness/` on Linux).
3. In Claude Code:
   ```
   /plugin install <path-to-unpacked-folder>
   ```

### From the release branch

The `release` branch always carries the latest install-ready files (no zip step):

```
git clone --branch release --depth 1 https://github.com/Seretos/agent-harness.git
```

Then `/plugin install <cloned-path>` in Claude Code.

### Build from source

Requires Python 3.11+ (standard python.org installer with the `py` launcher on Windows; `python3` on Linux).

```powershell
git clone https://github.com/Seretos/agent-harness.git
cd agent-harness
pwsh -File scripts/build.ps1 -Clean -Package
```

Output on Windows: `bin/harness.exe`. On Linux: `bin/harness`. Then install via `/plugin install <path>`.
