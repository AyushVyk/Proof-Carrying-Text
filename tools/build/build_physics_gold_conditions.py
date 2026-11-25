"""Gold stated-condition bindings for the physics query set (2026-11-21 rule audit).

After the audit, some rules take a condition as an input (initial_velocity = 0 for
release from rest, incline_angle = 0 for a flat surface, collision_type, ...). A
gold goal state carries such a binding ONLY when the query text asserts the
condition -- the test is "does the text assert it", not "is it compatible with it".
Gold labels and expected values are never changed: a query that does not state a
condition its rule needs is an in-scope query whose correct engine output is F5.

For each in-scope query this script finds the condition predicates its gold rule
chain consumes, tests the query text for an assertion of each, and writes
domains/physics/queries/gold_stated_conditions.json: the bindings to add, the
phrase that states each one, and every needed-but-unstated condition.

Run:
    python tools/build/build_physics_gold_conditions.py
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import DOMAINS  # noqa: E402

QUERIES = DOMAINS["physics"]["queries"]["structured"]
OUT = os.path.join(os.path.dirname(QUERIES), "gold_stated_conditions.json")

# condition predicate -> (value, unit, assertion regex)
COND = {
    "initial_velocity": (0, "m/s",
                         r"from rest|at rest|\bdropped\b|released from rest|starts? motionless"),
    "torque_angle": (90, "deg", r"perpendicular"),
    "incline_angle": (0, "deg", r"\bflat\b|\blevel\b|horizontal"),
    "acceleration": (0, "m/s2",
                     r"at rest|stationary|motionless|\bstill\b|equilibrium|constant velocity"
                     r"|steady speed"),
    "friction_force": (0, "N", r"frictionless|smooth"),
    "applied_force_angle": (0, "deg", r"horizontal|parallel"),
    "nonconservative_work": ("none", "condition",
                             r"frictionless|smooth|(ignor|neglect)\w* (friction|air resistance)"
                             r"|conservation of mechanical energy|mechanical energy is conserved"),
    "pendulum_amplitude": ("small", "condition", r"small (angle|amplitude|oscillation)"),
}
COLLISION = [("perfectly_inelastic",
              r"perfectly inelastic|stick together|move together after|coalesce"),
             ("elastic", r"(?<!in)elastic")]

# gold rule / template -> condition predicates its chain consumes beyond `params`
NEEDS_BY_RULE = {
    "kinematic_velocity_from_rest": ["initial_velocity"],
    "kinematic_displacement_from_rest": ["initial_velocity"],
    "free_fall_velocity": ["initial_velocity"],
    "free_fall_displacement": ["initial_velocity"],
    "torque_from_force": ["torque_angle"],
    "normal_force_flat": ["incline_angle"],
    "tension_hanging_mass": ["acceleration"],
    "net_force_incline_no_friction": ["friction_force"],
    "net_force_with_friction_flat": ["applied_force_angle"],
    "conservation_mechanical_energy": ["nonconservative_work"],
    "period_pendulum": ["pendulum_amplitude"],
    "elastic_collision_v1": ["collision_type"],
    "elastic_collision_v2": ["collision_type"],
    "perfectly_inelastic_collision": ["collision_type"],
}
NEEDS_BY_TEMPLATE = {   # medium templates: (predicate, context, sorted params)
    ("kinetic_energy", "energy", ("m", "t")): ["initial_velocity"],
    ("displacement", "kinematics", ("F", "m", "t")): ["initial_velocity"],
    ("acceleration", "newtonian_motion", ("m", "theta")): ["friction_force"],
    ("velocity", "energy_conservation", ("h", "h0", "v0")): ["nonconservative_work"],
    ("period", "simple_harmonic_motion", ("L", "g")): ["pendulum_amplitude"],
}


def needs(q: dict) -> list[str]:
    if q.get("rule_id"):
        return NEEDS_BY_RULE.get(q["rule_id"], [])
    key = (q["expected_predicate"], q["expected_context"], tuple(sorted(q["params"])))
    return NEEDS_BY_TEMPLATE.get(key, [])


def main() -> int:
    qs = json.load(open(QUERIES, encoding="utf-8"))
    rows, unstated = [], []
    for i, q in enumerate(qs, 1):
        if q["category"] == "oos":
            continue
        text = q["query"]
        bnd, ev = {}, {}
        for pred in needs(q):
            if pred == "collision_type":
                for val, rx in COLLISION:
                    m = re.search(rx, text, re.I)
                    if m:
                        bnd[pred] = {"value": val, "unit": "condition"}
                        ev[pred] = m.group(0)
                        break
            else:
                val, unit, rx = COND[pred]
                m = re.search(rx, text, re.I)
                if m:
                    bnd[pred] = {"value": val, "unit": unit}
                    ev[pred] = m.group(0)
            if pred not in bnd:
                unstated.append({"id": i, "query": text, "needs": pred})
        if bnd:
            rows.append({"id": i, "query": text, "bindings": bnd, "stated_by": ev})
    out = {
        "_note": ("Condition bindings added to physics gold goal states by the 2026-11-21 "
                  "rule audit, only where the query text asserts the condition. Written by "
                  "tools/build/build_physics_gold_conditions.py; read by "
                  "tools/eval/eval_oracle_physics.build_gold_bindings (matched on query "
                  "text). `unstated` lists queries whose gold rule needs a condition the "
                  "text does not state: their correct engine output is a typed failure. "
                  "Gold labels and expected values are unchanged."),
        "rows": rows, "unstated": unstated}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{len(rows)} gold goal states gain a stated condition; "
          f"{len(unstated)} needed conditions are unstated")
    for r in rows:
        print(f"  q{r['id']:<4} {r['bindings']}  <- {r['stated_by']}")
    for u in unstated:
        print(f"  UNSTATED q{u['id']:<4} {u['needs']:<22} {u['query'][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
