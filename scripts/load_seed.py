"""Upsert suppliers, customers and rules from seed/*.csv into the database.

Usage:  python scripts/load_seed.py [--overwrite-rules]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.db import session_scope
from agent.seed_loader import load_all

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--overwrite-rules", action="store_true", help="reset rule values to seed/rules.csv")
    args = ap.parse_args()
    with session_scope() as s:
        counts = load_all(s, overwrite_rules=args.overwrite_rules)
    print("loaded:", counts)
