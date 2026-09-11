from __future__ import annotations

import argparse
import logging
import sys

from worker.agent import resume_run, start_run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or resume the 3-step mini-aios agent.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task", default="Notify that mini-aios session 2 is working.")
    parser.add_argument("--to", default="user@example.com")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)

    if args.resume:
        result = resume_run(args.run_id)
    else:
        result = start_run(args.run_id, task=args.task, to=args.to)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
