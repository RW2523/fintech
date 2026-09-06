"""The synthetic data command line (docs/10 preamble).

python -m synthetic.cli population --n 5000 --months 24 --seed 42
python -m synthetic.cli load --base-url http://localhost:8010
python -m synthetic.cli stats
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from synthetic.config import DEMO_AS_OF_MONTH, Settings
from synthetic.population.generate import generate
from synthetic.writer import DEFAULT_OUT, write_population

__all__ = ["main"]


def _population(args: argparse.Namespace) -> int:
    settings = Settings(seed=args.seed, members=args.n, months=args.months, employers=args.employers)
    started = time.perf_counter()
    population = generate(settings)
    generated = time.perf_counter() - started

    manifest = write_population(population, Path(args.out), seed=settings.seed, months=settings.months)
    elapsed = time.perf_counter() - started

    print(
        f"  generated {settings.members:,} members in {generated:.1f}s (wrote in {elapsed - generated:.1f}s)"
    )
    for name, body in sorted(manifest.tables.items()):
        print(f"    {name:14s} {body['rows']:>9,} rows  {body['sha256'][:12]}")
    print(f"  digest {manifest.digest}")
    print(f"  demo as-of is the end of month {DEMO_AS_OF_MONTH}")
    return 0


def _documents(args: argparse.Namespace) -> int:
    from synthetic.documents.generate import generate_documents

    started = time.perf_counter()
    corpus = generate_documents(
        args.apps,
        population_dir=Path(args.out),
        out=Path(args.out) / "documents",
        seed=args.seed,
        render_pdf=not args.no_pdf,
    )
    elapsed = time.perf_counter() - started

    from collections import Counter

    by_type = Counter(d.type for d in corpus.documents)
    by_anomaly = Counter(str(a.kind) for a in corpus.anomalies)

    print(
        f"  {len(corpus.applications):,} applications, "
        f"{len(corpus.documents):,} documents in {elapsed:.0f}s "
        f"({elapsed / max(1, len(corpus.documents)) * 1000:.0f} ms each)"
    )
    for name, count in sorted(by_type.items()):
        print(f"    {name:26s} {count:>6,}")
    print(f"  {len(corpus.anomalies):,} injected anomalies")
    for name, count in sorted(by_anomaly.items()):
        print(f"    {name:26s} {count:>6,}")
    return 0


def _accuracy(args: argparse.Namespace) -> int:
    from synthetic.accuracy import measure

    result = measure(Path(args.out) / "documents", limit=args.limit).as_dict()
    print(f"  documents            {result['documents']}")
    print(f"  classification       {result['classification_accuracy']:.1%}   (target >= 98%)")
    print(f"  critical fields      {result['critical_field_accuracy']:.1%}   (target >= 95%)")
    print(f"  all fields           {result['field_accuracy']:.1%}")
    print(f"  bbox coverage        {result['bbox_coverage']:.1%}")
    print(f"  confidence coverage  {result['confidence_coverage']:.1%}")
    print()
    for name, stats in result["by_field"].items():
        mark = "  " if stats["accuracy"] >= 0.95 else "<-"
        print(f"  {mark} {name:24s} {stats['accuracy']:6.1%}  {stats['exact']}/{stats['total']}")

    ok = result["classification_accuracy"] >= 0.98 and result["critical_field_accuracy"] >= 0.95
    print(f"\n  {'meets' if ok else 'BELOW'} the T-023 thresholds")
    return 0 if ok else 1


def _load(args: argparse.Namespace) -> int:
    from synthetic.loader import load_population

    started = time.perf_counter()
    loaded = asyncio.run(
        load_population(Path(args.out), base_url=args.base_url, reset=not args.no_reset, token=args.token)
    )
    print(f"  loaded in {time.perf_counter() - started:.1f}s")
    for table, count in loaded.items():
        print(f"    {table:14s} {count:>9,}")
    return 0


def _stats(args: argparse.Namespace) -> int:
    from synthetic.stats import summarise

    summary = summarise(Path(args.out))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["within_ranges"] else 1


def main(argv: list[str] | None = None) -> int:
    # --out is accepted before or after the subcommand
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")

    parser = argparse.ArgumentParser(prog="synthetic", description=__doc__, parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    population = sub.add_parser("population", parents=[common], help="generate the member population")
    population.add_argument("--n", type=int, default=5000)
    population.add_argument("--months", type=int, default=24)
    population.add_argument("--employers", type=int, default=120)
    population.add_argument("--seed", type=int, default=42)
    population.set_defaults(handler=_population)

    documents = sub.add_parser("documents", parents=[common], help="render the document corpus")
    documents.add_argument("--apps", type=int, default=600)
    documents.add_argument("--seed", type=int, default=42)
    documents.add_argument("--no-pdf", action="store_true", help="skip PDF rendering (faster, PNG only)")
    documents.set_defaults(handler=_documents)

    accuracy = sub.add_parser("accuracy", parents=[common], help="score extraction against the ground truth")
    accuracy.add_argument("--limit", type=int, default=300)
    accuracy.set_defaults(handler=_accuracy)

    load = sub.add_parser("load", parents=[common], help="load a generated population into the core stub")
    load.add_argument("--base-url", default=None)
    load.add_argument("--token", default=None)
    load.add_argument("--no-reset", action="store_true")
    load.set_defaults(handler=_load)

    stats = sub.add_parser("stats", parents=[common], help="check the sanity ranges in docs/10 §4.6")
    stats.set_defaults(handler=_stats)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
