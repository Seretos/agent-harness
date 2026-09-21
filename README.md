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

## Waiting for a long run: `harness wait-run`

`harness_wait_run` cancels the run when its timeout expires. To wait for a run that outlasts a tool call, run the binary's second entry point from a background shell (see the `harness-wait` skill):

```
harness wait-run --run-id <id> --timeout <seconds> [--interval <seconds>]
```

`harness_list_runs` lists all recorded runs as compact rows (`run_id`, `state`, `model`, `cwd`, `created_at`, `label`; no text or usage) so a lost `run_id` can be found again; `harness_start_prompt`/`harness_start_agent` accept an optional `label`, `harness_start_agent` also accepts an optional `prompt` (the run's task / user message; the agent definition's body stays its system prompt), and `harness_poll_run` reports `event_count`/`last_event_at` while a run is RUNNING.

`harness_send_message(run_id, prompt)` sends a follow-up to a **finished** run: it resumes the origin's session (keeping its isolation) and returns a new RUNNING run with its own `run_id`; then use `harness_wait_run` or `harness_poll_run` on it.

It prints one `harness_poll_run`-shaped JSON object plus `waited_s` on stdout and never cancels the run. `--timeout` is required; `--interval` defaults to 2 (minimum 0.2).

| exit code | meaning |
| --------- | ------- |
| 0 | run COMPLETED |
| 1 | run FAILED |
| 2 | `--timeout` elapsed; the run is still RUNNING |
| 3 | run CANCELLED |
| 4 | error: unknown run id, unreadable artifacts dir, or invalid arguments |
