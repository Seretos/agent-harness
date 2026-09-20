"""`harness wait-run`: block until a run (started by the MCP server, a different process)
ends, print one JSON object, and report the outcome as the exit code.

Never cancels: a wait timeout leaves the run RUNNING. Imports no mcp/FastMCP code."""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import NoReturn

from lib_python_harness import FileRunStore, Harness, HarnessError, RunState

from harness_plugin.runs import artifacts_root, claude_argv, run_to_dict

EXIT_COMPLETED = 0
EXIT_FAILED = 1
EXIT_TIMEOUT = 2
EXIT_CANCELLED = 3
EXIT_ERROR = 4

_MIN_INTERVAL_S = 0.2

_EPILOG = """\
exit codes:
  0  run COMPLETED
  1  run FAILED
  2  --timeout elapsed first; the run is left RUNNING (never cancelled)
  3  run CANCELLED
  4  error: unknown run id, unreadable artifacts dir, or invalid arguments

stdout is exactly one JSON object shaped like harness_poll_run's result, plus
`waited_s` (measured seconds spent waiting). Diagnostics go to stderr."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(EXIT_ERROR)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="harness wait-run",
        description="Wait for a harness run to end without ever cancelling it.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--run-id", required=True, help="run to wait for")
    p.add_argument(
        "--timeout", required=True, type=float, help="give up waiting after this many seconds"
    )
    p.add_argument(
        "--interval", type=float, default=2.0, help="poll interval in seconds (default 2, min 0.2)"
    )
    return p


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    interval = max(args.interval, _MIN_INTERVAL_S)
    started = time.monotonic()
    try:
        h = Harness(store=FileRunStore(artifacts_root()), claude_argv=claude_argv())
        result = h.wait_for(args.run_id, timeout=max(args.timeout, 0.0), poll_interval=interval)
    except (HarnessError, OSError) as exc:
        print(f"harness wait-run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    waited = round(time.monotonic() - started, 1)
    sys.stdout.write(json.dumps(run_to_dict(result, waited_s=waited)) + "\n")
    sys.stdout.flush()
    return {
        RunState.COMPLETED: EXIT_COMPLETED,
        RunState.FAILED: EXIT_FAILED,
        RunState.CANCELLED: EXIT_CANCELLED,
    }.get(result.state, EXIT_TIMEOUT)

