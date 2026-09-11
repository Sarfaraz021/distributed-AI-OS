from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool

from core.config import get_settings
from core.idempotency import execute_idempotent, make_idem_key

logger = logging.getLogger(__name__)


def email_log_path(run_id: str) -> Path:
    return get_settings().email_dir / f"{run_id}.log"


def send_email(to: str, body: str, *, run_id: str, step_index: int) -> dict[str, Any]:
    """Append one JSON line to a per-run file. Repeats with the same key do not append."""

    args = {"to": to, "body": body}
    key = make_idem_key(run_id, step_index, "send_email", args)

    def _write() -> dict[str, Any]:
        path = email_log_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"run_id": run_id, "to": to, "body": body}) + "\n")
        logger.info("email_sent run_id=%s to=%s path=%s", run_id, to, path)
        return {"ok": True, "to": to, "path": str(path)}

    result, cached = execute_idempotent(key, _write)
    if cached:
        logger.info("email_idempotency_hit run_id=%s step=%s", run_id, step_index)
    return result


def send_email_tool(run_id: str, step_index: int) -> StructuredTool:
    def _run(to: str, body: str) -> str:
        return json.dumps(send_email(to, body, run_id=run_id, step_index=step_index))

    return StructuredTool.from_function(
        func=_run,
        name="send_email",
        description="Send an email. The observable side effect is one line appended to a local file.",
    )


def email_line_count(run_id: str) -> int:
    path = email_log_path(run_id)
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
