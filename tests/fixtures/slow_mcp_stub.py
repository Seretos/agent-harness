"""Minimal slow-starting MCP stub server (stdio transport), for #38 R1's live
first-turn-announcement test.

Args: ``<name> <delay_s> <log_path>``. Sleeps ``delay_s`` seconds *before* serving
-- only a controlled delay reproduces the "still in pendingMcpServers at the
model's first turn" symptom a fast-connecting stub never would. Once past the
sleep it serves exactly one tool, ``stub_nonce``, which returns
``"<name>:<uuid4 hex>"`` and appends that same value as one line to
``log_path`` -- so a test can prove the tool was genuinely *called* (not just
that the server *connected*) by checking the logged nonce shows up in the
run's own transcript.
"""
from __future__ import annotations

import sys
import time
import uuid


def main() -> int:
    name, delay_s, log_path = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    time.sleep(delay_s)

    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(name)

    @mcp.tool()
    def stub_nonce() -> str:
        """Return a fresh "<name>:<uuid4 hex>" nonce and log it to log_path."""
        value = f"{name}:{uuid.uuid4().hex}"
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(value + "\n")
        return value

    mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
