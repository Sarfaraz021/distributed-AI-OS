from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from core.db import get_conn
from worker.agent import resume_run, start_run

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="mini-aios", version="0.1.0")


class RunRequest(BaseModel):
    task: str = Field(default="Notify the user that mini-aios is running.")
    to: str = Field(default="user@example.com")


class RunResponse(BaseModel):
    id: UUID
    status: str
    input: dict[str, Any]
    output: dict[str, Any] | None = None
    error: str | None = None


def _missing_table(exc: Exception) -> bool:
    text = str(exc).lower()
    return "does not exist" in text or "undefinedtable" in text


def _fetch_run(run_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,)).fetchone()


def _execute_run(run_id: str, task: str, to: str) -> None:
    try:
        output = start_run(run_id, task=task, to=to)
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = 'completed', output = %s, updated_at = now()
                WHERE id = %s
                """,
                (Json(output), run_id),
            )
            conn.commit()
    except Exception as exc:
        logger.exception("run_failed id=%s", run_id)
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = 'failed', error = %s, updated_at = now()
                WHERE id = %s
                """,
                (str(exc), run_id),
            )
            conn.commit()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs", response_model=RunResponse, status_code=202)
def create_run(body: RunRequest, background_tasks: BackgroundTasks) -> RunResponse:
    run_id = str(uuid4())
    payload = body.model_dump()
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO runs (id, status, input)
                VALUES (%s, 'queued', %s)
                """,
                (run_id, Json(payload)),
            )
            conn.commit()
    except Exception as exc:
        if _missing_table(exc):
            raise HTTPException(
                status_code=503,
                detail="Postgres tables missing. Run sql/001_session2.sql in the Supabase SQL Editor.",
            ) from exc
        raise
    background_tasks.add_task(_execute_run, run_id, body.task, body.to)
    return RunResponse(id=UUID(run_id), status="queued", input=payload)


@app.get("/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: UUID) -> RunResponse:
    try:
        row = _fetch_run(str(run_id))
    except Exception as exc:
        if _missing_table(exc):
            raise HTTPException(
                status_code=503,
                detail="Postgres tables missing. Run sql/001_session2.sql in the Supabase SQL Editor.",
            ) from exc
        raise
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunResponse(
        id=row["id"],
        status=row["status"],
        input=row["input"],
        output=row["output"],
        error=row["error"],
    )


@app.post("/runs/{run_id}/resume", response_model=RunResponse)
def resume(run_id: UUID) -> RunResponse:
    try:
        output = resume_run(str(run_id))
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = 'completed', output = %s, error = NULL, updated_at = now()
                WHERE id = %s
                """,
                (Json(output), str(run_id)),
            )
            conn.commit()
    except Exception as exc:
        logger.exception("resume_failed id=%s", run_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    row = _fetch_run(str(run_id))
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunResponse(
        id=row["id"],
        status=row["status"],
        input=row["input"],
        output=row["output"],
        error=row["error"],
    )
