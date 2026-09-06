"""The retrieval command line (docs/06 §7).

uv run python -m ai.rag.cli build     # write the corpus from the packs
uv run python -m ai.rag.cli index     # chunk and index it
uv run python -m ai.rag.cli ask "..."  # retrieve
uv run python -m ai.rag.cli evaluate  # score it against the acceptance
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "synthetic" / "policy_corpus"


def _build(args: argparse.Namespace) -> int:
    from synthetic.corpus import build_corpus

    for document in build_corpus(Path(args.corpus)):
        print(f"  {document.path.name:28s} {document.product:12s} {len(document.clauses):3d} clauses")
    return 0


def _index(args: argparse.Namespace) -> int:
    from ai.rag.index import index_corpus

    report = asyncio.run(index_corpus(Path(args.corpus), gateway_url=args.gateway))
    print(
        f"  {report['clauses']} clauses from {report['documents']} documents, "
        f"{report['clause_ids']} distinct ids"
    )
    print(f"  retrieval: {report['retrieval']}")
    if report.get("note"):
        print(f"  note: {report['note']}")
    return 0


def _ask(args: argparse.Namespace) -> int:
    from ai.rag.index import retrieve

    hits = asyncio.run(
        retrieve(args.question, limit=args.limit, product=args.product, gateway_url=args.gateway)
    )
    for hit in hits:
        print(
            f"  {hit.clause_id:8s} {hit.doc:24s} {hit.score:.3f}  "
            f"(lex {hit.lexical:.3f} vec {hit.vector:.3f})"
        )
        print(f"      {hit.text.splitlines()[2][:100] if len(hit.text.splitlines()) > 2 else ''}")
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    from ai.rag.evaluate import evaluate_retrieval

    report = asyncio.run(evaluate_retrieval(Path(args.corpus), gateway_url=args.gateway)).as_dict()
    print(
        f"  clause-id questions   {report['clause_id_found_in_top_3']}"
        f"/{report['clause_id_questions']} in top {report['top_k']}"
        f" = {report['clause_id_rate']:.1%}   (target {report['target']:.0%})"
    )
    print(
        f"  officer's own words   {report['prose_found_in_top_3']}"
        f"/{report['prose_questions']} = {report['prose_rate']:.1%}"
        f"   (reported, not an acceptance)"
    )
    print(f"  retrieval: {report['retrieval']}")
    if report["misses"]:
        print("\n  misses:")
        for miss in report["misses"]:
            print(f"    {miss['expected']:8s} <- {miss['question'][:52]!r} returned {miss['returned']}")
    ok = report["meets_target"]
    print(f"\n  clause-id target {'met' if ok else 'BREACHED'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--corpus", default=str(CORPUS))
    common.add_argument("--gateway", default=None, help="LLM gateway base URL for embeddings")

    parser = argparse.ArgumentParser(prog="ai.rag", description=__doc__, parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("build", parents=[common], help="write the corpus from the policy packs").set_defaults(
        handler=_build
    )
    sub.add_parser("index", parents=[common], help="chunk and index the corpus").set_defaults(handler=_index)
    ask = sub.add_parser("ask", parents=[common], help="retrieve clauses")
    ask.add_argument("question")
    ask.add_argument("--limit", type=int, default=5)
    ask.add_argument("--product", default=None)
    ask.set_defaults(handler=_ask)
    sub.add_parser("evaluate", parents=[common], help="score retrieval against the acceptance").set_defaults(
        handler=_evaluate
    )

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
