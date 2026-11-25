"""Run the five demo queries against the PCT engine and print results."""
from __future__ import annotations

import json

from pct.engine import load_kb, prove

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import DOMAINS, RESULTS_DIR, domain, results_path  # noqa: E402


QUERIES = [
    (
        "Q1: velocity after free fall (t=3s, g=9.8)",
        {"predicate": "velocity", "unit": "m/s", "context": "free_fall"},
        {"time": {"value": 3, "unit": "s"}, "acceleration": {"value": 9.8, "unit": "m/s2"}},
    ),
    (
        "Q2: kinetic energy (m=2kg, v=5m/s)",
        {"predicate": "kinetic_energy", "unit": "J", "context": "energy"},
        {"mass": {"value": 2, "unit": "kg"}, "velocity": {"value": 5, "unit": "m/s"}},
    ),
    (
        "Q3: force from Newton's second law (m=10kg, a=2m/s2)",
        {"predicate": "force", "unit": "N", "context": "dynamics"},
        {"mass": {"value": 10, "unit": "kg"}, "acceleration": {"value": 2, "unit": "m/s2"}},
    ),
    (
        "Q4: KE after 3s free fall (m=5kg, t=3s, g=9.8)",
        {"predicate": "kinetic_energy", "unit": "J", "context": "energy"},
        {
            "mass": {"value": 5, "unit": "kg"},
            "time": {"value": 3, "unit": "s"},
            "acceleration": {"value": 9.8, "unit": "m/s2"},
        },
    ),
    (
        "Q5: out-of-scope (magnetic flux)",
        {"predicate": "magnetic_flux", "unit": "Wb", "context": "electromagnetism"},
        {},
    ),
]


def _fmt_step(s: dict) -> str:
    t = s["type"]
    n = s["step"]
    if t == "binding":
        return f"  [{n}] binding   {s['predicate']} = {s['value']} {s['unit']}"
    if t == "primitive":
        return f"  [{n}] primitive {s['id']}: {s['predicate']} = {s['value']} {s['unit']}"
    if t == "rule":
        ins = ", ".join(
            f"{k}={v['value']} {v['unit']}" if isinstance(v, dict) else f"{k}={v}"
            for k, v in s["inputs"].items()
        )
        return (
            f"  [{n}] rule      {s['id']}\n"
            f"        formula : {s['formula']}\n"
            f"        inputs  : {ins}\n"
            f"        output  : {s['output']} {s['unit']}\n"
            f"        proof   : {s['proof']}"
        )
    return f"  [{n}] {t} {s}"


def _print_result(label: str, goal: dict, result: dict) -> None:
    print(f"\n=== {label} ===")
    print(f"goal: {goal}")
    if result["status"] == "success":
        print(f"RESULT: {result['value']} {result['unit']}")
        print("derivation:")
        for s in result["derivation"]:
            print(_fmt_step(s))
    else:
        print(f"FAILURE [{result['type']}]: {result['reason']}")
        extras = {k: v for k, v in result.items() if k not in {"status", "type", "reason"}}
        if extras:
            print(f"  details: {json.dumps(extras, default=str)}")


def main() -> None:
    kb = load_kb(DOMAINS["physics"]["kb1"])
    print(f"Loaded KB: {len(kb['primitives'])} primitives, {len(kb['rules'])} rules")
    for label, goal, bindings in QUERIES:
        result = prove(goal, bindings, kb)
        _print_result(label, goal, result)


if __name__ == "__main__":
    main()
