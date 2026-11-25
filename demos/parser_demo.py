"""Run the four parser demo queries. Q4 is a follow-up to Q3 to exercise
conversation-history retention.
"""
from __future__ import annotations

import json

from pct.parser import Parser

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import DOMAINS, RESULTS_DIR, domain, results_path  # noqa: E402


QUERIES = [
    "what is the velocity of an object after falling for 3 seconds",
    "find the kinetic energy of a 5kg object moving at 10 m/s",
    "what force acts on a 2kg mass accelerating at 4 m/s squared",
    "what if the mass was 5kg instead",
]


def main() -> None:
    p = Parser(DOMAINS["physics"]["kb2"])
    print(f"model: {p.model}")
    print(f"kb2:   {len(p.kb2['predicates'])} predicates, "
          f"{len(p.kb2['bindings_schema'])} bindings_schema")
    for i, q in enumerate(QUERIES, 1):
        print(f"\n=== Q{i}: {q} ===")
        goal = p.parse(q)
        print(json.dumps(goal, indent=2))


if __name__ == "__main__":
    main()
