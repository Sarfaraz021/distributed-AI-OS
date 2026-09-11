# Distributed Systems for AI Operating Systems
### A 20-hour, 10-session practical plan — built for agent infrastructure, not exams

---

## How to use this plan

**Format of every session:** 2 hours, broken down as:

| Time | Block |
|---|---|
| 0:00 – 0:30 | Concepts (read/watch the assigned material, take notes in your own words) |
| 0:30 – 0:50 | Practical mapping (write down how it applies to a system you are actually building) |
| 0:50 – 1:45 | Hands-on exercise (code or design, always producing an artifact) |
| 1:45 – 2:00 | Review: active recall, key takeaways, answer the questions from memory |

**Rules that make this work:**

1. **Never watch a lecture passively.** Pause every 10 minutes and write one sentence about what you just learned.
2. **The hands-on exercise is the point.** If you run out of time, cut the reading, not the build.
3. **Every session adds to one project.** By session 10 you will have a working mini AI OS you can point at, break, measure, and cost out.
4. **Keep an ADR log.** One markdown file, one entry per decision: context, options, decision, consequences. This is the habit that turns knowledge into architecture skill.
5. **Answer the review questions out loud, from memory, before checking notes.** Recall beats re-reading by a wide margin.

**Suggested pace:** 3 sessions/week over 3.5 weeks, or 1 session/day over 2 weeks. Do not do more than 2 sessions in one day — the recall step needs a sleep cycle between sessions to stick.

**If you only have 10 hours:** do sessions 2, 3, 4, 7, 8 in full. Those five cover retries/idempotency, queues/concurrency, durable state, caching/cost, and scheduling/resources, which is where most AI OS failures and most of the bill actually come from.

---

## The through-line project: `mini-aios`

Every exercise builds one system. Start it in session 1 and keep it in a single Git repo.

**What it is:** a small AI Operating System that accepts agent-run requests over HTTP, queues them, executes multi-step agent workflows in a worker pool, survives crashes, caches aggressively, scales on queue depth, and reports cost and traces per run.

**Target end state after 20 hours:**

```
mini-aios/
├── api/              FastAPI: submit run, get status, stream result
├── worker/           async worker pool consuming the queue
├── core/
│   ├── llm.py        idempotent, retried, cached, cost-metered LLM gateway
│   ├── tools.py      idempotent tool-call gateway
│   ├── state.py      checkpoint + resume for long-running runs
│   ├── queue.py      enqueue/claim/ack/nack/DLQ
│   └── limits.py     token-bucket rate limiting + per-tenant budgets
├── deploy/           Dockerfiles, kind/k8s manifests, KEDA scaler
├── obs/              OpenTelemetry setup, dashboards, SLO definitions
├── chaos/            failure injection scripts
├── loadtest/         load generator + results
└── docs/
    ├── adr/          architecture decision records
    ├── runbook.md
    └── cost-model.md
```

**Stack (deliberately boring, matches what you already use):** Python + FastAPI, Postgres, Redis, Docker, kind (local Kubernetes), OpenTelemetry, LangGraph for the agent graph. Ray (a distributed compute framework for fan-out tasks and stateful GPU workers, covered in session 8) and Temporal (a durable-execution engine that persists workflow progress so long-running jobs survive crashes, covered in session 4) appear as comparisons, not requirements.

**A note on resources:** links and version details below reflect what I know as of mid-2026 and I cannot browse to verify them right now, so titles and URLs may have moved. Search the exact title if a link 404s, and double-check paper details before citing them anywhere formal.

---

# Session 1 — The mental model: partial failure, latency budgets, and service boundaries

## Core concepts

1. **Partial failure.** In a single process, either everything runs or nothing does. In a distributed system, half of it works and the other half is unreachable, slow, or lying. Every design decision downstream is a response to this one fact.
2. **You cannot distinguish "slow" from "dead."** A timeout is a guess, not information. This is why every remote call needs a deadline and every deadline needs a recovery plan.
3. **The eight fallacies of distributed computing** (network is reliable, latency is zero, bandwidth is infinite, network is secure, topology doesn't change, one admin, transport cost is zero, network is homogeneous). Read them once, then use them as a review checklist forever.
4. **Failure taxonomy:** crash-stop, crash-recover, omission (message lost), timing (too slow), and Byzantine (component lies). You will design for the first four and ignore the fifth. AI systems add a sixth: **semantic failure** — the call succeeds, returns 200, and the content is wrong.
5. **Latency budgets.** You cannot reason about architecture without knowing the numbers.
6. **Service boundaries** — the real answer to "one container or many."

## Practical explanation

**Numbers to memorize** (order of magnitude is what matters):

| Operation | Time |
|---|---|
| Memory read | ~100 ns |
| Redis GET, same network | ~0.2–1 ms |
| Postgres indexed query | ~1–5 ms |
| Same-region service call | ~1–5 ms |
| Cross-region round trip | ~50–150 ms |
| Vector search, 1M vectors, HNSW | ~5–50 ms |
| **LLM time-to-first-token** | **~300 ms – 2 s** |
| **LLM full completion** | **~2 – 60 s** |
| Agent run, 8 LLM steps | ~30 – 300 s |

The critical insight: **in an AI system, one LLM call costs more time than 10,000 internal network hops.** This inverts classic distributed systems advice. Chattiness between your own services barely matters. What matters is: how many LLM calls, how many tokens, how much concurrency, and what happens when one call hangs.

**Service boundaries — when do you actually need a separate container?**

Default answer: **you don't.** Put everything in one app (API + agent logic + workers) and only split a piece out when one of these five things is genuinely true for it:

1. **It needs to scale on its own schedule.** Example: your API only ever needs 2 copies running, but during a big batch job you need 40 workers. If they're stuck in the same container, you're forced to over-provision the API just to get enough workers.
2. **It needs different hardware.** Example: one part needs a GPU (embeddings), the rest just needs CPU. Bundling them means you pay for GPU machines everywhere, even where you don't need one.
3. **A crash in it shouldn't be able to crash everything else.** Example: you're running code an LLM generated. If that crashes or hangs, it must not be able to take your main orchestrator down with it.
4. **You change it much more often than the rest.** Example: you tweak your agent prompts multiple times a day, but the data-ingestion pipeline barely changes. Shipping them together means every tiny prompt tweak redeploys — and risks breaking — code that didn't need to move.
5. **It needs its own security wall.** Example: one customer's data must be handled in a way that's provably separate from everyone else's.

**If none of the five apply, keep it as one container.** Splitting things up isn't free: a function call inside one process is basically instant and can't really fail. The moment you split it into two containers, that same call becomes a network request — slower, and now it can time out, drop, or fail in ways a local call never could. Only take on that cost when you're actually getting one of the five things above in return.

## AI/Agent relevance

An agent run is a **distributed transaction you did not design**: it touches the LLM provider, 3–10 tools (each an external API), a database, a vector store, and a queue. Each has independent failure modes and none of them roll back together. Most "agent framework" bugs in production are really distributed systems bugs wearing a costume:

- Duplicate emails sent → at-least-once delivery without idempotency (session 2)
- Agent forgets everything after a deploy → state in process memory (session 4)
- Bill triples overnight → retry storm plus no caching (sessions 2 and 7)
- One customer's batch job starves everyone → no fairness or backpressure (session 3)

## Architecture example

**AI OS v0 — the smallest thing that is correctly shaped:**

*Quick definition before the diagram: a "replica" just means a running copy of the same program. "2 replicas" of the API service means you have that exact same API code running as 2 separate processes at once (usually 2 pods/containers), sitting behind a load balancer that spreads incoming requests across them. You run more than one copy for two reasons: if one crashes or is being redeployed, the other keeps serving requests, and two copies can handle roughly twice the traffic one could. "N replicas" for the worker pool means the same idea, except N is a number that goes up and down automatically as the queue gets busier or quieter (that's what autoscaling does — covered in session 8).*

```
   Client
     │  POST /runs  (returns run_id in <200ms)
     ▼
┌──────────────┐        ┌───────────────┐
│  API service │───────▶│   Postgres    │  runs, steps, dedup, outbox
│  (2 replicas)│        │               │
└──────┬───────┘        └───────▲───────┘
       │ enqueue                │
       ▼                        │
┌──────────────┐                │
│    Queue     │                │
│ (Redis/PG)   │                │
└──────┬───────┘                │
       │ claim                  │ checkpoint
       ▼                        │
┌──────────────────────┐        │
│   Worker pool        │────────┘
│ (async, N replicas)  │──────▶ LLM providers / tools / vector DB
└──────────────────────┘
```

Three deployables, not one, and not fifteen — "deployable" here means one of these boxes: one codebase that gets built and shipped as its own unit, which you can then run as 1, 2, or many replicas. The API and workers split into separate deployables because of the scaling axis trigger (item 1 from the list above: the API needs a small, steady number of copies, while the worker pool needs to grow and shrink a lot). Everything else lives inside those two until a trigger fires.


## Hands-on exercise (55 min)

1. **Draw your real system.** Take one client AI system you have actually built. Draw every process, every datastore, every external API, and every network hop on one page.
2. **Failure inventory.** Build this table and fill in at least 10 rows:

   | Component | Failure mode | Blast radius | Current handling | Gap |
   |---|---|---|---|---|
   | LLM provider | 429 rate limited | all runs stall | naive retry | no backoff, no budget |

3. **Latency + cost budget for one agent run.** List every step, its expected p50/p99 latency, and its token cost. Total it. You now know your run's theoretical floor.
4. **Scaffold `mini-aios`:** FastAPI app with `POST /runs` and `GET /runs/{id}`, Postgres via Docker Compose, one Dockerfile. Nothing clever yet.

## Best resources

- **MIT 6.824 Lecture 1, "Introduction"** — video: `youtube.com/watch?v=cQP8WApzIQQ` (Spring 2020 recording; the current in-person course has no public video, same material). Notes: `pdos.csail.mit.edu/6.824/notes/l01.txt`. The best 80 minutes on why distributed systems are hard. *Before you watch: this lecture uses MapReduce as its running example. In one sentence — MapReduce splits huge data into chunks, processes each chunk independently in parallel ("map"), then combines all results that share a key ("reduce"). The point the lecture is actually making with it isn't the map/reduce mechanics themselves, it's what happens when a worker machine dies mid-task and how the system recovers without anyone noticing.*
- **DDIA, chapter 1 and chapter 8** ("Reliable, Scalable, Maintainable Applications" and "The Trouble with Distributed Systems"). Chapter 8 is the single best chapter in the book for your goals.
- **"Fallacies of Distributed Computing Explained"** — Arnon Rotem-Gal-Oz, free PDF.
- **"Latency Numbers Every Programmer Should Know"** — Jeff Dean's list, plus any of the interactive versions.
- **AWS Builders' Library** — free, written by people running this at scale. Start with "Challenges with distributed systems."

## 15-minute review

Say out loud, from memory: the five triggers for splitting a service; three numbers from the latency table; what "partial failure" means in one sentence.

## Interview / architecture questions

1. A tool call to a payment API times out after 30 seconds. What are the three possible states of the world, and how does your design tell them apart?
2. Your agent orchestrator is one container. Give me two specific conditions under which you would split it, and two under which splitting would be a mistake.
3. Why does the classic advice "minimize network calls" matter much less in an LLM agent system than in a traditional web backend? What replaces it as the dominant constraint?
4. A run takes 45 seconds end to end. 42 seconds of that is LLM wait. Where can you possibly find latency savings, ranked by impact?
5. What is a semantic failure and why can no amount of retry logic fix it?

---

# Session 2 — RPC, timeouts, retries, and idempotency (the highest-leverage session)

*Quick definition: RPC ("Remote Procedure Call") is calling a function that actually runs on a different machine, written so it looks like an ordinary local function call in your code. Every LLM call, tool call, and cross-service call in your system is an RPC — and the whole point of this session is what to do about the fact that, unlike a local call, it can hang, get lost, or succeed without you finding out.*

## Core concepts

All six of these exist because of one root problem: when you call something over a network, you often can't tell whether it actually worked.

1. **RPC delivery semantics — how many times did the other side actually run your request?**
   There are three possible guarantees, and one practical goal:
   - **At-most-once:** send it once, never retry. If it gets lost, it just... never happened. Simple, but you're left not knowing if it worked.
   - **At-least-once:** if you don't hear back, you retry. This fixes "it never happened" — but now it might happen *twice*, because your first attempt could have actually succeeded right before your retry went out.
   - **Exactly-once:** the dream — happens exactly once, no more, no less. This is genuinely **impossible to guarantee over a network**, because you can never tell the difference between "it failed" and "it worked, but the confirmation got lost on the way back to you."
   - **Effectively-once (the actual, achievable goal):** retry as much as you need to (at-least-once), but design the action so that running it twice has the *exact same effect* as running it once — that's what "idempotent" means, covered in point 5. You get exactly-once *results* even though the call might genuinely run more than once behind the scenes.

   *Example: your agent sends an invoice email, the "it worked" confirmation gets lost on the way back, so your code retries the send. Did the customer just get two invoices? That's the at-least-once problem in action — and it's the default outcome unless you specifically build around it.*

2. **Timeouts — how long you wait before giving up on a call.**
   Set this from real, measured numbers — what do 99% of your calls actually take? — not a guess and not an average. Averages hide exactly the slow 1% that actually matters.
   Also **pass the remaining time budget along the chain.** If a request comes in with "you have 10 seconds total" and step 1 eats 3 of them, step 2 should know it only has 7 seconds left — not start its own fresh 10-second clock. Otherwise your system keeps grinding on work the original caller already gave up on and walked away from.

3. **Retries — trying again after a failure, carefully.**
   - **Exponential backoff:** wait longer between each attempt (1s, then 2s, then 4s, then 8s...) instead of retrying instantly, so you're not hammering something that's already struggling.
   - **Full jitter:** add randomness to that wait time. If 500 requests all fail at the same second and all retry after exactly 1 second, they all slam the service again at the same instant — often causing the very outage you were trying to recover from. Randomizing the wait spreads them out instead.
   - **Retry budget:** a hard cap on how much of your total traffic is allowed to be retries (say, never more than 10%). Without this cap, a bad patch can spiral: failures cause retries, retries add load, added load causes more failures, which cause more retries.

4. **Circuit breakers — stop calling something that's clearly broken.**
   Same idea as the breaker in your home's fuse box: after enough failures in a row, it "trips" and stops sending calls to that dependency for a while, instead of continuing to bang on a door nobody's answering. After a cooldown, it lets one test call through to check whether things have recovered before opening back up fully.

5. **Idempotency keys and dedup tables — making "doing it twice" harmless.**
   *("Idempotent" just means: doing it once or doing it five times gives the exact same result — like pressing an elevator button repeatedly still only brings one elevator, unlike transferring money twice, which moves double.)*
   Give every action a unique fingerprint based on what it is and which run/step it belongs to. Before actually doing the action, check a table: "have I already done this exact thing?" If yes, just hand back the result from last time instead of doing it again.
   *Example: the fingerprint might be "run 42, step 3, send-invoice." If a result is already recorded under that fingerprint, a retry just returns that stored result — no second email goes out.*

6. **Hedged requests — a speed trick, not a safety one.**
   If a call is running unusually slow, fire off a second identical request and just use whichever one answers first. This shaves your worst-case latency, but it also doubles the cost of every call you hedge this way — so it's for cases where speed genuinely matters more than the extra spend, used sparingly.

## Practical explanation

**The retry formula that matters:**

```python
delay = random.uniform(0, min(cap, base * 2 ** attempt))   # full jitter
```

*Reading it piece by piece: `base * 2 ** attempt` is the exponential backoff — 1s, 2s, 4s, 8s... as attempts increase. `min(cap, ...)` caps that so it never grows past, say, 30s. `random.uniform(0, X)` is the actual jitter — instead of waiting exactly X seconds, each caller independently picks a random point between 0 and X, so simultaneous failures retry at spread-out, non-matching moments instead of all at once.*

Without jitter, every client that failed during the same incident retries at the same instant and you re-create the outage yourself. This is the most common self-inflicted production failure in existence.

**The idempotency key for agent work:**

```
key = sha256(f"{run_id}:{step_index}:{tool_name}:{canonical_json(args)}")
```

Store `(key, status, result, created_at)` in Postgres with a unique index on `key`. The flow is: insert-or-conflict → if a completed row exists, return its result → otherwise execute, then write the result. `INSERT ... ON CONFLICT DO NOTHING` gives you the mutual exclusion for free.

**What to retry and what not to:**

| Situation | Retry? |
|---|---|
| HTTP 429, 500, 502, 503, 504, connection reset | Yes, with backoff + jitter |
| HTTP 400, 401, 403, 422 | No, it will fail identically |
| Timeout on a *read* | Yes |
| Timeout on a *write* (send email, create ticket) | Only through the idempotency gateway |
| LLM returned malformed JSON | Yes, but as a *new* call with a repair prompt, and cap at 2 |

## AI/Agent relevance

This session fixes the two most expensive bugs in agent systems:

**Duplicate side effects.** A worker crashes after calling `send_invoice()` but before checkpointing. The queue redelivers. The customer gets two invoices. The fix is not "better queues" — it is an idempotent tool gateway. Every write-capable tool goes through it.

**Retry-driven cost explosions.** An LLM provider degrades. Your naive `for attempt in range(5)` retries every call five times with no jitter and no budget. You pay for 5x tokens on the failing calls, the provider rate-limits you harder, more calls fail, and the loop feeds itself. Retry budgets and circuit breakers are cost controls, not just reliability controls.

**LLM-specific gotchas:**
- Streaming responses can fail halfway. Decide up front: discard partial and retry, or keep partial and continue. Store the partial either way.
- Providers charge for tokens on requests that time out on your side but complete on theirs.
- Structured-output failures are semantic, not transport. Handle them in a different code path from HTTP errors.

## Architecture example

```
agent step
    │
    ▼
┌─────────────────────────────────────────────┐
│  Tool / LLM Gateway                         │
│  1. compute idempotency key                 │
│  2. SELECT from dedup table  ──► hit? return│
│  3. circuit breaker open?    ──► fail fast  │
│  4. rate limiter token available?           │
│  5. call with deadline                      │
│  6. on failure: classify → retry w/ jitter  │
│     within budget, else DLQ                 │
│  7. persist (key, result, tokens, cost)     │
└─────────────────────────────────────────────┘
    │
    ▼  external API
```

Everything the agent does that touches the outside world goes through this one component. It is 200 lines and it is the most valuable code in the system.

## Hands-on exercise (55 min)

Build `core/llm.py` and `core/tools.py` in `mini-aios`:

1. `call_llm(messages, *, idem_key, deadline_s)` with: deadline, classify-then-retry, full jitter, max 3 attempts, retry budget counter, circuit breaker per provider.
2. A Postgres `idempotency` table with a unique index; return cached results on repeat.
3. A fake tool `send_email(to, body)` that appends to a file (your observable side effect).
4. **The proof test:** run a 3-step agent that sends an email in step 2. `kill -9` the process between the email and the checkpoint. Restart. Assert the file has exactly one line. If it has two, your gateway is wrong — fix it before moving on.
5. Add a fault-injection flag that makes the LLM call fail 50% of the time with 429, and confirm your backoff curve in the logs.

## Best resources

- **AWS Builders' Library: "Timeouts, retries, and backoff with jitter"** — Marc Brooker. Read this twice. It is the canonical treatment.
- **Marc Brooker's blog** (`brooker.co.za/blog`) — especially posts on retries, timeouts, and "Fixing retries with token buckets."
- **MIT 6.824 Lecture 2, "RPC and Threads"** — video: `youtube.com/watch?v=gA4YXUJX7t8` (Spring 2020 recording; the current in-person course has no public video, but the material is the same). Notes: `pdos.csail.mit.edu/6.824/notes/l-rpc.txt`. Covers the at-most-once/at-least-once discussion.
- **Stripe's idempotency documentation** — the clearest public writeup of idempotency keys as an API contract.
- **Google SRE book, chapter 22, "Addressing Cascading Failures"** — free online.
- **gRPC deadlines documentation** — for deadline propagation as a concrete pattern.

## 15-minute review

From memory: write the full-jitter formula; state the idempotency key components for a tool call; list four HTTP status codes you never retry; explain why exactly-once delivery is impossible but effectively-once execution is achievable.

## Interview / architecture questions

1. Your agent calls `create_refund()`. The call times out. Walk me through exactly what your system does next, including the database writes.
2. A provider starts returning 500s for 20% of requests. With 3 retries and no budget, what happens to your effective request rate and your bill? Show the arithmetic.
3. Why is jitter more important than the backoff curve itself?
4. Design an idempotency key for an agent step whose arguments include a timestamp and a randomly generated request ID. What has to change?
5. When would you deliberately choose at-most-once semantics for an LLM call?

---

# Session 3 — Queues, worker pools, concurrency, and backpressure

## Core concepts

1. **Sync vs. async decision rule:** if p99 work time exceeds ~5–10 seconds, or the work must survive the client disconnecting, it goes on a queue. Agent runs are almost always async; a single fast classification call can stay sync.
2. **Queue semantics:** visibility timeout / lease, ack and nack, redelivery, dead-letter queue, ordering guarantees, at-least-once delivery.
3. **Little's Law:** `L = λ × W` (concurrency = arrival rate × time in system). This one formula sizes your worker pool, your connection pool, and your rate limits.
4. **Concurrency models:** asyncio (thousands of in-flight IO waits, one core), threads (blocking IO, GIL-limited for CPU), processes (true CPU parallelism, high memory cost).
5. **Backpressure and load shedding:** bounded queues, rejecting work early with 429, and shedding low-priority work before high-priority work.
6. **Fairness and multi-tenancy:** per-tenant queues or weighted scheduling so one tenant's 10,000-item batch cannot cause head-of-line blocking for everyone else.

## Practical explanation

**Little's Law worked example, and why it changes your infra bill:**

> 1,000 agent runs per hour, each run spends 45 seconds waiting on LLM calls.
> λ = 1000/3600 = 0.28 runs/sec. W = 45 s.
> **L = 0.28 × 45 ≈ 12.5 concurrent runs.**

Twelve. Not 1,000. A single Python process running asyncio holds 12 in-flight HTTP requests without noticing. People provision 20 pods for this workload; two would do, and the actual ceiling is the provider's tokens-per-minute limit, not your CPU.

**The rule for agent workloads: they are IO-bound, so scale concurrency inside a process before you scale processes.** Use `asyncio` with a semaphore. Add processes only for genuinely CPU-bound work (document parsing, local embeddings, image processing) or to spread across cores.

**Broker selection:**

| Need | Use |
|---|---|
| Simplest thing that is durable and transactional with your data | **Postgres `SELECT ... FOR UPDATE SKIP LOCKED`** |
| Low latency, high throughput, you already run Redis | **Redis Streams** (consumer groups, acks, pending list) |
| Managed, no ops, at-least-once, DLQ built in | **SQS** |
| Complex routing, priorities, per-message TTL | **RabbitMQ** |
| Event log, replay, many consumers, high volume | **Kafka** |

For an AI OS at startup or mid-market scale, **Postgres SKIP LOCKED is genuinely the right answer far longer than people expect** — it gives you transactional enqueue with your state writes (see the outbox pattern in session 5) and zero new infrastructure. Move to Redis Streams or SQS when queue throughput or polling load actually shows up in your Postgres metrics.

**Backpressure done right:**

```
if queue_depth > HARD_LIMIT:      reject with 429 + Retry-After
elif queue_depth > SOFT_LIMIT:    reject low-priority tenants only
else:                             accept
```

An unbounded queue is not resilience. It is an unbounded latency commitment plus an eventual out-of-memory event.

## AI/Agent relevance

**"How do I run thousands of agents?"** The question contains a category error. An agent is not a process. **Agents are rows in a database plus messages in a queue.** A worker picks up a message, loads the agent's state, executes one step, checkpoints, and either finishes or re-enqueues. Ten thousand "agents" can exist on three worker pods, because at any instant only a few dozen are actually executing.

Provision by **token throughput**, not agent count:

```
required TPM = runs/min × avg tokens/run
workers needed = ceil(concurrent_runs / per_worker_concurrency)
concurrent_runs = Little's Law
hard ceiling = provider TPM/RPM limits
```

**Per-provider rate limiting** belongs in a shared place (Redis token bucket), not inside each worker, or your 20 workers will each think they have the full quota.

**Fairness:** give each tenant its own logical queue or a per-tenant in-flight cap. Without it, one customer's overnight bulk run blocks every interactive request in the system, which is the most common "the product feels broken" complaint in multi-tenant AI platforms.

## Architecture example

```
                   ┌──── per-tenant token buckets (Redis) ────┐
                   │                                           │
Client ──▶ API ──▶ admission control ──▶ queue(s) ──▶ workers ─┴─▶ provider limiter ──▶ LLM
             │        (429 if full)        │            │
             │                             │            └─▶ nack → retry queue → DLQ
             └─▶ 202 Accepted + run_id     │
                                    priority: interactive | batch
```

Two queues minimum: `interactive` and `batch`. Workers poll interactive first. This single decision solves 80% of perceived-latency complaints.

## Hands-on exercise (55 min)

1. Implement `core/queue.py` on Postgres with `SKIP LOCKED`: `enqueue`, `claim(worker_id, lease_s)`, `ack`, `nack(retry_after)`, `reap_expired_leases`, and a `dead_letter` table.
2. Build an async worker that runs N concurrent tasks via a semaphore.
3. **Measure it.** Load-test with a fake LLM that sleeps 2 seconds. Run with per-worker concurrency of 1, 10, 50, 200. Plot throughput and p99 latency. Find the knee. Compare the result to what Little's Law predicted.
4. Add a Redis token-bucket limiter shared across workers; verify that total request rate stays under the limit with 3 workers running.
5. Add bounded-queue admission control and confirm the API returns 429 under overload instead of collapsing.
6. Add a second priority queue and demonstrate that a 5,000-item batch does not delay an interactive run by more than a few seconds.

## Best resources

- **DDIA, chapter 11** ("Stream Processing") — for log-based vs. message-broker semantics.
- **AWS Builders' Library: "Using load shedding to avoid overload"** and **"Fairness in multi-tenant systems"** — both directly applicable to your work, both free.
- **"Little's Law"** — any queueing-theory primer; you need the formula and the intuition, not the proofs.
- **PostgreSQL docs on `SKIP LOCKED`**, plus any of the well-known "Postgres as a job queue" writeups.
- **Redis Streams documentation** — consumer groups, `XAUTOCLAIM`, pending entries list.
- **Python asyncio docs** on semaphores, `gather`, `TaskGroup`, and cancellation.

## 15-minute review

From memory: state Little's Law and compute worker count for 5,000 runs/hour at 60 s each; name three backpressure mechanisms; explain why an unbounded queue is a bug.

## Interview / architecture questions

1. A client says "we need to run 5,000 agents." What five questions do you ask before sizing anything, and what does the answer to each change?
2. When would you choose Kafka over SQS or Postgres for an agent workload, and what does it cost you operationally?
3. Your queue depth is growing steadily and workers are at 8% CPU. Diagnose it. What are the three most likely causes?
4. Describe head-of-line blocking in a multi-tenant agent platform and two different ways to prevent it.
5. Why does adding more worker pods sometimes make latency *worse* for an LLM-bound workload?

---

# Session 4 — State, checkpointing, and durable execution

## Core concepts

1. **Stateless services, stateful workflows.** Your processes should hold no state that matters. All durable state lives in Postgres, Redis, or object storage, so any worker can pick up any run.
2. **Checkpointing:** persist run state at step boundaries so a crash costs you one step, not the whole run.
3. **Event sourcing vs. state snapshots.** Snapshot = "here is the current state." Event log = "here is everything that happened." Agents want both: an append-only step log for audit/debug/replay, and a snapshot for fast resume.
4. **Durable execution** (Temporal, Restate, DBOS, and in a lighter form LangGraph checkpointers): the workflow's progress is persisted so that after a crash, execution resumes from the last completed step rather than the beginning. Requires determinism in the orchestration code.
5. **Sagas and compensation:** multi-step operations with side effects cannot roll back atomically, so each step gets a compensating action (`refund` compensates `charge`).
6. **Workflow versioning:** a run that started on graph v1 must not resume into graph v2 with different node names. Version the graph and pin each run.

## Practical explanation

**Where state actually goes:**

| State | Lives in | Lifetime |
|---|---|---|
| Run status, current step, retry count | Postgres `runs` table | forever (or archive) |
| Message history / graph state | Postgres JSONB or object storage if large | run lifetime + audit window |
| Step log (input, output, tokens, cost, latency) | Postgres `steps` append-only table | audit window |
| Ephemeral locks, rate-limit counters, caches | Redis | seconds to hours |
| Large artifacts (PDFs, images, scraped HTML) | S3/R2, with URLs in Postgres | per policy |
| Long-term agent memory | Vector DB + Postgres | forever |

**Checkpoint granularity is a cost/safety tradeoff:**

| Granularity | Crash cost | Write cost |
|---|---|---|
| Per run (start/end only) | Redo the entire run — potentially $2 of tokens | 2 writes |
| **Per node/step** | Redo one step | 1 write per step — **the right default** |
| Per token (streaming) | Nothing | very high |

Since one LLM step can cost real money and 30 seconds, per-step checkpointing pays for itself immediately. A checkpoint write is ~2 ms against a step that took 8,000 ms.

**Determinism is the catch with durable execution.** If your workflow code calls `datetime.now()`, `random()`, or an unguarded API on replay, the replay diverges from history. Durable execution frameworks solve this by making all non-determinism go through the framework. LangGraph's checkpointer is simpler: it stores state at node boundaries rather than replaying, so you get resumability without the strict determinism rules, at the cost of finer control.

## AI/Agent relevance

**LangGraph concretely:** a `thread_id` identifies a run; a checkpointer (`PostgresSaver`, `AsyncPostgresSaver`) writes state after each node; `graph.invoke(input, config={"configurable": {"thread_id": tid}})` resumes automatically from the last checkpoint. `interrupt()` plus a checkpoint is how you get human-in-the-loop pauses that can last for days. This is the mechanism that lets you kill every worker pod during a deploy and lose nothing.

**Three kinds of "memory" people conflate:**
1. **Run state** — the current graph state and message list. Checkpointed, short-lived.
2. **Conversation memory** — history across sessions for one user. Postgres, summarized when it grows.
3. **Long-term/semantic memory** — facts retrieved by similarity. Vector DB, curated.

Keeping them separate is the difference between a system that gets slower and more expensive every week and one that doesn't. Unbounded message history is a token cost bomb: at 30 turns you are paying to resend 40,000 tokens on every step.

**Long-running agents (hours to days):** never hold them in a process. The run is a row with a status and a `next_wakeup_at`. A scheduler enqueues due runs. A worker executes one step and puts it back. This is how you run 10,000 long-lived agents on 3 pods.

## Architecture example

```
┌──────────────────────────────────────────────────────────┐
│ runs      (id, tenant, graph_version, status, state_ref,  │
│            next_wakeup_at, lease_owner, lease_expires)    │
│ steps     (run_id, idx, node, input, output, tokens,      │
│            cost, latency_ms, created_at)   ← append-only  │
│ idempotency (key, result, created_at)                     │
└──────────────────────────────────────────────────────────┘
        ▲                                  │
        │ checkpoint after each node        │ resume: load state @ last idx
        │                                   ▼
   ┌─────────────────────────────────────────────┐
   │ Worker: claim(lease) → load → execute node  │
   │ → checkpoint → re-enqueue or complete       │
   └─────────────────────────────────────────────┘
```

Crash recovery: the lease expires, a reaper marks the run claimable, another worker loads the checkpoint and continues from step k. Combined with session 2's idempotency gateway, a re-executed step produces no duplicate side effects.

## Hands-on exercise (55 min)

1. Add `runs` and `steps` tables to `mini-aios` and implement `save_checkpoint(run_id, idx, state)` / `load_checkpoint(run_id)`.
2. Build a 5-node LangGraph agent (or a hand-rolled state machine) where node 3 calls the `send_email` tool from session 2.
3. **Crash test:** `kill -9` the worker during node 3. Restart. Verify (a) the run resumes at node 3, not node 1, (b) exactly one email exists, (c) `steps` shows the retry.
4. Implement a saga: nodes `reserve → charge → confirm`, with compensations `release` and `refund`. Force `confirm` to fail and verify compensations run in reverse order.
5. Add `graph_version` to runs and write the code path that refuses to resume a v1 run on a v2 graph (fail loudly rather than corrupt silently).
6. Add a `next_wakeup_at` scheduler loop and prove a run can sleep for 60 seconds and wake up in a different worker process.

## Best resources

- **LangGraph persistence documentation** — checkpointers, threads, `interrupt`, time travel. Read the whole persistence section.
- **Temporal docs: "Workflows" and "Determinism"** — even if you never adopt Temporal, this is the clearest explanation of durable execution as a concept.
- **Pat Helland, "Life Beyond Distributed Transactions: An Apostate's Opinion"** — a genuinely important paper about designing systems without distributed transactions. Short and readable.
- **Sagas** — Garcia-Molina and Salem, 1987; or any modern writeup of the saga pattern with compensations.
- **DDIA, chapter 7** ("Transactions") and **chapter 11** (log-based state).
- **MIT 6.5840 Lecture on fault-tolerant VMs / primary-backup** — for the replay-vs-snapshot intuition.

## 15-minute review

From memory: describe the three kinds of agent memory and where each is stored; explain per-step checkpointing's cost/benefit; state the two conditions required for a crashed run to resume safely.

## Interview / architecture questions

1. A research agent runs for 3 hours across 60 LLM calls. A pod is evicted at minute 100. Describe exactly what your system loses and what it recovers.
2. Why does durable execution require determinism, and how do LangGraph-style checkpointers sidestep that requirement? What do you give up?
3. You deploy a new version of the agent graph while 400 runs are mid-flight. What breaks, and what are your three options?
4. Design the compensation logic for an agent that books a flight, a hotel, and a car, where the hotel booking fails.
5. A conversation reaches 200 turns. Describe what happens to latency and cost, and three strategies to fix it with their tradeoffs.

---

# Session 5 — The data layer: Postgres, Redis, vector stores, and the outbox pattern

## Core concepts

1. **Choose storage by access pattern**, not by hype. Most AI OS state is relational and small.
2. **Transactions and isolation:** Read Committed (Postgres default) permits lost updates; `SELECT ... FOR UPDATE` or an optimistic `version` column fixes concurrent state mutation.
3. **The dual-write problem and the outbox pattern:** you cannot atomically write to Postgres and publish to a queue. Write the job into an outbox table in the same transaction, then have a poller publish it.
4. **Connection pooling:** Postgres costs several MB per connection and degrades past a few hundred. 40 workers × 20 connections = an outage. Use pgbouncer or a small pool with high async concurrency.
5. **Partitioning and hot keys:** partition big append-only tables (steps, events) by time; watch for tenant-level hot partitions.
6. **Read replicas and replication lag:** a read-after-write against a replica can return stale data. Route reads that must be fresh to the primary.
7. **Vector storage decisions:** pgvector vs. a dedicated vector database, and the thresholds that actually matter.

## Practical explanation

**The default data layer for an AI OS, and when to break it:**

| Store | Use for | Break out when |
|---|---|---|
| **Postgres** | runs, steps, dedup, outbox, queue, tenants, prompts, evals, pgvector embeddings | >20–30k writes/sec, or vectors in the tens of millions |
| **Redis** | cache, rate limits, leases, ephemeral streams, pub/sub | you need durability guarantees Redis doesn't offer |
| **Object storage** | documents, images, large agent artifacts, raw scrapes | basically never |
| **Dedicated vector DB** | >5–10M vectors, heavy metadata filtering, frequent full reindexing, separate scaling | not before then |

**pgvector reality check:** with HNSW indexing it handles low single-digit millions of vectors at acceptable latency on decent hardware, and it gives you transactional consistency between your embeddings and your relational data, which is worth a great deal operationally. Moving to a dedicated vector store buys you scale and index flexibility and costs you a second system to keep in sync. Do it when the numbers force you, not before.

**The outbox pattern, which you should implement once and reuse forever:**

```sql
BEGIN;
  UPDATE runs SET state = $1, step_idx = $2 WHERE id = $3;
  INSERT INTO outbox (topic, payload) VALUES ('run.step_ready', $4);
COMMIT;
-- separate poller: SELECT ... FROM outbox WHERE published_at IS NULL
--                  FOR UPDATE SKIP LOCKED → publish → mark published
```

Without this, the classic bug is: state saved, process crashes, message never enqueued, run stalls forever with status `running`. This bug is invisible until you have a few thousand runs, at which point 0.5% of runs are silently stuck.

**Optimistic concurrency for agent state:**

```sql
UPDATE runs SET state = $1, version = version + 1
WHERE id = $2 AND version = $3;
-- 0 rows updated → someone else wrote first → reload and retry
```

Cheaper than locking and correct under contention, which matters when a user cancels a run while a worker is mid-write.

## AI/Agent relevance

- **RAG index freshness** is a consistency problem. Decide explicitly: is your index eventually consistent (batch reindex, minutes to hours stale) or read-your-writes (write document → immediately searchable)? The second requires transactional embedding writes, which is a strong argument for pgvector.
- **Per-tenant isolation:** row-level with a `tenant_id` and enforced filters is right for most B2B AI products. Schema-per-tenant is defensible at low tenant counts with strong compliance requirements. Database-per-tenant is an operational tax you should charge for.
- **The `steps` table grows fast.** 1,000 runs/day × 10 steps × full prompt/response is gigabytes per month. Partition by month, move payloads to object storage, keep metadata in Postgres.
- **Connection math for async workers:** an asyncio worker with 200 concurrent runs does *not* need 200 connections. It needs enough for actual DB round trips, which are 2 ms out of an 8,000 ms step. Ten connections is usually plenty. Getting this wrong is the most common way people take down their own Postgres.

## Architecture example

```
┌── Postgres (primary) ─────────────────────────────┐
│  runs · steps · idempotency · outbox · queue      │
│  tenants · prompts · evals · embeddings(pgvector) │
└──────┬───────────────────────────────┬────────────┘
       │ logical replication            │ FOR UPDATE SKIP LOCKED
       ▼                                ▼
  read replica                     outbox poller ──▶ Redis Streams / workers
  (analytics, dashboards)
       
Redis: cache · rate limits · leases · hot counters
S3/R2: documents · artifacts · large step payloads
```

One database until the numbers say otherwise. That is not laziness; consistency between your agent state and your job queue is a feature worth protecting.

## Hands-on exercise (55 min)

1. Implement the outbox pattern in `mini-aios`: transactional state-update + outbox insert, plus a poller with `SKIP LOCKED`. Kill the process between commit and publish, and verify the poller still delivers.
2. **Reproduce a lost update:** two workers write agent state concurrently under Read Committed. Observe data loss. Fix with a `version` column and retry loop. Verify.
3. Load 100k embeddings into pgvector. Measure query p50/p99 with no index, with IVFFlat, and with HNSW. Record build time and memory for each.
4. Deliberately exhaust the connection pool (set pool size to 5, run 50 concurrent requests) and observe the failure mode. Then fix it and note the difference between "pool exhausted" and "database overloaded."
5. Add monthly partitioning to the `steps` table and move payloads >32KB to local object storage with a URL reference.

## Best resources

- **DDIA, chapters 5, 6, 7** (replication, partitioning, transactions) — the core of the book for this session.
- **PostgreSQL documentation:** transaction isolation, `SELECT FOR UPDATE`, `SKIP LOCKED`, advisory locks, table partitioning.
- **pgvector README** — index types, tuning parameters, and the honest tradeoffs between IVFFlat and HNSW.
- **"Choose Boring Technology"** — Dan McKinley. Read it before your next stack decision.
- **pgbouncer documentation** — transaction vs. session pooling modes.
- **Jepsen analyses** (`jepsen.io/analyses`) — pick any two databases you use and read what actually broke. This calibrates your trust in vendor claims permanently.
- **"Life Beyond Distributed Transactions"** (again — it lands differently after this session).

## 15-minute review

From memory: draw the outbox pattern and explain what it prevents; state the pgvector-vs-dedicated threshold and the two non-scale reasons to switch; explain why 40 workers × 20 connections is a bug.

## Interview / architecture questions

1. You save agent state and then publish a "next step" message. The process dies in between. What is the user-visible symptom, and what are your two fix options?
2. When would you move from pgvector to a dedicated vector database? Give thresholds, not adjectives.
3. Two workers claim the same run because of a lease race. Describe three independent layers of defense.
4. Your RAG system must make an uploaded document searchable within 2 seconds. Which architectural choices does that requirement eliminate?
5. Your `steps` table is 400 GB and queries have degraded. Give me a migration plan with no downtime.

---

# Session 6 — Replication, consistency, and coordination (the useful 20% of theory)

## Core concepts

1. **Replication:** single-leader (default and almost always correct), multi-leader (multi-region writes, conflict resolution required), leaderless/quorum (Dynamo-style).
2. **Replication lag anomalies:** read-your-writes, monotonic reads, consistent prefix reads. These are the bugs users actually report.
3. **The consistency spectrum:** linearizable → sequential → causal → eventual. Know where each of your reads must sit.
4. **CAP and PACELC:** during a partition you choose consistency or availability; *even when there is no partition*, you choose latency or consistency. PACELC is the more useful framing day to day.
5. **Consensus (Raft):** leader election, log replication, majority quorum, why an even number of nodes is a mistake. You need enough to reason about it, not to implement it.
6. **Leases and fencing tokens:** a lock with an expiry, plus a monotonically increasing token that lets the resource reject writes from a lock holder whose lease already expired.
7. **Clocks:** never order distributed events by wall-clock time. Clock skew is real, NTP jumps, and `now()` on two machines disagrees.

## Practical explanation

**The single most useful conclusion of this session: you almost never need to implement consensus. You need to *use* it.** Raft is running inside etcd (and therefore Kubernetes), inside your managed database's failover logic, inside Temporal. Your job is to know when you need a coordination primitive and to borrow one that is already correct.

**Distributed locking, ranked by how much I'd trust it for an AI OS:**

| Approach | Verdict |
|---|---|
| Postgres advisory lock or `SELECT FOR UPDATE` on a row | Best default. Correct, transactional, no new infra |
| Kubernetes `Lease` object (etcd-backed) | Correct for leader election of singleton controllers |
| Redis single-instance lock with TTL + fencing token | Fine when duplicate work is *inefficient* but not *incorrect* |
| Redlock across N Redis nodes | Contested; Kleppmann's critique is worth reading before adopting |
| No lock, idempotency instead | **Frequently the best answer of all** |

That last row matters. If step execution is idempotent (session 2) and you have a dedup table, duplicate delivery costs you a wasted LLM call, not a corrupted system. Design so that correctness does not depend on mutual exclusion, and use leases only to reduce waste.

**Why you must never sort agent events by wall clock:** two workers, clocks 300 ms apart, both write step records. Your "ordered" step log is wrong, your debugging is wrong, and your evals are wrong. Use a monotonic per-run `step_idx` from the database instead.

## AI/Agent relevance

- **"Only one worker should process this run":** implement as `UPDATE runs SET lease_owner=$1, lease_expires=now()+interval '5 min' WHERE id=$2 AND (lease_owner IS NULL OR lease_expires < now()) RETURNING *`. Zero rows means someone else has it. This is a lease, done correctly, in one statement.
- **Singleton schedulers:** your cron/wakeup scheduler must not run in all 3 API replicas. Use a K8s Lease or a Postgres advisory lock for leader election, and make the scheduler's work idempotent anyway.
- **Distributed rate limiting across workers** requires coordination and therefore latency. In practice: a shared Redis token bucket with a small local burst allowance, accepting slight overshoot. Perfect global rate limiting is not worth the round trip.
- **Multi-region agent memory:** if you replicate a vector store across regions, you have accepted eventual consistency for retrieval. Decide whether a stale retrieval is acceptable (usually yes) and document it.
- **Cache/DB consistency for RAG:** when a document is deleted, the embedding must go too. Deletes are the invalidation case people forget, and they are the one with legal consequences.

## Architecture example

```
Leader election for the scheduler (3 API replicas, only 1 active):

  replica A ──┐
  replica B ──┼──▶ etcd/K8s Lease ──▶ holder runs scheduler loop
  replica C ──┘        (renew every 5s, TTL 15s)

Lease-based run claiming (no global lock needed):

  worker ──▶ UPDATE runs SET lease_owner=me, lease_expires=now()+5min
             WHERE id=$1 AND (lease_expires IS NULL OR lease_expires < now())
             RETURNING id       ──▶ 0 rows = someone else owns it

  reaper ──▶ every 60s: runs WHERE lease_expires < now() AND status='running'
             → make claimable, increment attempt counter, DLQ after N
```

## Hands-on exercise (55 min)

1. Implement lease-based run claiming plus a reaper in `mini-aios`. Prove with two workers that a run is never executed concurrently under normal conditions.
2. **Break it on purpose:** pause worker A with `SIGSTOP` after it claims a run, let the lease expire, let worker B claim and complete it, then `SIGCONT` A and let it write. Observe the corruption. Fix it with a fencing token: the write must include the lease generation, and the update must fail if the generation is stale.
3. Implement leader election using a Postgres advisory lock; start three processes; kill the leader; measure failover time.
4. Reproduce a replication-lag bug: write to primary, immediately read from a replica, observe stale data. Implement read-your-writes routing.
5. Spend 20 minutes on the Raft visualization at `raft.github.io` — trigger elections, partition nodes, watch log replication. Write down in your own words why a majority quorum prevents split-brain.

## Best resources

- **MIT 6.5840 Lectures 5–8 (Raft)** plus **the Raft extended paper** ("In Search of an Understandable Consensus Algorithm"). Watch the lectures; skim the paper for the election and log-matching rules.
- **`raft.github.io`** — the interactive visualization. Twenty minutes here beats two hours of reading.
- **Martin Kleppmann, "How to do distributed locking"** — the Redlock critique and the fencing-token argument. Essential and short.
- **DDIA, chapters 5 and 9** — replication and consistency/consensus. Chapter 9 is dense; read it after the Raft lectures, not before.
- **Jepsen analyses** — again, but this time read the consistency-model discussions.
- **Kubernetes `Lease` API documentation** and the `leaderelection` package docs.

## 15-minute review

From memory: write the lease-claim SQL; explain fencing tokens in two sentences; state the difference between CAP and PACELC; explain why you should not order agent events by timestamp.

## Interview / architecture questions

1. Your scheduler runs in 3 replicas and fires duplicate jobs. Give me two fixes, one using coordination and one avoiding it, and say which you would ship.
2. Explain to a junior engineer why a Redis lock with a TTL can be held by two processes at once, and what a fencing token does about it.
3. Your vector index is replicated to three regions. A user uploads a document in Frankfurt and searches from Singapore two seconds later. What do they see, and how would you change that?
4. When is eventual consistency clearly acceptable in an AI OS, and where is it clearly not?
5. Why is an even number of Raft nodes a bad idea?

---

# Session 7 — Caching and cost engineering (the session that pays for the other nine)

## Core concepts

1. **Cache hierarchy and hit-rate math.** Effective cost = `(1 − hit_rate) × miss_cost + hit_rate × hit_cost`. A 40% hit rate on a $0.03 call is a 40% bill reduction, which no amount of infrastructure tuning will match.
2. **Invalidation strategies:** TTL, write-through, and versioned keys (`v{schema_version}:{model}:{hash}`) so a prompt or model change invalidates everything implicitly.
3. **Cache stampede** (dogpile): a popular key expires, 500 requests miss simultaneously, all 500 hit the LLM. Fix with single-flight locks or probabilistic early expiration.
4. **Exact vs. semantic caching.** Exact = hash of the normalized request. Semantic = embedding similarity above a threshold. Semantic caching returns wrong answers if your threshold is too loose; treat the threshold as a tunable with a measured false-hit rate.
5. **Provider prompt caching (prefix caching):** providers cache the KV state of a shared prompt prefix, giving large discounts on cached input tokens. Requires a **stable prefix**: static system prompt and tool definitions first, dynamic content last.
6. **Batching:** provider batch APIs typically trade latency (hours) for roughly half the price. Embeddings batch trivially.
7. **Model routing / cascades:** a small cheap model attempts first, escalating to a large model only on low confidence or validation failure.
8. **Token economics:** cost per run, cost per tenant, and the context-growth problem.

## Practical explanation

**The cost stack, in the order you should attack it:**

| Lever | Typical saving | Effort |
|---|---|---|
| Stop resending full history every step (summarize/window) | 30–70% | low |
| Provider prompt caching with a stable prefix | 40–90% on input tokens | low |
| Exact-match cache on repeated calls | 10–40% | low |
| Model routing (small first, escalate) | 30–80% | medium |
| Batch API for non-interactive work | ~50% | medium |
| Semantic cache | 10–40% | medium, with accuracy risk |
| Self-hosting open models | varies wildly | high |
| Infrastructure right-sizing | 5–20% of a much smaller number | medium |

Note the ordering. **Infrastructure is usually the smallest line item in an AI OS.** A pod costs cents per hour; an agent run with 8 large-model calls costs 20–80 cents. Optimize tokens first, always. When a client asks you to reduce cloud spend, the honest answer is usually "your cloud bill is 8% of the problem."

**Prompt layout for prefix caching:**

```
[ static system prompt        ]  ← cacheable
[ tool definitions            ]  ← cacheable
[ few-shot examples           ]  ← cacheable
[ retrieved documents         ]  ← sometimes cacheable per session
[ conversation history        ]  ← grows, breaks cache below this point
[ current user message        ]  ← never cacheable
```

Anything that changes invalidates everything after it. Putting a timestamp or a request ID at the top of your system prompt destroys your entire cache and is a real, common, expensive mistake.

**Single-flight, in ten lines:**

```python
async def get_or_compute(key, compute):
    if (hit := await cache.get(key)) is not None:
        return hit
    lock = await redis.set(f"lock:{key}", worker_id, nx=True, ex=30)
    if not lock:                       # someone else is computing
        return await wait_for_key(key, timeout=30) or await compute()
    try:
        val = await compute()
        await cache.set(key, val, ex=TTL)
        return val
    finally:
        await redis.delete(f"lock:{key}")
```

**Break-even for self-hosting:** an always-on GPU instance costs roughly $1–3/hour depending on the card and provider. Compare that against your monthly API spend for the specific workload, and remember that self-hosting adds ops burden, model quality tradeoffs, and utilization risk. The math usually favors self-hosting only for high-volume, latency-tolerant, small-model workloads such as embeddings, classification, and reranking. Those three are exactly where you should look first.

## AI/Agent relevance

**Build a cost ledger before you build a cache.** Every step writes `(run_id, tenant_id, model, input_tokens, output_tokens, cached_tokens, cost_usd, latency_ms)`. Without this table you are guessing, and you will optimize the wrong thing. With it you can answer "which tenant, which agent, which node is expensive" in one SQL query, and that query is the highest-value dashboard in the system.

**Per-tenant budgets** belong in the same place as rate limits: a Redis counter checked at admission time, with a hard monthly cap and a soft alert threshold. This is a product feature as well as a safety mechanism.

**Semantic caching caution for agents:** caching a *tool result* by semantic similarity is dangerous (yesterday's stock price is similar to today's). Caching a *reasoning step* by semantic similarity is dangerous in a different way (a nearly identical prompt can require a different answer because of context). The safest high-value uses are: FAQ-style user queries, embedding lookups, document summaries, and classification.

## Architecture example

```
request
  │
  ├─▶ normalize (strip whitespace, sort keys, drop volatile fields)
  │
  ├─▶ L1: in-process LRU        (µs)    ──hit──▶ return
  ├─▶ L2: Redis exact-match     (~1ms)  ──hit──▶ return
  ├─▶ L3: semantic cache        (~20ms) ──hit(>0.95)──▶ return + log for audit
  │
  ├─▶ router: cheap model → validate → escalate if needed
  │
  ├─▶ provider call with stable cacheable prefix
  │
  └─▶ write: L1 + L2 (+L3), and append to cost ledger
```

## Hands-on exercise (55 min)

1. Add a `cost_ledger` table to `mini-aios` and record tokens, cached tokens, and cost for every LLM call. Build one SQL query for cost per tenant per day and one for cost per graph node.
2. Implement L1 (in-process LRU) and L2 (Redis) exact-match caching with versioned keys. Run a 200-request workload with 30% repeated inputs and measure the hit rate, latency, and cost delta.
3. Add single-flight and prove it: fire 50 concurrent identical requests on a cold cache, and verify exactly one upstream call.
4. Restructure one real prompt for prefix caching (static content first). Measure cached-token counts before and after.
5. Implement a two-tier router: small model attempts, a validator checks the output (schema validity or a confidence heuristic), escalate on failure. Measure the escalation rate and the net cost change. Note the accuracy impact honestly.
6. Write `docs/cost-model.md`: cost per run, per 1,000 runs, and per tenant per month, with the levers you just measured.

## Best resources

- **Anthropic and OpenAI documentation on prompt caching and batch APIs** — read the actual docs, since the pricing rules and minimum cacheable lengths are specific and they change.
- **"Scaling Memcache at Facebook"** (NSDI 2013) — the canonical paper on caching at scale, including stampede handling and lease mechanisms. Still the best thing written on the subject.
- **"Optimal Probabilistic Cache Stampede Prevention"** (Vattani, Chierichetti, Lowenstein) — short paper, gives you the XFetch technique.
- **vLLM's PagedAttention paper** — for a genuine understanding of what KV caching is and why prefix stability matters.
- **GPTCache** (open source) — read the code for semantic caching design, whether or not you use it.
- **AWS Builders' Library: "Caching challenges and strategies"**.

## 15-minute review

From memory: list the cost levers in priority order; explain why a timestamp in the system prompt is expensive; describe single-flight; state the effective-cost formula.

## Interview / architecture questions

1. A client's LLM bill is $14,000/month. Walk through your diagnostic process in order, and say what you expect to find first.
2. When is semantic caching actively dangerous? Give two concrete examples from agent systems.
3. Your cache hit rate is 8% and you expected 40%. List five plausible causes.
4. Explain prefix caching to a non-technical founder in three sentences, then explain the one prompt-engineering rule it implies.
5. A client wants to self-host to save money. What five numbers do you need before you can answer, and what is your prior?

---

# Session 8 — Scheduling, isolation, and resource allocation

## Core concepts

1. **Containers as resource and isolation boundaries:** namespaces (what you can see) and cgroups (what you can use). A container is not a security boundary against hostile code.
2. **Kubernetes resource model:** `requests` drive scheduling and cost, `limits` drive throttling and OOMKills. Memory over-limit is a kill; CPU over-limit is throttling, which shows up as mysterious latency.
3. **Bin packing:** the scheduler places pods by requests. Over-requesting is the single most common way to double a Kubernetes bill.
4. **Autoscaling:** HPA on CPU is wrong for IO-bound agent workers. **KEDA on queue depth or queue latency is right.** Cluster autoscaler adds nodes; it is slower than pod scaling (typically tens of seconds to minutes).
5. **Scale-to-zero and cold starts:** worth it for spiky, latency-tolerant workloads; painful when a cold start includes loading a model.
6. **Ray:** a distributed execution framework with tasks (stateless functions) and actors (stateful workers), a global control store, and placement groups. It shines for fan-out compute, stateful GPU workers, and data-parallel workloads.
7. **GPU allocation:** whole-GPU-per-pod by default, with MIG (hardware partitioning), time-slicing (context switching, no memory isolation), and MPS as sharing options. Continuous batching in the inference server is what actually drives utilization.
8. **Isolation levels for agent execution**, from shared thread to microVM.

## Practical explanation

**The isolation ladder — choose the lowest rung that meets your requirement:**

| Rung | Mechanism | Startup | Use when |
|---|---|---|---|
| 1 | Shared async worker, tenant-tagged tasks | µs | **Default.** Trusted code, normal multi-tenant SaaS |
| 2 | Separate worker pool per tier or per noisy tenant | seconds | Noisy-neighbor or compliance separation |
| 3 | Container per run (K8s Job) | 2–20 s | Heavy native deps, per-run resource caps |
| 4 | gVisor / Kata / Firecracker microVM | 100 ms – 2 s | **Executing untrusted or LLM-generated code** |
| 5 | Dedicated node/cluster per tenant | minutes | Contractual isolation, sovereign data |

The mistake to avoid: jumping to rung 3 or 4 for everything because "agents should be isolated." Isolation costs startup latency, memory overhead, and scheduling complexity. Use rung 4 exactly where it belongs, which is running code an LLM wrote.

**The "agents are data, not processes" principle, quantified:**

| Design | 10,000 agents |
|---|---|
| One pod per agent | ~10,000 × 256 MB = 2.5 TB RAM. Absurd |
| One process per agent | thousands of processes, ~50 MB each. Still absurd |
| **Rows + queue + shared async workers** | ~3–10 pods, a few GB total. Correct |

**Resource requests you can actually defend:** measure first. Run a load test, look at p95 CPU and max memory, then set `requests` at roughly p95 usage and `limits` at 1.5–2x for memory. For CPU-bound-but-bursty workloads, consider setting no CPU limit (requests only) to avoid throttling, while keeping memory limits strict.

**KEDA scaling on queue depth:**

```yaml
triggers:
  - type: postgresql          # or redis-streams, aws-sqs
    metadata:
      query: "SELECT count(*) FROM queue WHERE status='pending'"
      targetQueryValue: "20"   # ~20 pending items per replica
```

This is the single most useful autoscaling change for agent infrastructure. CPU-based HPA will keep your workers at 1 replica while 4,000 jobs pile up, because waiting on an HTTP response uses no CPU.

**When Ray beats queue + workers:** stateful GPU actors you want to keep warm; fan-out/fan-in over large in-memory data; dynamic task graphs; distributed batch inference. **When it doesn't:** ordinary web-scale agent orchestration where a queue and stateless workers are simpler, cheaper to operate, and easier to debug. Adding Ray adds a cluster to run. Have a reason.

## AI/Agent relevance

**Answering the client question "should each agent have its own container?"** — the honest decision tree:

```
Does the agent execute code the LLM generated, or untrusted user code?
   YES → microVM/gVisor sandbox pool, one per execution, short-lived
   NO  ↓
Does it need a GPU or >4 GB RAM or heavy native dependencies?
   YES → dedicated pool for that agent type (not per agent instance)
   NO  ↓
Is there a contractual/regulatory isolation requirement?
   YES → per-tenant worker pool (rung 2) or namespace
   NO  ↓
→ Shared async worker pool. Tag tasks by tenant, enforce quotas in software.
```

**GPU allocation for AI OS builders:** if you serve your own models, one inference server (vLLM or similar) per GPU with continuous batching gets far better utilization than N pods each holding a fraction of a GPU. Treat the inference server as a shared service behind your LLM gateway, and let batching happen there. Fractional GPU tricks are for development environments and small models, not for maximizing serving throughput.

## Architecture example

```
┌─ node pool: general (CPU, spot/preemptible ok) ────────────┐
│  api × 2        workers × N  (KEDA: queue depth)           │
│  scheduler × 1 (leader-elected)   outbox poller × 1        │
└────────────────────────────────────────────────────────────┘
┌─ node pool: sandbox (gVisor runtime class, no egress) ─────┐
│  code-exec pods, one per execution, TTL 60s, no secrets    │
└────────────────────────────────────────────────────────────┘
┌─ node pool: gpu (on-demand, taints + tolerations) ─────────┐
│  vLLM/embedding server × M, continuous batching            │
└────────────────────────────────────────────────────────────┘
```

Three pools, three cost profiles, three failure domains. Spot instances for stateless workers with graceful drain, on-demand for GPU, isolated runtime for code execution.

## Hands-on exercise (55 min)

1. Spin up `kind`. Deploy the `mini-aios` API and workers with explicit requests and limits.
2. **Demonstrate CPU throttling:** set `cpu: limits: 100m` on a worker doing a burst of parsing. Observe `container_cpu_cfs_throttled_seconds_total` and the latency impact. Remove the limit and re-measure.
3. **Demonstrate OOMKill:** set a memory limit below actual usage. Watch the pod die. Note which signals told you and which didn't.
4. Install KEDA and scale workers on queue depth. Inject 500 jobs and measure time-to-scale, then time-to-drain. Compare against an HPA on CPU (which should barely react).
5. **Bin-packing math:** with nodes of 4 vCPU / 16 GB, compute how many pods fit at `requests: 500m/1Gi` vs. `1000m/2Gi`, and what each costs monthly at your provider's rate. Write the number in `docs/cost-model.md`.
6. Optional but valuable: run a Ray local cluster, implement a fan-out of 100 embedding tasks as Ray tasks and again as queue jobs. Compare code complexity, latency, and what you'd have to operate.

## Best resources

- **"Large-scale cluster management at Google with Borg"** (EuroSys 2015) — the paper Kubernetes descends from. Read the sections on utilization, bin packing, and job classes.
- **Kubernetes documentation:** "Managing Resources for Containers," QoS classes, and the HPA walkthrough. Read the QoS classes page carefully; it explains eviction order.
- **KEDA documentation** — scalers list plus the concept of the scaling modifier and cooldown.
- **Ray documentation: "Ray Core architecture"** and the Ray paper (OSDI 2018, "Ray: A Distributed Framework for Emerging AI Applications").
- **vLLM documentation and the PagedAttention paper** — continuous batching, GPU memory management.
- **NVIDIA docs on MIG and time-slicing**; **gVisor** and **Firecracker** documentation for sandbox design.
- **"Kubernetes Patterns"** (free PDF editions have circulated) or the Google SRE chapters on capacity.

## 15-minute review

From memory: explain requests vs. limits and what happens when each is exceeded; state why HPA-on-CPU fails for agent workers; recite the isolation ladder and name the rung you'd use for LLM-generated code.

## Interview / architecture questions

1. A client wants "one container per agent" for 3,000 agents. Explain why that is wrong and what you would build instead, including the cost comparison.
2. Your worker pods sit at 15% CPU while the queue grows to 8,000. What is misconfigured, and exactly what do you change?
3. When would you actually introduce Ray into an AI OS, and what operational cost are you accepting?
4. You have four GPUs and eleven models to serve. Describe two allocation strategies and their tradeoffs.
5. An agent runs Python code the LLM generated. Design the execution environment: isolation mechanism, network policy, filesystem, timeouts, resource caps, and what you log.

---

# Session 9 — Observability, failure design, and debugging non-determinism

## Core concepts

1. **Traces are the backbone**, not logs. A distributed trace with a `run_id` shows you the whole agent execution across API, queue, worker, tools, and provider in one view. Metrics tell you something is wrong; traces tell you where.
2. **Context propagation across async boundaries:** the trace context must ride inside the queue message, or your trace ends at the enqueue.
3. **RED for services** (Rate, Errors, Duration) and **USE for resources** (Utilization, Saturation, Errors). Two acronyms, most of practical monitoring.
4. **SLOs and error budgets:** define what "working" means numerically, then let the budget decide whether to ship features or fix reliability.
5. **Cardinality discipline:** never put `run_id` or `user_id` in a Prometheus label. Those belong in traces and logs.
6. **Cascading failure prevention:** timeouts, bulkheads (separate pools per dependency), circuit breakers, load shedding, jittered retries. Session 2 and 3 built the parts; this session assembles them.
7. **Debugging non-determinism:** record the exact model, prompt, parameters, seed, tool outputs, and version of every step so you can replay a bad run offline.

## Practical explanation

**The signals an AI OS needs that a normal backend doesn't:**

| Signal | Why it matters |
|---|---|
| Tokens in / out / cached per step | cost attribution, cache effectiveness |
| Cost per run, per tenant, per node | the metric your client cares about most |
| Step count and loop detection | runaway agents are a distinct failure mode |
| Tool error rate by tool | one broken tool degrades every agent silently |
| Time-to-first-token vs. total duration | distinguishes provider slowness from your own |
| Output validation failure rate | your only real-time proxy for quality |
| Eval scores on sampled production traffic | catches semantic regressions no metric will |
| Queue depth and oldest-message age | the leading indicator of every capacity problem |

**Oldest-message age is the best single alert in the system.** Queue depth can be high and healthy; a message that has waited 15 minutes is always a problem.

**SLOs worth defining for an AI OS:**

```
99% of interactive runs complete in < 20 s          (latency)
99.5% of submitted runs eventually complete         (durability of work)
99.9% of API submissions return 2xx or 429, not 5xx (availability)
< 0.1% of runs produce duplicate side effects       (correctness)
```

Note the last one. It is unusual and it is exactly what you built sessions 2 and 4 to guarantee, so measure it.

**Cascading failure, the pattern to recognize:** a dependency slows down → your threads/connections/concurrency slots fill with waiting requests → your service stops responding to *everything*, including healthy paths → health checks fail → Kubernetes restarts pods → in-flight work is lost → the queue grows → the restart storm continues. Every link in that chain is prevented by something you already know: deadlines, bulkheads, load shedding, circuit breakers, and health checks that reflect real health rather than process liveness.

**Replay harness for semantic bugs:** store every step's `(model, model_version, params, rendered_prompt, tool_outputs, response)`. When a user reports "the agent gave a bad answer," you re-run that exact prompt against the same and a new model and compare. Without this you are debugging by anecdote, and agents are too non-deterministic for anecdotes to work.

## AI/Agent relevance

**Trace structure for an agent run:**

```
span: run  (run_id, tenant_id, graph_version)
 ├─ span: node:plan          (model, tokens, cost, cache_hit)
 ├─ span: node:retrieve
 │   └─ span: vector_search  (k, filter, latency, results)
 ├─ span: node:act
 │   ├─ span: tool:http_get  (url_host, status, retries)
 │   └─ span: llm:call       (attempt=2, backoff_ms, 429)
 └─ span: node:respond
```

One trace, and you can answer: where did the time go, where did the money go, which step failed, how many retries happened, and whether the cache helped. LangSmith and Langfuse give you the LLM-specific view; OpenTelemetry gives you everything else. Wire both, with the same `run_id` as the correlating key so you can jump between them.

**Cost as a first-class observable:** put a cost panel next to the latency panel on the same dashboard. Teams that see cost in real time make different architecture decisions than teams that see it in a monthly invoice.

## Architecture example

```
app (OTel SDK)
   │ traces + metrics + logs, trace context injected into queue messages
   ▼
OTel Collector ──┬──▶ Prometheus  ──▶ Grafana (RED/USE, queue, cost panels)
                 ├──▶ Tempo/Jaeger ──▶ trace search by run_id / tenant_id
                 └──▶ Loki/ELK      ──▶ structured logs
                 
LangSmith / Langfuse ──▶ prompt, response, eval scores, per-step diffs
Postgres cost_ledger ──▶ Grafana (cost/tenant/day, cost/node, cache hit rate)
Alerting: oldest_message_age, error rate, budget burn, cost anomaly
```

## Hands-on exercise (55 min)

1. Instrument `mini-aios` with OpenTelemetry end to end. **The key part: propagate trace context through the queue message** so one trace spans API → queue → worker → LLM. Verify in Jaeger or Tempo that it is a single trace.
2. Add span attributes for model, tokens, cached tokens, cost, attempt number, and cache hit.
3. Build a dashboard with six panels: request rate, error rate by class, p50/p95/p99 run duration, queue depth and oldest-message age, cost per hour, and cache hit rate.
4. Write three SLOs in `obs/slo.md` with the error budget for each and the alert that fires on burn rate.
5. **Run four chaos experiments** and record detection time, user impact, and recovery time for each:
   - Kill a worker mid-run
   - Make the LLM provider return 429 for 60 seconds
   - Add 5 seconds of latency to every tool call
   - Fill the queue with 10,000 jobs from one tenant
6. Write `docs/runbook.md`: for each of the four, the symptom, the dashboard that shows it, the first three diagnostic commands, and the mitigation.

## Best resources

- **Google SRE Book** (free at `sre.google/books`) — chapters 4 (SLOs), 6 (monitoring), 21 (handling overload), 22 (cascading failures). This is the highest-value free reading in the entire plan after the AWS Builders' Library.
- **The SRE Workbook** (also free) — the SLO implementation chapters are more practical than the original book's.
- **OpenTelemetry documentation** — context propagation, semantic conventions, and the collector. Focus on the propagation section.
- **Brendan Gregg, "The USE Method"** — one page, permanently useful.
- **Langfuse or LangSmith documentation** — tracing for LLM applications, and how they model runs and spans.
- **Charity Majors' writing on observability** — particularly on high-cardinality debugging and why dashboards alone are insufficient.

## 15-minute review

From memory: state RED and USE; explain why trace context must ride in the queue message; name the best single alert for an agent platform and why; list four AI-specific signals a normal backend doesn't need.

## Interview / architecture questions

1. p99 run latency jumped from 20 s to 90 s an hour ago. Walk me through your diagnosis, in order, naming the dashboard or query at each step.
2. Why is CPU utilization a poor health signal for agent workers, and what do you use instead?
3. A user says "the agent gave a wrong answer yesterday." What must you have logged to investigate, and what is your process?
4. Design SLOs for a multi-tenant AI OS and explain how the error budget changes your team's behavior in a bad month.
5. Explain a cascading failure you would expect in an agent platform, tracing the chain from the first slow dependency to total unavailability, and name the control that breaks each link.

---

# Session 10 — Capacity, cost modeling, safe deployment, and design review

## Core concepts

1. **Capacity planning from first principles:** Little's Law for concurrency, provider TPM/RPM as the hard ceiling, plus headroom for the peak-to-average ratio.
2. **The scaling ladder:** what changes at 10x and 100x, and knowing which decisions are reversible.
3. **Cost model:** fixed vs. variable cost, cost per run, and idle waste.
4. **Safe deployment:** blue/green, canary, feature flags — and the AI-specific version, where the risky change is a prompt or a model rather than code.
5. **Prompts and models as versioned config**, deployed independently from code, with offline evals as the gate and shadow traffic as the verification.
6. **DR basics:** RPO (how much data you can lose) and RTO (how long you can be down), stated as numbers before you design anything.
7. **The design review checklist** you will use for the rest of your career.

## Practical explanation

**Capacity math, worked end to end:**

```
Target: 20,000 agent runs/day, peak 3x average, 6 LLM calls/run,
        avg 4,000 input + 800 output tokens per call, 45 s per run

Average rate:      20,000/86,400        = 0.23 runs/s
Peak rate:         × 3                  = 0.70 runs/s
Concurrency (L=λW): 0.70 × 45           ≈ 32 concurrent runs
Workers @100 conc:  ceil(32/100)        = 1 pod (use 3 for HA + headroom)
Peak TPM:          0.70 × 6 × 4,800 × 60 ≈ 1.2M tokens/min  ← the real constraint
Daily tokens:      20,000 × 6 × 4,800    ≈ 576M tokens/day
```

The lesson repeats: **your bottleneck is provider throughput and token cost, not compute.** Three pods serve this workload. The token bill is what you negotiate, cache, and route around.

**The scaling ladder:**

| Scale | What changes |
|---|---|
| **1x → 10x** | Nothing structural. Add worker replicas, add caching, tune indexes, add per-tenant limits |
| **10x → 100x** | Split queues by workload class, read replicas, partition big tables, dedicated vector store, multiple provider accounts or regions for rate limits, cost attribution becomes a product feature |
| **100x → 1000x** | Sharding by tenant, regional deployments, dedicated inference capacity, custom scheduling, a platform team |

Most engineers over-build for a scale they never reach, and under-build the things that break at 3x (fairness, idempotency, observability). Build for correctness at 1x and headroom for 10x.

**Deploying prompt and model changes safely:**

```
change prompt/model
  → offline eval suite (golden set, must not regress)
  → shadow: run new version on 5% of live traffic, compare outputs, do not serve
  → canary: serve to 5% of tenants, watch quality + cost + latency
  → ramp 25% → 100%, with an instant rollback flag
```

Code deploys have tests. Prompt deploys need evals, and they need to be independently versioned so you can roll back a prompt without redeploying the service. Store prompts in a table with a version, pin runs to the version they started with, and log the version on every step.

## The design review checklist

Run this against every system you design or inherit. This is the deliverable of the entire 20 hours.

| Dimension | Questions |
|---|---|
| **Architecture** | What are the deployables and why? Which of the five split-triggers justifies each one? |
| **Scalability** | What is the bottleneck at 10x? Is scaling horizontal? What is the hard ceiling (provider limits)? |
| **Concurrency** | What is the concurrency model? Little's Law numbers? Where is the shared mutable state? |
| **State** | Where does every piece of state live? What survives a pod restart? Checkpoint granularity? |
| **Failure** | For each dependency: timeout, retry policy, budget, circuit breaker, fallback. What is the blast radius? |
| **Recovery** | Can a crashed run resume? Are side effects idempotent? Is there a DLQ, and who drains it? RPO/RTO? |
| **Resources** | Requests/limits set from measurements? Autoscaling on the right signal? Isolation rung justified? |
| **Observability** | Can you trace one run end to end? Are cost and tokens per run visible? What are the SLOs? |
| **Cost** | Cost per run and per tenant? Cache hit rate? Cheapest adequate model per task? Idle waste? |
| **Performance** | Where does the time go? What is the theoretical floor? What is the p99 and why? |

## Hands-on exercise (55 min)

1. **Load test `mini-aios` to find the knee.** Ramp arrival rate until p99 degrades. Record the throughput ceiling and which resource saturated first. Compare against your Little's Law prediction and explain any gap.
2. **Build `docs/cost-model.md`:** fixed monthly infra, variable cost per run broken down by token cost, infra cost, and storage. Project at 1k, 10k, 100k runs/month. Include the cache hit rate as a variable so you can see its leverage.
3. **Write two ADRs:** one for your queue choice and one for your isolation strategy. Format: context, options considered, decision, consequences, revisit-when.
4. **Implement a prompt registry:** a `prompts` table with version, content, and active flag; pin each run to a version; log the version on every step; roll back by flipping a flag with no deploy.
5. **Run the design review checklist** against `mini-aios`, then against one real client system. Write down every gap. That gap list is your consulting value in written form.

## Best resources

- **Google SRE Workbook** — the chapters on capacity planning and canarying releases.
- **AWS Well-Architected Framework** (free) — the Reliability and Cost Optimization pillars, skimmed with your own system in hand.
- **Marc Brooker's blog** — posts on capacity, load, and the mathematics of overload.
- **"Choose Boring Technology"** and **"Simple Made Easy"** (Rich Hickey talk) — architecture judgment, not techniques.
- **DDIA chapter 1 and the final chapter** — reread now; they will read completely differently than they did in session 1.
- **Any two Jepsen analyses and one AWS or Cloudflare public post-mortem** — post-mortems are the best free architecture education available.

## 15-minute review

From memory: run the full 10-point design review checklist on a system you know, out loud, without looking. If you can do that, the 20 hours worked.

## Interview / architecture questions

1. A client wants to go from 500 to 50,000 runs/day. What actually has to change, and what does not?
2. You need to change the model behind a production agent. Describe your rollout, including how you know it is safe and how fast you can roll back.
3. What are your RPO and RTO for agent runs, and what architecture decisions do those numbers force?
4. Where does your architecture waste money right now? Name the top three and quantify them.
5. Explain your entire AI OS architecture in five minutes to a CTO, then in one minute to a CFO. What changes between the two?

---
---

# Deliverable 1 — One-page distributed systems cheat sheet for AI OS design

## Numbers

| Thing | Value |
|---|---|
| Redis GET / Postgres indexed query | 0.2–1 ms / 1–5 ms |
| Same-region hop / cross-region RTT | 1–5 ms / 50–150 ms |
| Vector search (1M, HNSW) | 5–50 ms |
| LLM TTFT / full completion | 0.3–2 s / 2–60 s |
| Agent run (8 steps) | 30–300 s |
| Checkpoint write | ~2 ms (free, relative to a step) |
| Pod cold start / with model load | 2–20 s / minutes |

## Formulas

```
Little's Law:      concurrency = arrival_rate × time_in_system
Workers:           ceil(concurrency / per_worker_concurrency)
Peak TPM:          peak_runs_per_sec × calls_per_run × tokens_per_call × 60
Retry backoff:     random(0, min(cap, base × 2^attempt))       # full jitter
Effective cost:    (1 − hit_rate) × miss_cost + hit_rate × hit_cost
Idempotency key:   sha256(run_id : step_idx : tool : canonical_args)
```

## Decision rules

| Question | Rule |
|---|---|
| One container or many? | Split only on: different scaling axis, resource profile, failure isolation, deploy cadence, or security boundary |
| Sync or async? | p99 > 5–10 s, or must survive client disconnect → queue it |
| How to run 1,000 agents? | Rows + queue + shared async workers. Agents are data, not processes |
| Which queue? | Postgres SKIP LOCKED → Redis Streams → SQS → Kafka. Stop at the first that fits |
| Which store? | Postgres by default; Redis for ephemeral; S3 for blobs; dedicated vector DB past ~5–10M vectors |
| Isolate agents? | Shared pool by default. microVM only for LLM-generated/untrusted code |
| Autoscale on what? | Queue depth or oldest-message age (KEDA). Never CPU for IO-bound workers |
| Need a distributed lock? | Try to need idempotency instead. Then Postgres lease. Then K8s Lease. Redlock rarely |
| Need consensus? | No. Use something that already has it (Postgres, etcd, Temporal) |
| Where to cut cost? | Context size → prompt caching → exact cache → model routing → batch → semantic cache → infra |
| Retry this? | 429/5xx/timeouts yes with jitter and a budget; 4xx never; writes only through the idempotency gateway |

## Failure playbook

| Symptom | Likely cause | First check |
|---|---|---|
| Duplicate side effects | at-least-once delivery, no idempotency | dedup table hit rate |
| Runs stuck in `running` | dual-write, no outbox; or a leaked lease | lease expiry reaper, outbox lag |
| Queue grows, CPU flat | wrong autoscale signal or provider rate limit | oldest-message age, 429 rate |
| Bill spike | retry storm, context bloat, cache miss, model change | cost ledger by tenant/node/day |
| Latency cliff under load | unbounded queue, no shedding, pool exhaustion | queue depth, connection pool, saturation |
| Works locally, dies in prod | CPU throttling from limits, OOMKill, cold start | throttled seconds, OOM events |
| Agent "forgets" after deploy | state in process memory | checkpointer configuration |
| One tenant degrades all | no fairness, head-of-line blocking | per-tenant queue depth |

## Ten words to keep in your head

Partial failure · deadline · idempotency · backpressure · checkpoint · lease · fencing · bulkhead · budget · blast radius.

---

# Deliverable 2 — Reference architecture: a production AI Operating System

```
                            ┌─────────────────────────────────────┐
   Clients / UI / API ─────▶│  API Gateway (auth, quota, 429)     │
                            │  FastAPI × 2–3, stateless           │
                            └───────┬─────────────────────┬───────┘
                                    │ POST /runs (202)    │ GET /runs/{id}
                                    ▼                     ▼
  ══════════════ CONTROL PLANE ══════════════════════════════════════
   ┌──────────────────────────────────────────────────────────────┐
   │ Postgres:  runs · steps · idempotency · outbox · queue        │
   │            tenants · quotas · prompts(versioned) · evals      │
   │            cost_ledger · embeddings(pgvector)                 │
   ├──────────────────────────────────────────────────────────────┤
   │ Scheduler (leader-elected): wake due runs, retries, reaper    │
   │ Outbox poller: transactional enqueue → broker                 │
   └──────────────────────────────────────────────────────────────┘
                                    │
  ══════════════ EXECUTION PLANE ═══╪═══════════════════════════════
                                    ▼
            ┌──────────────────────────────────────────┐
            │ Queues:  interactive │ batch │ retry │ DLQ│
            │ (Postgres SKIP LOCKED or Redis Streams)  │
            └───────────────────┬──────────────────────┘
                                │ claim w/ lease
                                ▼
   ┌──────────────────────────────────────────────────────────┐
   │ Worker pool  (async, KEDA-scaled on queue depth)          │
   │  ┌────────────────────────────────────────────────────┐  │
   │  │ LangGraph run: load checkpoint → execute node →     │  │
   │  │ checkpoint → re-enqueue / complete                  │  │
   │  └────────────────────────────────────────────────────┘  │
   │        │                    │                   │         │
   │        ▼                    ▼                   ▼         │
   │  ┌───────────┐      ┌─────────────┐     ┌─────────────┐  │
   │  │LLM Gateway│      │Tool Gateway │     │  Retrieval  │  │
   │  │ idem key  │      │ idem key    │     │ pgvector /  │  │
   │  │ deadline  │      │ deadline    │     │ vector DB   │  │
   │  │ retry+CB  │      │ retry+CB    │     │ + rerank    │  │
   │  │ L1/L2/L3  │      │ allowlist   │     └─────────────┘  │
   │  │ cost log  │      │ audit log   │                       │
   │  └─────┬─────┘      └──────┬──────┘                       │
   └────────┼───────────────────┼──────────────────────────────┘
            │                   │
            ▼                   ▼
   ┌──────────────────┐  ┌──────────────────────────────────┐
   │ Provider limiter │  │ Sandbox pool (gVisor/Firecracker)│
   │ (Redis buckets)  │  │ code exec, no egress, TTL 60s    │
   └────────┬─────────┘  └──────────────────────────────────┘
            ▼
   ┌────────────────────────────┐   ┌───────────────────────┐
   │ LLM providers  │  self-host │   │ Redis: cache · limits │
   │                │  vLLM/GPU  │   │ leases · counters     │
   └────────────────────────────┘   └───────────────────────┘

  ══════════════ OBSERVABILITY (cross-cutting) ═════════════════════
   OTel SDK → Collector → Prometheus/Grafana · Tempo · Loki
   LangSmith/Langfuse (prompt, response, evals) · cost dashboards
   Alerts: oldest_message_age · error rate · budget burn · cost anomaly
```

**Why each piece exists, in one line each:**

| Component | Justification |
|---|---|
| API separate from workers | Different scaling axis; API must stay responsive under worker overload |
| 202 + run_id instead of blocking | Runs take 30–300 s; the client must not hold a connection |
| Postgres as control plane | Transactional state + queue + outbox in one place removes an entire class of bugs |
| Outbox poller | Removes the dual-write failure that silently strands runs |
| Lease-based claiming | Cheap mutual exclusion without a distributed lock service |
| Checkpoint per node | A crash costs one step, not one run and one dollar |
| LLM/Tool gateways | The single place where idempotency, retries, limits, caching, and cost accounting live |
| Separate interactive/batch queues | Prevents head-of-line blocking, which is the top perceived-quality complaint |
| KEDA on queue depth | The only autoscaling signal that correlates with agent load |
| Sandbox pool | The one place isolation is genuinely required |
| Versioned prompts | Lets you roll back behavior without a deploy, and pin in-flight runs |
| Cost ledger | Makes the most important number in the system queryable |

**What to leave out until you need it:** service mesh, Kafka, dedicated vector DB, Ray, multi-region, per-tenant clusters, custom schedulers. Each of these has a trigger described in the sessions above. Wait for the trigger.

---

# Deliverable 3 — Ten real-world architecture problems

Work through these after session 10. Give each 30–45 minutes, produce a diagram plus a written decision list, then check your answer against the design review checklist.

**1. The duplicate invoice.**
A client's agent emails invoices. Twice a week a customer gets two. Logs show one run, one step, two sends. Find every possible cause and design the fix so it cannot recur.

**2. The overnight bill.**
Monthly LLM spend went from $3,000 to $11,000 with flat usage. Nothing was deployed. List your hypotheses in order of likelihood, the query or dashboard that confirms each, and the fix.

**3. Ten thousand sleeping agents.**
A client wants 10,000 monitoring agents, each waking hourly to check a data source and act if something changed. Design it for under $500/month of infrastructure. State your concurrency, storage, and scheduling decisions with numbers.

**4. The three-hour research agent.**
A deep-research agent runs 2–4 hours across 200 LLM calls and 50 web fetches. It must survive deploys, pod evictions, and provider outages, and must show live progress to the user. Design state, execution, and streaming.

**5. Noisy neighbor.**
One enterprise tenant submits 50,000 document-processing jobs at 2 a.m. Interactive users see 4-minute latencies. Fix it without buying more capacity, then explain what you would sell them instead.

**6. RAG freshness.**
A legal client requires an uploaded document to be searchable within 5 seconds, with 100% guarantee that a deleted document is never retrieved again. Design the indexing pipeline and state which consistency guarantees you need where.

**7. Untrusted code.**
Your agent writes and runs Python for data analysis on customer CSVs. Design the execution environment end to end: isolation, network, filesystem, secrets, resource caps, timeouts, and audit. Then describe how you'd detect an attempted breakout.

**8. The provider outage.**
Your primary LLM provider is degraded for 40 minutes: 30% error rate, 4x latency. 600 runs are in flight. Design the behavior. Consider failover, quality differences between models, cost, and what you tell users.

**9. GPU economics.**
A client spends $9,000/month on embedding and reranking API calls. Should they self-host? Show the numbers you need, the break-even calculation, the hidden costs, and your recommendation with the conditions that would change it.

**10. The multi-agent deadlock.**
Five agents coordinate through a shared task board. Occasionally two claim the same task, and occasionally all five wait for a task that no one owns. Diagnose both bugs in distributed systems terms and fix them.

---

# Deliverable 4 — The 60-minute system design challenge

## The brief

> **Design an AI Operating System for a mid-size logistics company.**
>
> Requirements:
> - 400 internal users, 12 external customer portals (multi-tenant)
> - Six agent types: document intake (invoices, bills of lading), shipment tracking, customer support, pricing analysis, compliance checking, and an internal ops copilot
> - Volume: 8,000 agent runs/day, peaking 5x during business hours; document intake handles 30,000 PDFs/month
> - Some runs are interactive (support, copilot, under 15 s expected); some are long-running (compliance audits, up to 2 hours)
> - The pricing agent executes generated Python against internal data
> - Compliance requires a full audit trail of every agent decision for 7 years
> - Budget: under $6,000/month total, LLM spend included
> - Team: three engineers, no dedicated platform team

## Timeboxes

| Minutes | Task | What "good" looks like |
|---|---|---|
| 0–5 | Clarify requirements, state assumptions | You ask about peak-to-average, latency expectations per agent type, data residency, and what "audit trail" legally means |
| 5–15 | High-level architecture | Deployables, data stores, queues, execution plane. You justify each split with a trigger |
| 15–30 | Deep dive: execution and state | Concurrency model, worker sizing with Little's Law, checkpointing, long-running run handling, code sandbox |
| 30–40 | Failure and recovery | Per-dependency timeout/retry/circuit breaker, idempotency, DLQ, provider outage, blast radius |
| 40–50 | Cost and scale | Cost per run, monthly projection, caching strategy, model routing, what changes at 5x |
| 50–60 | Observability and wrap-up | Traces, SLOs, key alerts, top three risks, what you would build first and what you would defer |

## Self-grading rubric (50 points)

| Area | Points | Criteria |
|---|---|---|
| Requirements and assumptions | 5 | Asked before designing; assumptions written down and reasonable |
| Architecture and boundaries | 6 | Correct number of deployables, each justified by a trigger, not by fashion |
| Concurrency and scale | 6 | Used Little's Law; identified provider TPM as the real ceiling; horizontal scaling path |
| State and long-running work | 6 | Checkpointing, resume, no state in process memory, versioning addressed |
| Failure and recovery | 8 | Idempotency for every write; timeouts and budgets; DLQ; provider fallback; blast radius named |
| Isolation | 4 | Sandbox only for generated code; shared pool for the rest; justified |
| Cost | 6 | Concrete per-run cost; caching and routing; total projection inside budget |
| Observability | 5 | End-to-end tracing; SLOs; the right alerts; cost visibility |
| Multi-tenancy | 4 | Fairness, quotas, data isolation, audit trail design |

**Scoring:** 40+ means you can lead this design. 30–39 means you can build it with review. Under 30, revisit the sessions covering the areas you lost points in.

**Do this twice:** once now, once two weeks later without looking at your first answer. Compare them. The delta is your actual learning.

---

# Deliverable 5 — What to study after these 20 hours

**Next 20 hours, ranked by return for your work:**

1. **Inference infrastructure.** vLLM internals, continuous batching, speculative decoding, quantization, KV cache management, tensor/pipeline parallelism. This is where AI-specific distributed systems knowledge is scarcest and most valuable, and it directly serves the self-hosting questions clients keep asking.
2. **Temporal or a durable execution engine, properly.** Build one real workflow. Even if you keep LangGraph, this teaches you the correct mental model for long-running distributed work.
3. **Kafka and stream processing.** Log-based architecture, consumer groups, partitioning, exactly-once semantics in Kafka, and change data capture with Debezium. Needed the moment a client wants real-time event-driven agents.
4. **Kubernetes beyond the basics.** Operators and CRDs, admission controllers, network policies, pod security, and multi-tenancy patterns. Enough to build an agent platform as a controller rather than a script.
5. **Security for agent systems.** Sandbox escape surfaces, secrets management, mTLS and service identity, prompt-injection as a distributed authorization problem, and least-privilege tool design. This will be a paid specialty within two years.
6. **Database internals.** B-trees vs. LSM trees, write amplification, MVCC, query planning, HNSW and IVF index internals. Makes you dramatically better at choosing and tuning stores.
7. **Deterministic simulation and property testing.** FoundationDB's testing approach, Jepsen methodology, and chaos engineering as practice. This is how you build confidence in a system you cannot fully observe.
8. **FinOps for AI.** Unit economics, cost allocation, commitment/reserved capacity, spot strategies, and how to present cost architecture to a CFO. Directly monetizable in consulting.

**Longer horizon, lower urgency:** CRDTs and collaborative state, multi-region consistency and Spanner/CockroachDB designs, formal methods with TLA+, eBPF for observability, scheduling theory, and the classic paper canon (GFS, MapReduce, Bigtable, Dynamo, Chubby, ZooKeeper, Spanner) read properly rather than skimmed.

---

# Appendix — Consolidated resource index

**Free courses and lectures**
- MIT 6.5840 / 6.824, Distributed Systems — lectures, notes, and labs at `pdos.csail.mit.edu/6.824`. Sessions 1, 2, 6.
- Martin Kleppmann's Cambridge distributed systems lecture series — free videos and notes; a gentler companion to DDIA.

**Free books and libraries**
- Google SRE Book and SRE Workbook — `sre.google/books`. Sessions 9, 10.
- AWS Builders' Library — timeouts and retries, load shedding, fairness, caching, health checks. The most directly applicable free resource in the list.
- AWS Well-Architected Framework — Reliability and Cost Optimization pillars.

**Books worth buying**
- *Designing Data-Intensive Applications*, Kleppmann. Chapters 1, 5, 6, 7, 8, 9, 11 map directly onto sessions 1, 4, 5, 6.
- *Database Internals*, Petrov — for the follow-on study list.

**Papers**
- Raft, "In Search of an Understandable Consensus Algorithm" (+ `raft.github.io`)
- "Large-scale cluster management at Google with Borg"
- "Scaling Memcache at Facebook"
- "Life Beyond Distributed Transactions", Pat Helland
- Ray (OSDI 2018) and PagedAttention/vLLM (SOSP 2023)
- Sagas, Garcia-Molina & Salem (1987)

**Documentation to read as literature, not reference**
- LangGraph persistence; Temporal concepts; PostgreSQL isolation and `SKIP LOCKED`; Redis Streams; KEDA scalers; Kubernetes resource management and QoS; pgvector; OpenTelemetry context propagation; provider prompt-caching and batch API docs.

**Blogs and essays**
- Marc Brooker (`brooker.co.za/blog`); Martin Kleppmann ("How to do distributed locking"); Dan McKinley ("Choose Boring Technology"); Jepsen analyses; public post-mortems from AWS, Cloudflare, and GitHub.

---

*Verify links and paper details before citing them formally. I built this from knowledge current to mid-2026 without live search, so specific URLs, pricing figures, and version-dependent details may have shifted.*
