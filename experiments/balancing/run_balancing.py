"""Chemical balancing as rule derivation on the UNMODIFIED PCT engine.

Coefficients are DERIVED. The query supplies only a basis (one coefficient set
to 1, e.g. "per mole of CH4"); molecular composition comes from KB primitives.
No solver, no linear algebra, no search: each rule is one element's conservation
equation rearranged for one coefficient, evaluated as a single expression.

Run:
    python experiments/balancing/run_balancing.py
"""
from __future__ import annotations

import os
import sys
from fractions import Fraction

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pct.engine import load_kb, prove  # noqa: E402

KB = load_kb(os.path.join(_HERE, "kb_balancing.json"))


def coeff(name: str, context: str, basis: dict, d_max: int = 8):
    goal = {"predicate": f"coeff_{name}", "unit": "coefficient", "context": context}
    bnd = {f"coeff_{k}": {"value": v, "unit": "coefficient"} for k, v in basis.items()}
    return prove(goal, bnd, KB, d_max=d_max)


def show(label, context, basis, targets, accepted):
    print("=" * 96)
    print(label)
    print(f"  basis supplied by the query : {basis}   (a normalisation choice, not an answer)")
    print("=" * 96)
    derived = dict(basis)
    for t in targets:
        r = coeff(t, context, basis)
        if r.get("status") != "success":
            print(f"\n  coeff_{t}: {r.get('type')}  {r.get('reason')}")
            if r.get("cycle"):
                print(f"           cycle=True  <-- the engine detected the coupling")
            derived[t] = None
            continue
        derived[t] = r["value"]
        rules = [s["id"] for s in r["derivation"] if s["type"] == "rule"]
        print(f"\n  coeff_{t} = {r['value']}   ({len(rules)} rules)")
        for s in r["derivation"]:
            if s["type"] == "rule":
                ins = ", ".join(f"{k}={v['value']}" for k, v in s["inputs"].items())
                print(f"      rule {s['id']}")
                print(f"           {s['formula']}   with [{ins}]  ->  {s['output']}")
            elif s["type"] == "primitive":
                print(f"      primitive {s['id']}: {s['predicate']} = {s['value']}")
            else:
                print(f"      binding   {s['predicate']} = {s['value']} (basis)")

    if all(v is not None for v in derived.values()):
        fr = {k: Fraction(v).limit_denominator(10**6) for k, v in derived.items()}
        den = 1
        import math
        for v in fr.values():
            den = den * v.denominator // math.gcd(den, v.denominator)
        ints = {k: int(v * den) for k, v in fr.items()}
        g = 0
        for v in ints.values():
            g = math.gcd(g, abs(v))
        ints = {k: v // g for k, v in ints.items()}
        print(f"\n  derived ratios     : { {k: str(v) for k, v in fr.items()} }")
        print(f"  smallest integers  : {ints}")
        print(f"  accepted answer    : {accepted}")
        print(f"  MATCH: {ints == accepted}")
        print("  NOTE: the integer scaling above is done HERE, not by a rule -- it needs")
        print("        the whole coefficient set at once (an LCM over all denominators),")
        print("        which no fixed-arity rule can express.")
    print()


show("REACTION 1  H2 + O2 -> H2O", "hydrogen_combustion",
     {"H2": 1}, ["H2O", "O2"], {"H2": 2, "H2O": 2, "O2": 1})

show("REACTION 2  CH4 + O2 -> CO2 + H2O", "methane_combustion",
     {"CH4": 1}, ["CO2", "H2O", "O2"], {"CH4": 1, "CO2": 1, "H2O": 2, "O2": 2})

show("REACTION 3  NO + NO2 -> N2O3   (propagation stalls from every basis)",
     "nitrogen_oxide_synthesis", {"NO": 1}, ["NO2", "N2O3"],
     {"NO": 1, "NO2": 1, "N2O3": 1})

print("=" * 96)
print("REACTION 3, every basis choice tried")
print("=" * 96)
for basis_species in ("NO", "NO2", "N2O3"):
    for target in ("NO", "NO2", "N2O3"):
        if target == basis_species:
            continue
        r = coeff(target, "nitrogen_oxide_synthesis", {basis_species: 1})
        tag = f"{r.get('type')}" + (" cycle" if r.get("cycle") else "")
        val = r["value"] if r.get("status") == "success" else "-"
        print(f"  basis coeff_{basis_species}=1  ->  coeff_{target:<5} : "
              f"{'SUCCESS ' + str(val) if r.get('status') == 'success' else tag:<12} "
              f"{str(r.get('reason', ''))[:58]}")
