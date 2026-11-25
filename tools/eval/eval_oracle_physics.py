"""Oracle-parser ablation: bypass the stochastic goal-state former (Parser)
entirely and feed the unmodified engine hand-correct goal states, to isolate
engine + KB performance from parser error.

Input is the 250 in-scope queries from results/queries_300.json (the same
set eval_three_system_physics.py runs, minus the 50 OOS). For each query, a gold goal
state is built directly from the query's known structure:

  - shallow queries carry `rule_id` + `params` -- the kb1_physics.json rule's input
    list gives an exact, automatic mapping from each param to its predicate
    name and unit (no physics judgment required).
  - medium (multi-rule-chain) queries carry only `params`, with no rule_id
    (the chain isn't a single rule). Each of the 15 distinct medium query
    templates is mapped by hand below, once, from its param names to the
    predicate names a perfect parser would extract (per kb2_physics.json's
    bindings_schema), based on what the template text actually says.

The gold goal's top-level predicate/context/unit are taken directly from
the eval set's expected_predicate/expected_context/expected_unit fields.

Run:
    python tools/eval/eval_oracle_physics.py

Output:
    results/oracle_eval.json
"""
from __future__ import annotations

import json
import os
import re

from pct.engine import load_kb, prove

import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import DOMAINS, RESULTS_DIR, domain, results_path  # noqa: E402

QUERIES_PATH = DOMAINS["physics"]["queries"]["structured"]
RESULTS_PATH = results_path("physics", "oracle_eval.json")

D_MAX = 5
C_MAX = 100


def _kb_rules_by_id(kb: dict) -> dict[str, dict]:
    return {r["id"]: r for r in kb.get("rules", [])}


# -----------------------------------------------------------------------------
# Manual mapping for the 15 distinct MEDIUM (multi-rule-chain) query templates
# in build_queries_physics.py. Keyed by (expected_predicate, expected_context,
# expected_depth, sorted tuple of params.keys()) -- verified unique across
# the generated 300-query set. Each value maps a param key to the
# (binding_predicate, unit) an ideal parser would extract for that quantity,
# per kb2_physics.json's bindings_schema.
# -----------------------------------------------------------------------------
MEDIUM_BINDING_MAP: dict[tuple, dict[str, tuple[str, str]]] = {
    # KE of a falling object: "a {m} kg object ... falling for {t} seconds"
    ("kinetic_energy", "energy", 2, ("m", "t")): {
        "m": ("mass", "kg"),
        "t": ("time", "s"),
    },
    # Displacement under a net force: "mass {m} kg ... net force of {F} N ... {t} seconds"
    ("displacement", "kinematics", 2, ("F", "m", "t")): {
        "m": ("mass", "kg"),
        "F": ("net_force", "N"),
        "t": ("time", "s"),
    },
    # Velocity from impulse: "mass {m} kg ... initial velocity {v0} m/s ... impulse of {J} N*s"
    ("velocity", "newtonian_motion", 1, ("J", "m", "v0")): {
        "m": ("mass", "kg"),
        "v0": ("initial_velocity", "m/s"),
        "J": ("net_impulse", "N*s"),
    },
    # Rotational KE of a point mass: "{m} kg point mass at radius {r} m rotating at {omega} rad/s"
    ("kinetic_energy", "rotational_motion", 2, ("m", "omega", "r")): {
        "m": ("mass", "kg"),
        "r": ("radius", "m"),
        "omega": ("angular_velocity", "rad/s"),
    },
    # Angular momentum of a point mass: same surface quantities as above
    ("angular_momentum", "rotational_motion", 2, ("m", "omega", "r")): {
        "m": ("mass", "kg"),
        "r": ("radius", "m"),
        "omega": ("angular_velocity", "rad/s"),
    },
    # Acceleration down a frictionless incline: "{m} kg ... frictionless {theta} degree incline"
    ("acceleration", "newtonian_motion", 2, ("m", "theta")): {
        "m": ("mass", "kg"),
        "theta": ("incline_angle", "deg"),
    },
    # Centripetal force with v derived from omega*r: "{m} kg ... radius {r} m ... angular velocity {omega} rad/s"
    ("centripetal_force", "circular_motion", 2, ("m", "omega", "r")): {
        "m": ("mass", "kg"),
        "r": ("radius", "m"),
        "omega": ("angular_velocity", "rad/s"),
    },
    # Tension, acceleration given: "{m} kg load upward at {a} m/s squared"
    ("tension", "newtonian_motion_accelerating", 1, ("a", "m")): {
        "m": ("mass", "kg"),
        "a": ("acceleration", "m/s2"),
    },
    # Mechanical energy from v and h: "{m} kg object moving at {v} m/s at height {h} metres"
    ("mechanical_energy", "energy", 3, ("h", "m", "v")): {
        "m": ("mass", "kg"),
        "v": ("velocity", "m/s"),
        "h": ("height", "m"),
    },
    # Pendulum period on other gravity: "length {L} metres where gravity is {g} m/s squared"
    ("period", "simple_harmonic_motion", 1, ("L", "g")): {
        "L": ("length", "m"),
        "g": ("gravitational_acceleration", "m/s2"),
    },
    # Weight on other planet: "{m} kg object where gravity is {g} m/s squared"
    ("force", "gravity", 1, ("g", "m")): {
        "m": ("mass", "kg"),
        "g": ("gravitational_acceleration", "m/s2"),
    },
    # Velocity via energy conservation: "height {h0} m with velocity {v0} m/s descends to height {h} m"
    ("velocity", "energy_conservation", 1, ("h", "h0", "v0")): {
        "v0": ("initial_velocity", "m/s"),
        "h0": ("initial_height", "m"),
        "h": ("height", "m"),
    },
    # Mechanical energy from given KE and height: "kinetic energy of a {m} kg object is {KE} J ... height {h} metres"
    ("mechanical_energy", "energy", 2, ("KE", "h", "m")): {
        "KE": ("kinetic_energy", "J"),
        "m": ("mass", "kg"),
        "h": ("height", "m"),
    },
    # Acceleration from spring force: "{m} kg mass ... spring of constant {k} N/m ... displaced {x} metres"
    ("acceleration", "newtonian_motion", 2, ("k", "m", "x")): {
        "m": ("mass", "kg"),
        "k": ("spring_constant", "N/m"),
        "x": ("spring_extension", "m"),
    },
    # Power from force/displacement/time: "{F} N force over {d} metres in {t} seconds"
    ("power", "energy", 2, ("F", "d", "t")): {
        "F": ("force", "N"),
        "d": ("displacement", "m"),
        "t": ("time", "s"),
    },
}


def _medium_key(row: dict) -> tuple:
    return (
        row.get("expected_predicate"),
        row.get("expected_context"),
        row.get("expected_depth"),
        tuple(sorted(row.get("params", {}).keys())),
    )


# 2026-11-21 rule audit: special-case rules removed from kb1 in favour of the
# general rule in the same context. A query generated from a removed rule maps its
# params onto the general rule's inputs (the input names coincide); the condition
# the special case assumed comes only from STATED_CONDITIONS, when the text states it.
RULE_SUCCESSOR = {
    "kinematic_velocity_from_rest": "kinematic_velocity_with_initial",
    "kinematic_displacement_from_rest": "kinematic_displacement_with_initial",
    "torque_from_force": "torque_with_angle",
}

_COND_PATH = os.path.join(os.path.dirname(QUERIES_PATH), "gold_stated_conditions.json")


def _stated_conditions() -> dict[str, dict]:
    """query text -> condition bindings the text asserts (see
    tools/build/build_physics_gold_conditions.py)."""
    if not os.path.exists(_COND_PATH):
        return {}
    rows = json.load(open(_COND_PATH, encoding="utf-8"))["rows"]
    return {r["query"]: r["bindings"] for r in rows}


STATED_CONDITIONS = _stated_conditions()

# 2026-11-22 pre-freeze predicate audit (tools/build/migrate_physics_prefreeze.py).
# GOAL: the gold labels keep their expected_predicate; the predicate a gold goal state
# asks for is the KB's name for that quantity, keyed on (expected_predicate, context).
GOAL_PREDICATE = {
    ("force", "dynamics"): "net_force",                       # F = m a is the net force
    ("kinetic_energy", "rotational_motion"): "rotational_kinetic_energy",
    ("friction_force", "static"): "max_static_friction",
    ("potential_energy", "energy"): "gravitational_potential_energy",
    ("potential_energy", "elasticity"): "elastic_potential_energy",
    ("velocity", "simple_harmonic_motion"): "max_velocity",
}

# NET QUALIFIER wording rule: a value binds the net quantity only when the text says
# so; otherwise it binds the general quantity (which cannot fill a net_* input).
NET_WORDING = {
    "net_force": ("force", r"net force|resultant force|unbalanced force|total force"
                           r"|the only force acting"),
    "net_work": ("work", r"net work|total work|work done by the net force"),
    "net_impulse": ("impulse", r"receives? an impulse|net impulse|total impulse"),
}


def gold_goal_predicate(row: dict) -> str:
    return GOAL_PREDICATE.get((row.get("expected_predicate"), row.get("expected_context")),
                              row.get("expected_predicate"))


def net_stated(pred: str, text: str) -> bool:
    return bool(re.search(NET_WORDING[pred][1], text or "", re.I))


def build_gold_bindings(row: dict, rules_by_id: dict) -> tuple[dict | None, str]:
    """Returns (bindings, source) where source is 'auto' or 'manual', or
    (None, 'unmapped') if no mapping could be found. Stated-condition bindings
    (initial_velocity = 0 for "from rest", ...) are added where the query states them.
    A net_* binding the text does not qualify as net binds the general quantity."""
    bindings, source = _build_param_bindings(row, rules_by_id)
    if bindings is not None:
        for net, (general, _rx) in NET_WORDING.items():
            if net in bindings and not net_stated(net, row.get("query")):
                bindings[general] = bindings.pop(net)
        for k, v in STATED_CONDITIONS.get(row.get("query"), {}).items():
            bindings.setdefault(k, dict(v))
    return bindings, source


def _build_param_bindings(row: dict, rules_by_id: dict) -> tuple[dict | None, str]:
    params = row.get("params") or {}

    rule_id = row.get("rule_id")
    rule_id = RULE_SUCCESSOR.get(rule_id, rule_id)
    if rule_id and rule_id in rules_by_id:
        rule = rules_by_id[rule_id]
        bindings = {}
        for inp in rule["inputs"]:
            name = inp["name"]
            if name in params:
                bindings[inp["predicate"]] = {"value": params[name], "unit": inp["unit"]}
        return bindings, "auto"

    mapping = MEDIUM_BINDING_MAP.get(_medium_key(row))
    if mapping is not None:
        bindings = {}
        for pkey, val in params.items():
            if pkey not in mapping:
                return None, "unmapped"
            pred, unit = mapping[pkey]
            bindings[pred] = {"value": val, "unit": unit}
        return bindings, "manual"

    return None, "unmapped"


def _value_matches(expected, actual, expected_unit, actual_unit) -> bool:
    if expected is None or actual is None:
        return False
    if expected_unit is not None and actual_unit is not None and expected_unit != actual_unit:
        return False
    if not isinstance(expected, (int, float)) or not isinstance(actual, (int, float)):
        return False
    if expected == 0:
        return abs(actual) < 1e-6
    return abs(actual - expected) / abs(expected) <= 0.01


def main() -> None:
    if not os.path.exists(QUERIES_PATH):
        raise SystemExit(f"missing {QUERIES_PATH}; run `python tools/build/build_queries_physics.py` first")

    with open(QUERIES_PATH, "r", encoding="utf-8") as f:
        all_queries = json.load(f)

    kb = load_kb(DOMAINS["physics"]["kb1"])
    rules_by_id = _kb_rules_by_id(kb)

    in_scope = [(i, q) for i, q in enumerate(all_queries, 1) if q["category"] != "oos"]
    print(f"Loaded {len(all_queries)} queries total; {len(in_scope)} in-scope (excluding OOS)")

    rows = []
    n_auto = 0
    n_manual = 0
    n_unmapped = 0

    for qid, q in in_scope:
        bindings, source = build_gold_bindings(q, rules_by_id)
        if source == "auto":
            n_auto += 1
        elif source == "manual":
            n_manual += 1
        else:
            n_unmapped += 1

        goal = {
            "predicate": gold_goal_predicate(q),
            "unit": q.get("expected_unit"),
            "context": q.get("expected_context"),
        }

        if bindings is None:
            rows.append({
                "query_id": qid,
                "category": q["category"],
                "query": q["query"],
                "gold_goal": {**goal, "bindings": {}},
                "binding_source": source,
                "engine_status": "error",
                "engine_failure_type": "unmapped",
                "returned_value": None,
                "returned_unit": None,
                "expected_value": q.get("expected_value"),
                "expected_unit": q.get("expected_unit"),
                "match": False,
            })
            continue

        eng = prove(goal, bindings, kb, d_max=D_MAX, c_max=C_MAX)

        if eng.get("status") == "success":
            returned_value = eng.get("value")
            returned_unit = eng.get("unit")
            failure_type = None
        else:
            returned_value = None
            returned_unit = None
            failure_type = eng.get("type")

        match = eng.get("status") == "success" and _value_matches(
            q.get("expected_value"), returned_value, q.get("expected_unit"), returned_unit
        )

        rows.append({
            "query_id": qid,
            "category": q["category"],
            "query": q["query"],
            "gold_goal": {**goal, "bindings": bindings},
            "binding_source": source,
            "engine_status": eng.get("status"),
            "engine_failure_type": failure_type,
            "returned_value": returned_value,
            "returned_unit": returned_unit,
            "expected_value": q.get("expected_value"),
            "expected_unit": q.get("expected_unit"),
            "match": match,
        })

    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)

    n = len(rows)
    answered = sum(1 for r in rows if r["engine_status"] == "success")
    correct = sum(1 for r in rows if r["match"])
    # Match the F5 FAMILY, not the exact string: a sub-typed F5 (e.g. "F5a")
    # must still be counted here. See reconcile_f5.py for the sub-classification.
    f5_count = sum(
        1 for r in rows if str(r["engine_failure_type"] or "").startswith("F5")
    )

    failure_breakdown: dict[str, int] = {}
    for r in rows:
        ft = r["engine_failure_type"]
        if ft:
            failure_breakdown[ft] = failure_breakdown.get(ft, 0) + 1

    summary = {
        "n_in_scope": n,
        "gold_states_auto": n_auto,
        "gold_states_manual": n_manual,
        "gold_states_unmapped": n_unmapped,
        "ORACLE_ANSWERED": answered,
        "ORACLE_RATE": round(100 * answered / n, 2) if n else 0.0,
        "ORACLE_CORRECT": correct,
        "ORACLE_ACC": round(100 * correct / n, 2) if n else 0.0,
        "ORACLE_COND": round(100 * correct / answered, 2) if answered else 0.0,
        "ORACLE_F5": f5_count,
        "failure_breakdown": failure_breakdown,
    }

    output = {"summary": summary, "results": rows}
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nGold goal states built: {n_auto} automatic (shallow, rule_id-driven), "
          f"{n_manual} manual (medium, hand-mapped), {n_unmapped} unmapped")
    print()
    print("=" * 60)
    print("ORACLE-PARSER ABLATION  -  engine on hand-correct goal states")
    print("=" * 60)
    print(f"ORACLE_ANSWERED: {answered}/{n}")
    print(f"ORACLE_RATE:     {summary['ORACLE_RATE']:.2f}%")
    print(f"ORACLE_CORRECT:  {correct}/{n}")
    print(f"ORACLE_ACC:      {summary['ORACLE_ACC']:.2f}%  (correct / 250, unconditional)")
    print(f"ORACLE_COND:     {summary['ORACLE_COND']:.2f}%  (correct / answered, conditional)")
    print(f"ORACLE_F5:       {f5_count}")
    print(f"Failure breakdown: {failure_breakdown}")
    print(f"\nSaved per-query results to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
