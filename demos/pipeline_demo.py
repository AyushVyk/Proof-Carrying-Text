"""End-to-end PCT pipeline demo: parser -> engine -> renderer.

The 5 queries are the same scenarios the standalone engine demo covered,
re-phrased in natural language.
"""
from __future__ import annotations

from pct.engine import load_kb, prove
from pct.parser import Parser
from pct.renderer import render

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import DOMAINS, RESULTS_DIR, domain, results_path  # noqa: E402


QUERIES = [
    "what is the velocity of an object after falling for 3 seconds",
    "find the kinetic energy of a 2 kg object moving at 5 m/s",
    "what force acts on a 10 kg mass accelerating at 2 m/s squared",
    "what is the kinetic energy of a 5 kg object after falling for 3 seconds",
    "what is the magnetic flux through a coil",
]


def main() -> None:
    parser = Parser(DOMAINS["physics"]["kb2"])
    kb = load_kb(DOMAINS["physics"]["kb1"])
    for i, q in enumerate(QUERIES, 1):
        print("=" * 72)
        print(f"Q{i}: {q}")
        print("=" * 72)

        parser.reset()
        parsed = parser.parse(q)

        if parsed.get("status") == "failure":
            print(render(parsed))
            print()
            continue

        goal = {
            "predicate": parsed["predicate"],
            "unit": parsed["unit"],
            "context": parsed["context"],
        }
        bindings = parsed["bindings"]
        result = prove(goal, bindings, kb)
        print(render(result))
        print()


if __name__ == "__main__":
    main()
