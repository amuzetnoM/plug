#!/usr/bin/env python3
"""
Aria Memory Flush → COMB

Persistent cross-session memory for Aria (AVA's little sister).
Same architecture as AVA's flush.py — lossless, chain-ordered.

Usage:
    python3 aria_memory/flush.py stage "Important thing to remember"
    python3 aria_memory/flush.py recall
    python3 aria_memory/flush.py search "query"
    python3 aria_memory/flush.py rollup
    python3 aria_memory/flush.py stats
    python3 aria_memory/flush.py verify
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PLUG_ROOT = Path(__file__).resolve().parent.parent
COMB_STORE = PLUG_ROOT / "aria_memory" / "comb-store"

# COMB is installed in Plug's venv
from comb import CombStore


def get_store() -> CombStore:
    return CombStore(str(COMB_STORE))


def stage_text(text: str, metadata: dict = None):
    store = get_store()
    meta = metadata or {}
    meta["source"] = "aria-flush"
    meta["timestamp"] = datetime.now(timezone.utc).isoformat()
    store.stage(text, metadata=meta)
    print(f"✅ Staged {len(text)} chars into Aria's COMB")


def rollup(date: str = None):
    store = get_store()
    doc = store.rollup(date=date)
    if doc:
        print(f"✅ Rolled up: {doc.date} ({len(doc.to_dict()['content'])} chars)")
    else:
        print("ℹ️  Nothing to roll up")


def search(query: str, k: int = 5):
    store = get_store()
    results = store.search(query, mode="bm25", k=k)
    if not results:
        print("No results found.")
        return
    for i, doc in enumerate(results):
        content = doc.to_dict()["content"]
        preview = content[:500] + ("..." if len(content) > 500 else "")
        print(f"\n--- Result {i+1} ({doc.date}) ---")
        print(preview)


def recall():
    """Pull Aria's operational memory for session start."""
    store = get_store()
    queries = [
        "identity sister AVA who I am",
        "active tasks projects status",
        "lessons learned mistakes corrections",
        "dispatch delegation workflow",
        "important context remember",
    ]

    seen = set()
    all_results = []

    for query in queries:
        results = store.search(query, mode="bm25", k=3)
        for doc in results:
            if doc.date not in seen:
                seen.add(doc.date)
                all_results.append(doc)

    if not all_results:
        print("Aria's COMB is empty — no memories yet.")
        return

    all_results.sort(key=lambda d: d.date, reverse=True)

    print("=== ARIA OPERATIONAL RECALL ===\n")
    for doc in all_results[:10]:
        print(f"--- {doc.date} ---")
        print(doc.to_dict()["content"][:1000])
        print()


def stats():
    store = get_store()
    s = store.stats()
    print(json.dumps(s, indent=2, default=str))


def verify():
    store = get_store()
    ok = store.verify_chain()
    print("✅ Chain integrity verified" if ok else "❌ Chain integrity BROKEN")
    if not ok:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Aria Memory → COMB")
    sub = parser.add_subparsers(dest="command")

    p_stage = sub.add_parser("stage", help="Stage text into memory")
    p_stage.add_argument("text", help="Text to remember")

    sub.add_parser("rollup", help="Roll up staged entries")

    p_search = sub.add_parser("search", help="Search memory")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("-k", type=int, default=5)

    sub.add_parser("recall", help="Pull context for session start")
    sub.add_parser("stats", help="Store statistics")
    sub.add_parser("verify", help="Verify chain integrity")

    args = parser.parse_args()

    commands = {
        "stage": lambda: stage_text(args.text),
        "rollup": lambda: rollup(),
        "search": lambda: search(args.query, k=args.k),
        "recall": recall,
        "stats": stats,
        "verify": verify,
    }

    if args.command in commands:
        commands[args.command]()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
