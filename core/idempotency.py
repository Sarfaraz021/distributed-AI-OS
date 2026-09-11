from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from psycopg.types.json import Json

from core.db import get_conn
from core.errors import IdempotencyInProgressError


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def make_idem_key(run_id: str, step_index: int, name: str, args: dict[str, Any]) -> str:
    material = f"{run_id}:{step_index}:{name}:{canonical_json(args)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class IdempotencyRow:
    key: str
    status: str
    result: Any = None
    error: str | None = None


class IdempotencyStore(Protocol):
    def begin_or_get(self, key: str) -> IdempotencyRow | None:
        """Insert in_progress. Return existing row on conflict, else None."""

    def complete(self, key: str, result: Any) -> None: ...

    def fail(self, key: str, error: str) -> None: ...

    def reclaim_failed(self, key: str) -> bool: ...

    def get(self, key: str) -> IdempotencyRow | None: ...


class PostgresIdempotencyStore:
    def begin_or_get(self, key: str) -> IdempotencyRow | None:
        with get_conn() as conn:
            with conn.transaction():
                inserted = conn.execute(
                    """
                    INSERT INTO idempotency (key, status)
                    VALUES (%s, 'in_progress')
                    ON CONFLICT (key) DO NOTHING
                    RETURNING key, status, result, error
                    """,
                    (key,),
                ).fetchone()
                if inserted:
                    return None
                row = conn.execute(
                    "SELECT key, status, result, error FROM idempotency WHERE key = %s",
                    (key,),
                ).fetchone()
        if row is None:
            return None
        return IdempotencyRow(**row)

    def complete(self, key: str, result: Any) -> None:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE idempotency
                SET status = 'completed', result = %s, error = NULL, updated_at = now()
                WHERE key = %s
                """,
                (Json(result), key),
            )
            conn.commit()

    def fail(self, key: str, error: str) -> None:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE idempotency
                SET status = 'failed', error = %s, updated_at = now()
                WHERE key = %s
                """,
                (error, key),
            )
            conn.commit()

    def reclaim_failed(self, key: str) -> bool:
        with get_conn() as conn:
            row = conn.execute(
                """
                UPDATE idempotency
                SET status = 'in_progress', error = NULL, updated_at = now()
                WHERE key = %s AND status = 'failed'
                RETURNING key
                """,
                (key,),
            ).fetchone()
            conn.commit()
        return row is not None

    def get(self, key: str) -> IdempotencyRow | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT key, status, result, error FROM idempotency WHERE key = %s",
                (key,),
            ).fetchone()
        return IdempotencyRow(**row) if row else None


class MemoryIdempotencyStore:
    def __init__(self) -> None:
        self._rows: dict[str, IdempotencyRow] = {}

    def begin_or_get(self, key: str) -> IdempotencyRow | None:
        if key in self._rows:
            return self._rows[key]
        self._rows[key] = IdempotencyRow(key=key, status="in_progress")
        return None

    def complete(self, key: str, result: Any) -> None:
        self._rows[key] = IdempotencyRow(key=key, status="completed", result=result)

    def fail(self, key: str, error: str) -> None:
        self._rows[key] = IdempotencyRow(key=key, status="failed", error=error)

    def reclaim_failed(self, key: str) -> bool:
        row = self._rows.get(key)
        if row is None or row.status != "failed":
            return False
        self._rows[key] = IdempotencyRow(key=key, status="in_progress")
        return True

    def get(self, key: str) -> IdempotencyRow | None:
        return self._rows.get(key)


_store: IdempotencyStore | None = None


def get_store() -> IdempotencyStore:
    global _store
    if _store is None:
        _store = PostgresIdempotencyStore()
    return _store


def set_store(store: IdempotencyStore) -> None:
    global _store
    _store = store


def execute_idempotent(key: str, fn):
    store = get_store()
    existing = store.begin_or_get(key)
    if existing is not None:
        if existing.status == "completed":
            return existing.result, True
        if existing.status == "in_progress":
            raise IdempotencyInProgressError(
                f"idempotency key already in progress: {key}",
                status_code=409,
            )
        if existing.status == "failed" and not store.reclaim_failed(key):
            raise IdempotencyInProgressError(
                f"idempotency key already in progress: {key}",
                status_code=409,
            )
    try:
        result = fn()
    except Exception as exc:
        store.fail(key, str(exc))
        raise
    store.complete(key, result)
    return result, False
