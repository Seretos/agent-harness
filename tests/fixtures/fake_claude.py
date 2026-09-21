"""Minimal stand-in for the `claude` CLI, driven through HARNESS_CLAUDE_ARGV.

Answers `--version`; otherwise reads the prompt from stdin and emits the
stream-json events `lib_python_harness` parses (a terminal `result` event
carrying `result` and `session_id`). A prompt containing `SLEEP:<seconds>`
keeps the process alive first, so one fixture yields both a fast run and a
still-RUNNING run. With HARNESS_FAKE_ARGV_LOG set, each real invocation appends
one JSON line {"argv": [...], "cwd": ...} so tests can assert which flags and
working directory the CLI actually received. A prompt containing `NO_RESULT`
exits after the init event without any `result` event (a FAILED run).
A prompt containing `TICK:<count>:<interval>` emits `count` assistant events
`interval` seconds apart (flushed) before the terminal `result` event, so a
still-RUNNING run makes visible progress.
A prompt containing `TOOL:<name>` emits one assistant `tool_use` event for that tool right
after the init event (before any SLEEP), so a still-RUNNING run's last activity is that tool.
A prompt containing `ECHO:<word>` is answered with `<word>` instead of `OK`. The session
id is taken from `--session-id <id>` or, for a resumed run, `--resume <id>`.
"""
import json
import os
import re
import sys
import time


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        print("2.0.0 (Claude Code)")
        return 0

    log = os.environ.get("HARNESS_FAKE_ARGV_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"argv": argv, "cwd": os.getcwd()}) + "\n")

    session_id = "fake-session"
    for flag in ("--session-id", "--resume"):
        if flag in argv:
            session_id = argv[argv.index(flag) + 1]

    prompt = sys.stdin.read()
    match = re.search(r"SLEEP:(\d+(?:\.\d+)?)", prompt)
    print(json.dumps({"type": "system", "subtype": "init", "session_id": session_id}), flush=True)
    tool = re.search(r"TOOL:(\w+)", prompt)
    if tool:
        print(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "id": "toolu_1", "name": tool.group(1), "input": {}}
                        ],
                    },
                    "session_id": session_id,
                }
            ),
            flush=True,
        )
    if match:
        deadline = time.monotonic() + float(match.group(1))
        while time.monotonic() < deadline:
            time.sleep(0.1)

    tick = re.search(r"TICK:(\d+):(\d+(?:\.\d+)?)", prompt)
    if tick:
        for i in range(int(tick.group(1))):
            print(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": f"tick {i}"}],
                        },
                        "session_id": session_id,
                    }
                ),
                flush=True,
            )
            time.sleep(float(tick.group(2)))

    echo = re.search(r"ECHO:(\w+)", prompt)
    answer = echo.group(1) if echo else "OK"

    if "NO_RESULT" in prompt:
        # Ends without a terminal `result` event: the run finishes FAILED.
        return 0

    print(
        json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": answer}]},
                "session_id": session_id,
            }
        ),
        flush=True,
    )
    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": answer,
                "session_id": session_id,
                "total_cost_usd": 0.0,
                "usage": {},
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
