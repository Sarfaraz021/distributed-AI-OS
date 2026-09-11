-- Run this in the Supabase SQL Editor (or any Postgres client) before starting mini-aios.
-- Session 2 only needs `idempotency`. `runs` backs POST /runs and GET /runs/{id}.

CREATE TABLE IF NOT EXISTS idempotency (
    key         TEXT PRIMARY KEY,
    status      TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'failed')),
    result      JSONB,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Primary key already unique; this index makes the lookup path explicit.
CREATE UNIQUE INDEX IF NOT EXISTS idempotency_key_uidx ON idempotency (key);

CREATE TABLE IF NOT EXISTS runs (
    id          UUID PRIMARY KEY,
    status      TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    input       JSONB NOT NULL,
    output      JSONB,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS runs_status_idx ON runs (status);
