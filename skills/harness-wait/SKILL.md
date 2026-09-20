---
name: harness-wait
description: Wait for a long-running harness subagent run (started with harness_start_agent / harness_start_prompt) without killing it. Use when a run may outlast one MCP tool call and you need its final result - instead of harness_wait_run, whose timeout cancels the run.
---

# Waiting for a harness run without cancelling it

`harness_wait_run` blocks inside an MCP tool call and **cancels the run** when its
`timeout_seconds` expires (terminal state CANCELLED). For a run that may take longer than
a tool call is allowed to, use the `harness wait-run` command from a background shell
instead: its timeout only stops the waiting, never the run.

## Flow

1. Start the run (`harness_start_agent` or `harness_start_prompt`) and note the `run_id`.
2. If the run is short, `harness_poll_run` is enough: it returns immediately and never
   cancels. Only use the steps below for runs you must wait on.
3. Call the Bash tool with `run_in_background: true` and the command:

   ```
   <harness-binary> wait-run --run-id <run_id> --timeout <seconds> [--interval <seconds>]
   ```

   `--timeout` is required. `--interval` (default 2, minimum 0.2) is the poll cadence.
4. You are notified when the command exits. Read its stdout: exactly one JSON object shaped
   like a `harness_poll_run` result, plus `waited_s` (seconds actually waited).
5. Afterwards call `harness_cleanup_run` to forget the run record.

## Exit codes

| code | meaning |
| ---- | ------- |
| 0 | run COMPLETED |
| 1 | run FAILED |
| 2 | `--timeout` elapsed first; the run is still RUNNING (re-run `wait-run`, or `harness_poll_run`) |
| 3 | run CANCELLED (e.g. by `harness_stop_run`) |
| 4 | error: unknown run id, unreadable artifacts dir, or invalid arguments (message on stderr, nothing on stdout) |

## Finding the binary

The plugin ships `bin/harness` (Linux) and `bin/harness.exe` (Windows). Resolve it without
relying on placeholder substitution:

1. `command -v harness.exe || command -v harness` - works when the plugin `bin/` is on PATH.
2. Otherwise glob the plugin cache: `~/.claude/plugins/**/agent-harness*/bin/harness*`.
3. If the bare name fails with exit 126/127 on Windows, retry with the `.exe` suffix.

Pass the same environment the MCP server runs with (in particular `HARNESS_ARTIFACTS_DIR`
if it is set) - the command finds runs through the shared artifacts directory.
