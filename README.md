# distributed-AI-OS

A 20-hour, 10-session build of **mini-aios**: a small AI operating system that accepts agent runs over HTTP, survives crashes, and does not double-send side effects.

Course notes live in [`distributed-systems-for-ai-os.md`](distributed-systems-for-ai-os.md). This repo currently implements **Session 2** — RPC, timeouts, retries, and idempotency — on top of a Session 1 API scaffold.

## What Session 2 proves

Exactly-once delivery over a network is impossible. Effectively-once execution is not: retry at-least-once, but make a second execution a no-op.

The proof is a 3-step LangGraph agent that sends a fake email in step 2, is killed after the write and before the checkpoint, then resumes. The email file must still have **exactly one line**. Two lines means the gateway is wrong.

## Layout

```
api/                 FastAPI: POST /runs, GET /runs/{id}, POST /runs/{id}/resume
core/
  llm.py             call_llm(..., idem_key=, deadline_s=)
  tools.py           send_email → one line in data/emails/{run_id}.log
  idempotency.py     Postgres unique-key gateway
  retry.py           classify-then-retry + full jitter + retry budget
  circuit_breaker.py per-provider breaker
worker/agent.py      LangGraph: plan_email → send_email → summarize
chaos/
  proof_crash.py     kill-between-email-and-checkpoint proof
  inject_429.py      50% 429 fault injection + backoff logs
sql/001_session2.sql tables to run in Supabase
```

LiteLLM is only the provider adapter (OpenAI / Anthropic / Gemini). Retries, deadlines, jitter, budget, breaker, and idempotency live in `core/`.

## Setup

Python 3.10+ (3.12 used locally). Postgres via Supabase.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill `.env`:

- Provider keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` (optional until `LLM_BACKEND=live`)
- **Session pooler** (port **5432**) as `DIRECT_URL`. That is what the app uses. Transaction pooler (port 6543) is the Prisma-style `DATABASE_URL` and is not used here — it breaks prepared statements.
- Percent-encode special characters in the password (`,` → `%2C`)

In the Supabase SQL Editor, run [`sql/001_session2.sql`](sql/001_session2.sql) to create `idempotency` and `runs`.

Keep `.env` out of git. It is already gitignored.

## Commands

```bash
# unit tests (no Postgres)
pytest

# crash / resume proof (needs Postgres tables)
python -m chaos.proof_crash

# 50% 429 injection — watch llm_backoff delay_s= in the logs
python -m chaos.inject_429

# API
uvicorn api.main:app --reload
```

Default LLM mode is **fake** (`LLM_BACKEND=fake`), so the proof does not spend tokens. Set `LLM_BACKEND=live` and `LLM_MODEL=openai/gpt-4o-mini` (or another LiteLLM model string) for real providers.

Optional tracing: `LANGCHAIN_TRACING_V2=true` with `LANGSMITH_API_KEY`.

## API

| Method | Path | Behavior |
| --- | --- | --- |
| `GET` | `/health` | liveness |
| `POST` | `/runs` | enqueue a 3-step agent run; returns `202` with `id` |
| `GET` | `/runs/{id}` | status / output |
| `POST` | `/runs/{id}/resume` | continue a crashed thread |

```bash
curl -s -X POST http://127.0.0.1:8000/runs \
  -H 'content-type: application/json' \
  -d '{"task":"Notify that mini-aios is running.","to":"user@example.com"}'
```

## Gateway contract

`call_llm(messages, *, idem_key, deadline_s)`:

- remaining deadline passed into each attempt
- retry `429 / 500 / 502 / 503 / 504` and timeouts; never retry `400 / 401 / 403 / 422`
- full jitter: `delay = random.uniform(0, min(cap, base * 2 ** attempt))`
- max 3 attempts, retry-budget counter, circuit breaker per provider

Tool and LLM results are stored under:

```
sha256("{run_id}:{step_index}:{name}:{canonical_json(args)}")
```

A completed row is returned on repeat. `send_email` only appends the file on a first successful claim.

## Next sessions

Queues, durable checkpoints, outbox, leases, cost ledger, k8s, and tracing are still ahead in the course plan. Do not move on from Session 2 until `python -m chaos.proof_crash` prints `PASS`.
