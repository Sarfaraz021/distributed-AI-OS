"""Call the LLM gateway with 50% 429 injection and print the backoff curve."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["FAULT_INJECT_429"] = "true"

from core.config import get_settings  # noqa: E402
from core.idempotency import MemoryIdempotencyStore, set_store  # noqa: E402
from core.llm import call_llm, retry_budget  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def main() -> int:
    get_settings.cache_clear()
    set_store(MemoryIdempotencyStore())
    settings = get_settings()
    print(
        f"backend={settings.llm_backend} model={settings.llm_model} "
        f"fault_inject_429={settings.fault_inject_429}"
    )
    for i in range(8):
        try:
            result = call_llm(
                [{"role": "user", "content": f"ping {i}"}],
                idem_key=f"fault-demo-{i}",
                deadline_s=30,
            )
            print(f"call {i}: ok content={result['content'][:80]!r}")
        except Exception as exc:
            print(f"call {i}: failed {type(exc).__name__}: {exc}")
    print("retry_budget", retry_budget().snapshot())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
