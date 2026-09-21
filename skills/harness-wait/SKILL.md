---
name: harness-wait
description: Keep waiting for a harness subagent run (started with harness_start_agent / harness_start_prompt) that outlasts one MCP tool call, and read a run's liveness fields to tell a working run from a hung one. Use when a wait limit elapsed while the run was unfinished - e.g. harness_wait_run came back RUNNING - or when you want an unattended wait from a background shell. Covers both wait paths, the `harness wait` command and its exit codes.
---

# Waiting for a harness run without cancelling it

No wait limit in this plugin ends a run. `harness_wait_run`'s `timeout_seconds` and the
`harness wait` command's `--timeout` end only the waiting; the run itself keeps going.
Only `harness_stop_run` cancels a run (terminal state CANCELLED).

So when a wait limit elapses on an unfinished run: keep waiting. Do not restart the run,
do not start a replacement, and do not report the run as cancelled or dead - the work it
has already done is still running and still paid for.

## Flow

1. Start the run (`harness_start_agent` or `harness_start_prompt`) and note the `run_id`.
2. For a state check without waiting, call `harness_poll_run`; it returns immediately.
3. To wait for the result, pick path A or path B below. Either can follow the other: an
   elapsed limit in one is a reason to continue in the other, never a reason to stop.
4. If you lost the `run_id` (e.g. after context compaction), find the run with
   `harness_list_runs` first. Once you have the result, call `harness_cleanup_run` to
   forget the run record.
5. To continue a finished run's conversation, call `harness_send_message(run_id, prompt)`:
   it returns a **new** `run_id` (state RUNNING); wait on that one the same way.

## Path A - blocking tool call: `harness_wait_run(run_id, timeout_seconds)`

Blocks inside the MCP tool call for up to `timeout_seconds` (default 300). Use it when the
run has a fair chance of finishing within that limit.

If the limit expires first, the result comes back with `state: RUNNING` plus:

- `duration_s` - how long the run has been going.
- `event_count` and `last_event_at` - progress markers that only advance while the state is
  RUNNING. A growing count and an advancing timestamp mean the run is working, so wait
  again; both frozen across two checks mean it may be hung, which is when
  `harness_stop_run` becomes a deliberate choice.
- `last_activity` - the last tool or command the run used.
- `next_step` - a hint telling you how to continue.

Continue by calling `harness_wait_run` again with a fresh `timeout_seconds`, or switch to
path B so the wait outlives the tool call.

## Path B - unattended wait in a background shell: `harness wait <run_id>`

Call the Bash tool with `run_in_background: true` and the command:

```
<harness-binary> wait <run_id> [--timeout <seconds>] [--interval <seconds>]
```

`--timeout` is optional: omit it and the command waits until the run ends, which is the
right default for an unattended wait. Give it only when you want the shell to report back
after a bounded stretch. `--interval` (default 2, minimum 0.2) is the poll cadence.

You are notified when the command exits. Read its stdout: exactly one JSON object shaped
like a `harness_poll_run` result, plus `waited_s` (seconds actually waited). The exit code
says what happened.

## Exit codes

| code | meaning |
| ---- | ------- |
| 0 | run COMPLETED |
| 1 | run FAILED |
| 2 | `--timeout` (if given) elapsed; the run is left RUNNING, never cancelled - judge progress from the printed liveness fields, then wait again (path A or path B) |
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
