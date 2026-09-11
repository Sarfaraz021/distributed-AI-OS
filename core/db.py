from __future__ import annotations

from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings


@contextmanager
def get_conn():
    with psycopg.connect(get_settings().postgres_dsn, row_factory=dict_row) as conn:
        yield conn
