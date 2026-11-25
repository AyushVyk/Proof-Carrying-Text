"""Stoichiometry domain on the UNMODIFIED PCT engine.

One of three scientific domains sharing pct/engine.py unmodified: physics
(numeric), biology (categorical traits plus numeric envelopes in the deep/cat
sub-tree), and chemistry -- of which this per-instance stoichiometry is a part.

THE STRUCTURE THAT MATTERS. The engine backward-chains over 12 UNIVERSAL rules
that mention no reaction and no species. Reaction-specific facts are DATA in
kb1_chemistry.json (`reactions`, `species`, `primitives`). This module resolves a
query's named reaction and species into the role-named bindings the universal
rules ask for -- coeff_reactant, molar_mass_product, and so on. That resolution
is the parser's job in the bowtie: it forms the goal state, and it is the only
place a reaction name appears. The engine never sees one.

Run:
    python tools/eval/eval_chemistry.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import DOMAINS, RESULTS_DIR, domain, results_path  # noqa: E402

from pct.engine import load_kb, prove  # noqa: E402

KB = load_kb(DOMAINS["chemistry"]["kb1"])
# the legacy 43-query structured set (used only by main()); absent under PCT_QUERY_SET=dev
_SQ = DOMAINS["chemistry"]["queries"]["structured"]
QUERIES = (json.load(open(_SQ, encoding="utf-8"))["queries"] if os.path.exists(_SQ) else [])
CONTEXT = "stoichiometry"

UNIT = {
    "moles_reactant": "mol", "moles_product": "mol", "mass_product": "g",
    "particles_product": "particles", "volume_gas_product": "L",
    "theoretical_mass_product": "g", "percent_yield": "percent",
    "moles_product_limited": "mol",
}
GIVEN_UNIT = {
    "mass_reactant": "g", "concentration": "mol/L", "volume_solution": "L",
    "mass_reactant_a": "g", "mass_reactant_b": "g", "actual_mass_product": "g",
    "gas_conditions": "condition", "other_reactants": "condition",
    "moles_reactant": "mol", "moles_product": "mol", "theoretical_mass_product": "g",
}


class Unresolved(Exception):
    """The reaction/species named by the query is not in the KB, so no goal
    state can be formed. The parser-stage analogue of a refusal."""


# 2026-11-23 (dev task 2B): the five single-step forms below convert an amount of ONE
# substance (mass <-> moles, solution -> moles, moles -> particles / gas volume). Their
# queries name no reaction, and the rules they need consume only the species' molar mass,
# avogadro, molar_volume_stp and concentration x volume -- no stoichiometric coefficient.
# The generator nevertheless stored a host reaction in their gold, chosen at random among
# the reactions containing the species (not stated in the query; sometimes with a role
# the species does not play in it). So the schema makes `reaction` OPTIONAL: a goal state
# without one resolves molar masses only. gold_goal() drops the host reaction from these
# forms' gold at load time; the query files stay byte-identical. Proved equivalent: every
# such gold state of the dev and test sets gives the same engine outcome (status, value,
# failure type, goal) with and without the host reaction.
NO_REACTION_PARTS = frozenset({"moles_from_mass", "moles_from_solution", "mass_from_moles",
                               "particles_from_moles", "gas_volume_from_moles"})


def gold_goal(q: dict) -> dict | None:
    """The gold goal state of a query, with no reaction for NO_REACTION_PARTS."""
    g = q.get("gold")
    if not g:
        return g
    if q.get("part") in NO_REACTION_PARTS:
        return {**g, "reaction": None}
    return g


def resolve(q: dict) -> dict:
    """Turn a named reaction + species roles into role-named bindings, using
    ONLY the KB's own reaction and species data. With no reaction (None), only each
    role's molar mass is supplied: no coefficient, so no rule that relates two species
    can fire."""
    rid = q.get("reaction")
    rx = None
    if rid is not None:
        if rid not in KB["reactions"]:
            raise Unresolved(f"no reaction {rid!r} in the KB")
        rx = KB["reactions"][rid]
    bnd: dict = {}
    for role, sp in q["roles"].items():
        if sp not in KB["species"]:
            raise Unresolved(f"species {sp!r} has no composition/molar mass in the KB")
        bnd[f"molar_mass_{role}"] = {
            "value": KB["species"][sp]["molar_mass"], "unit": "g/mol"}
        if rx is None:
            continue
        c = rx["reactants"].get(sp) or rx["products"].get(sp)
        if c is None:
            raise Unresolved(f"species {sp!r} does not appear in reaction {rid!r}")
        bnd[f"coeff_{role}"] = {"value": c, "unit": "dimensionless"}
    for k, v in q["given"].items():
        bnd[k] = {"value": v, "unit": GIVEN_UNIT.get(k, "")}
    # mole_ratio needs every OTHER reactant in excess (categorical other_reactants).
    # A single-reactant reaction has no other reactant, so the condition holds
    # vacuously: supplied here from the KB's reaction data, like the coefficients.
    if rx is not None and "reactant" in q["roles"] and len(rx["reactants"]) == 1             and "other_reactants" not in bnd:
        bnd["other_reactants"] = {"value": "excess", "unit": "condition"}
    return bnd


def close(a, b, tol=0.01):
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    return abs(a) < 1e-9 if b == 0 else abs(a - b) / abs(b) <= tol


def main() -> int:
    rules = KB["rules"]
    n_data = len(KB["primitives"]) + len(KB["reactions"]) + len(KB["species"])
    print("=" * 96)
    print("PCT STOICHIOMETRY DOMAIN  --  universal rules + reaction data, unmodified engine")
    print("=" * 96)
    print(f"  UNIVERSAL RULES : {len(rules)}")
    print(f"  DATA            : {len(KB['primitives'])} primitives + "
          f"{len(KB['reactions'])} reactions + {len(KB['species'])} species = {n_data}")
    print(f"  rules naming a reaction or species: "
          f"{sum(1 for r in rules for n in list(KB['reactions']) + list(KB['species']) if n in json.dumps(r))}")
    print(f"  context_transitions={KB['context_transitions']}  "
          f"predicate_aliases={KB['predicate_aliases']}\n")

    correct = 0
    answered = refused_engine = refused_resolution = 0
    ftypes: Counter = Counter()
    depths: Counter = Counter()
    chain_ok = chain_bad = 0
    rows = []

    for q in QUERIES:
        try:
            bnd = resolve(q)
        except Unresolved as e:
            got = "refuse"
            ok = q["expected"] == "refuse" and q.get("refusal_stage") == "resolution"
            refused_resolution += 1
            correct += ok
            rows.append((q, got, ok, None, str(e)))
            print(f"[{'ok ' if ok else 'FAIL'}] Q{q['id']:<3} {q['form']:<32} "
                  f"-> RESOLUTION REFUSAL: {e}")
            continue

        goal = {"predicate": q["goal"], "unit": UNIT[q["goal"]], "context": CONTEXT}
        res = prove(goal, bnd, KB, d_max=8)

        if res.get("status") == "success":
            answered += 1
            fired = [s["id"] for s in res["derivation"] if s["type"] == "rule"]
            depths[len(fired)] += 1
            got = res["value"]
            ok = close(got, q["expected"])
            if "expected_rules" in q:
                if sorted(fired) == sorted(q["expected_rules"]):
                    chain_ok += 1
                else:
                    chain_bad += 1
                    ok = False
            correct += ok
            rows.append((q, got, ok, fired, None))
            print(f"[{'ok ' if ok else 'FAIL'}] Q{q['id']:<3} {q['form']:<32} "
                  f"-> {got:<22.6g} (exp {q['expected']:.6g})  {len(fired)} rules")
        else:
            refused_engine += 1
            ftypes[res.get("type")] += 1
            got = "refuse"
            ok = q["expected"] == "refuse" and q.get("refusal_stage") == "engine"
            correct += ok
            rows.append((q, got, ok, None, res.get("reason")))
            print(f"[{'ok ' if ok else 'FAIL'}] Q{q['id']:<3} {q['form']:<32} "
                  f"-> {res.get('type')}  {str(res.get('reason'))[:44]}")

    n = len(QUERIES)
    print()
    print("-" * 96)
    print(f"accuracy                  : {correct}/{n} ({100*correct/n:.1f}%)")
    print(f"engine answered           : {answered}/{n}")
    print(f"engine refused            : {refused_engine}/{n}  types {dict(ftypes)}")
    print(f"resolution-stage refused  : {refused_resolution}/{n}  (parser-stage analogue)")
    print(f"exact rule chain matched  : {chain_ok} ok, {chain_bad} mismatched")
    print(f"derivation depth (rules)  : {dict(sorted(depths.items()))}")

    # limiting-reactant guard behaviour
    print()
    print("-" * 96)
    print("LIMITING-REACTANT GUARD SELECTION (both rules output moles_product_limited;")
    print("the guards are mutually exclusive, so the guard alone picks the branch)")
    print("-" * 96)
    for q, got, ok, fired, _ in rows:
        if q["form"] in ("limiting_theoretical", "percent_yield") and fired:
            picked = next((r for r in fired if r.startswith("limiting_product_from")), "-")
            print(f"  Q{q['id']:<3} {q['reaction']:<34} limiting={q['limiting']:<8} "
                  f"rule fired: {picked}")

    # a full worked trace
    print()
    print("=" * 96)
    print("WORKED TRACE  (deepest chain)")
    print("=" * 96)
    deepest = max((r for r in rows if r[3]), key=lambda r: len(r[3]))
    q, got, ok, fired, _ = deepest
    print(f"  {q['question']}")
    print(f"  reaction data used: {KB['reactions'][q['reaction']]['equation']}")
    res = prove({"predicate": q["goal"], "unit": UNIT[q["goal"]], "context": CONTEXT},
                resolve(q), KB, d_max=8)
    for s in res["derivation"]:
        if s["type"] == "rule":
            ins = ", ".join(f"{k}={v['value']:.6g}" for k, v in s["inputs"].items())
            print(f"    rule      {s['id']}")
            print(f"              {s['formula']}  [{ins}]  ->  {s['output']:.6g}")
        elif s["type"] == "primitive":
            print(f"    primitive {s['id']}: {s['predicate']} = {s['value']}")
        else:
            print(f"    binding   {s['predicate']} = {s['value']} {s['unit']}")
    print(f"  = {res['value']:.6g} {res['unit']}   (expected {q['expected']:.6g})")

    print()
    print("-" * 96)
    print(f"{'PASS' if correct == n else 'FAIL'}: {correct}/{n}")
    return 0 if correct == n else 1


if __name__ == "__main__":
    sys.exit(main())
