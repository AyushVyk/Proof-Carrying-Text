"""Which coefficients can a reaction be balanced from, one coefficient at a time?

For each reaction, build one conservation equation per element. Fix one coefficient
to 1, then repeatedly take an equation in which exactly one coefficient is still
unknown and solve it for that coefficient (one equation, one unknown: a single
division). The reaction "starts from" that coefficient if this reaches every
coefficient. No linear solver is used to find coefficients. Exact rank arithmetic is
used only afterwards, to verify that the result balances every element and is the
reaction's unique balance up to scale.

Run:
    python experiments/balancing/analyse_sequential.py
"""
from __future__ import annotations

import math
import re
import sys
from fractions import Fraction

REACTIONS = [
    ("H2 + O2 -> H2O", None),
    ("CH4 + O2 -> CO2 + H2O", {"CH4", "CO2", "H2O"}),
    ("C3H8 + O2 -> CO2 + H2O", 3),
    ("Fe + O2 -> Fe2O3", None),
    ("KMnO4 + HCl -> KCl + MnCl2 + H2O + Cl2", 5),
    ("P4 + P2I4 + H2O -> PH4I + H3PO4", {"H2O", "H3PO4"}),
    ("Fe2O3 + FeO -> Fe3O4", set()),
    ("NO + NO2 -> N2O3", set()),
]
# expected: None = every coefficient; int = that many of them; set = exactly those


def atoms(formula: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for el, n in re.findall(r"([A-Z][a-z]?)(\d*)", formula):
        out[el] = out.get(el, 0) + (int(n) if n else 1)
    return out


def equations(reaction: str):
    lhs, rhs = (side.split(" + ") for side in reaction.split(" -> "))
    species = lhs + rhs
    sign = {s: 1 for s in lhs} | {s: -1 for s in rhs}
    elements = sorted({e for s in species for e in atoms(s)})
    # one equation per element: sum_s sign(s) * atoms(e in s) * c_s = 0
    eqs = {e: {s: sign[s] * atoms(s)[e] for s in species if e in atoms(s)} for e in elements}
    return species, eqs


def sequential(species, eqs, start):
    """Derive coefficients one at a time from `start` = 1. Returns (values, steps)."""
    known = {start: Fraction(1)}
    steps = [f"{start} = 1 (fixed)"]
    progress = True
    while progress and len(known) < len(species):
        progress = False
        for el, eq in eqs.items():
            unknown = [s for s in eq if s not in known]
            if len(unknown) != 1:
                continue
            u = unknown[0]
            known[u] = -sum(eq[s] * known[s] for s in eq if s != u) / eq[u]
            steps.append(f"{u} = {known[u]} (from {el} balance)")
            progress = True
    return known, steps


def rank(rows):
    """Exact rank by Gaussian elimination: VERIFICATION ONLY, never used to derive."""
    m = [list(r) for r in rows]
    r = 0
    for c in range(len(m[0]) if m else 0):
        p = next((i for i in range(r, len(m)) if m[i][c] != 0), None)
        if p is None:
            continue
        m[r], m[p] = m[p], m[r]
        for i in range(len(m)):
            if i != r and m[i][c] != 0:
                f = m[i][c] / m[r][c]
                m[i] = [a - f * b for a, b in zip(m[i], m[r])]
        r += 1
    return r


def verify(species, eqs, values):
    if len(values) < len(species) or any(v <= 0 for v in values.values()):
        return False
    if any(sum(eq[s] * values[s] for s in eq) != 0 for eq in eqs.values()):
        return False
    rows = [[Fraction(eq.get(s, 0)) for s in species] for eq in eqs.values()]
    return rank(rows) == len(species) - 1          # unique balance up to scale


def integers(species, values):
    den = math.lcm(*(v.denominator for v in values.values()))
    ints = [int(values[s] * den) for s in species]
    g = math.gcd(*ints)
    return [i // g for i in ints]


def main() -> int:
    rows, all_match = [], True
    for reaction, expected in REACTIONS:
        species, eqs = equations(reaction)
        starts, balance = [], None
        print("=" * 78)
        print(reaction)
        for el, eq in eqs.items():
            print(f"  {el}: " + " + ".join(f"{c}*{s}" for s, c in eq.items()) + " = 0")
        for s in species:
            values, steps = sequential(species, eqs, s)
            ok = verify(species, eqs, values)
            print(f"  start {s:<6}: {'derives all' if ok else 'stalls'}")
            for st in steps[1:]:
                print(f"      {st}")
            if ok:
                starts.append(s)
                balance = integers(species, values)
        if expected is None:
            match = len(starts) == len(species)
        elif isinstance(expected, int):
            match = len(starts) == expected
        else:
            match = set(starts) == expected
        all_match &= match
        if balance:
            print("  balanced: " + " + ".join(f"{n} {s}" for n, s in zip(balance, species)))
        rows.append((reaction, starts, len(species), match))
    print()
    print("| reaction | coefficients it can start from | match |")
    print("|---|---|---|")
    for reaction, starts, n, match in rows:
        print(f"| {reaction} | {', '.join(starts) or 'none'} ({len(starts)} of {n}) "
              f"| {'yes' if match else 'NO'} |")
    return 0 if all_match else 1


if __name__ == "__main__":
    sys.exit(main())
