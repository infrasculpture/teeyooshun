"""Generate practice questions from approved revision cards:

    python -m app.generate <subject> [--topic <topic id>] [--level sec1] [--limit N] [--dry-run]

--dry-run lists the cards and the planned question mix with a cost estimate; no API calls, no changes.
See docs/GENERATION_SPEC.md."""

import argparse
import sys

from app.generate.run import run


def main(argv=None, **kwargs) -> int:
    parser = argparse.ArgumentParser(description="Generate practice questions from approved revision cards.")
    parser.add_argument("subject", help="for example science or geography")
    parser.add_argument("--topic", help="only this topic id")
    parser.add_argument("--level", help="only this level, for example sec1")
    parser.add_argument("--limit", type=int, help="at most this many cards")
    parser.add_argument("--dry-run", action="store_true", help="show the plan and cost estimate; no API calls")
    args = parser.parse_args(argv)
    return run(args.subject, topic=args.topic, level=args.level, limit=args.limit, dry_run=args.dry_run, **kwargs)


if __name__ == "__main__":
    sys.exit(main())
