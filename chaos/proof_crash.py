"""Crash/resume proof for the idempotent send_email gateway.

Starts a 3-step agent, kills the process after the email is written and before
LangGraph checkpoints, then resumes. The email file must contain exactly one line.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db import get_conn  # noqa: E402
from core.tools import email_line_count, email_log_path  # noqa: E402


def _require_postgres() -> None:
    try:
        with get_conn() as conn:
            row = conn.execute("select to_regclass('public.idempotency') as t").fetchone()
    except Exception as exc:
        print("Cannot reach Postgres.")
        print("In Supabase: Connect → Direct (or Session pooler, port 5432) → copy URI into DATABASE_URL.")
        print(exc)
        raise SystemExit(2) from exc
    if not row or not row["t"]:
        print("Table public.idempotency is missing. Run sql/001_session2.sql in the Supabase SQL Editor.")
        raise SystemExit(2)


def _run(env: dict[str, str], args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "worker.run", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


def main() -> int:
    _require_postgres()
    run_id = f"proof-{uuid.uuid4()}"
    env = os.environ.copy()
    env["LLM_BACKEND"] = env.get("LLM_BACKEND", "fake")
    env["FAULT_INJECT_429"] = "false"

    log_path = email_log_path(run_id)
    if log_path.exists():
        log_path.unlink()

    crash_env = env.copy()
    crash_env["CRASH_AFTER_EMAIL"] = "true"
    crashed = _run(crash_env, ["--run-id", run_id])
    print(crashed.stdout)
    print(crashed.stderr, file=sys.stderr)

    if crashed.returncode == 0:
        print("expected the first process to die before checkpoint; it completed instead")
        return 1

    lines_after_crash = email_line_count(run_id)
    print(f"lines after crash: {lines_after_crash}")
    if lines_after_crash != 1:
        print("email file should have exactly one line after the crash")
        return 1

    resume_env = env.copy()
    resume_env["CRASH_AFTER_EMAIL"] = "false"
    resumed = _run(resume_env, ["--run-id", run_id, "--resume"])
    print(resumed.stdout)
    print(resumed.stderr, file=sys.stderr)
    if resumed.returncode != 0:
        print("resume failed")
        return 1

    lines_after_resume = email_line_count(run_id)
    print(f"lines after resume: {lines_after_resume}")
    if lines_after_resume != 1:
        print("gateway is wrong — duplicate side effect. fix core/tools.py before moving on.")
        return 1

    print(f"PASS: {log_path} has exactly one line after crash+resume")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
