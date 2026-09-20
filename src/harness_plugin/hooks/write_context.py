"""`harness hook`: hook stdin JSON -> <CLAUDE_PLUGIN_DATA>/sessions/<session_id>.json.

Never blocks the parent's tool call: any failure exits 0 having written nothing."""
from __future__ import annotations

import json
import os
import sys

from harness_plugin.host_context import PROJECT_ENV, write_session_context

_KEYS = ("session_id", "cwd", "permission_mode", "effort", "model", "transcript_path")


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return 0
        record = {k: data[k] for k in _KEYS if isinstance(data.get(k), str) and data[k]}
        if "session_id" not in record:
            return 0
        project = os.environ.get(PROJECT_ENV)
        if project:
            record["project_dir"] = project
        write_session_context(record)
    except Exception:
        return 0
    return 0
