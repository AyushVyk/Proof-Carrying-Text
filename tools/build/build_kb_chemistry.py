"""Generate domains/chemistry/kb1_chemistry.json and domains/chemistry/kb2_chemistry.json from domains/chemistry/source.json.

Design principle: reaction-specific facts are DATA; rules are UNIVERSAL LAWS.

  * Rules (12) are species- and reaction-agnostic. Each is one arithmetic
    expression over role-named inputs (reactant / product / reactant_a /
    reactant_b), exactly like the physics rules. None mentions a reaction or a
    species. Adding a reaction adds NO rules.
  * Reaction data (coefficients + species compositions) lives in the KB's
    `reactions` and `species` fields, and molar masses in `primitives`. Adding a
    reaction is one cheap data entry.

How a rule reaches reaction-specific data (the crux):
  The engine matches primitives by predicate name alone and IGNORES context
  (see engine._find_primitive), so a primitive cannot vary per reaction. The
  only per-call channel is bindings. So the universal predicates a rule asks
  for -- coeff_reactant, molar_mass_product, ... -- are satisfied from BINDINGS,
  and tools/eval/eval_chemistry.py resolves them by looking up the named reaction and species
  in this KB's own data before calling prove(). That resolution step is the
  parser's job in the bowtie, not the engine's: the engine still only
  backward-chains over universal rules.
  The two genuinely universal constants, `avogadro` and `molar_volume_stp`, have
  fixed predicate names and so ARE read straight from primitives by the rules.

Balancing is NOT part of this domain -- coefficients are verified source data.
This generator re-verifies each reaction's element conservation and refuses to
write the KB if any reaction does not balance.

Run:
    python tools/build/build_kb_chemistry.py
"""
from __future__ import annotations

import json
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import DOMAINS, RESULTS_DIR, domain, results_path  # noqa: E402
SRC = DOMAINS["chemistry"]["sources"]["species_and_reactions"]
KB1 = DOMAINS["chemistry"]["kb1"]
KB2 = DOMAINS["chemistry"]["kb2"]
CONTEXT = "stoichiometry"

LAW = ("Law of definite proportions / conservation of mass: a balanced equation's "
       "coefficients give the fixed mole ratios in which species react. "
       "OpenStax Chemistry 2e, Ch 4 (Stoichiometry of Chemical Reactions).")


def molar_mass(comp: dict, aw: dict) -> float:
    return round(sum(aw[el] * n for el, n in comp.items()), 6)


def verify_balanced(rx: dict, comps: dict) -> list[str]:
    """Return a list of complaints; empty means the reaction balances."""
    bad = []
    elements = {e for s in list(rx["reactants"]) + list(rx["products"])
                for e in comps[s]}
    for el in sorted(elements):
        left = sum(c * comps[s].get(el, 0) for s, c in rx["reactants"].items())
        right = sum(c * comps[s].get(el, 0) for s, c in rx["products"].items())
        if left != right:
            bad.append(f"{el}: {left} != {right}")
    return bad


def rule(rid, pred, unit, inputs, formula, proof, guard=None):
    r = {
        "id": rid, "predicate": pred, "context": CONTEXT,
        "inputs": [{"name": n, "predicate": n, "unit": u} for n, u in inputs],
        "output": {"predicate": pred, "unit": unit},
    }
    if guard:
        r["guard"] = guard
    r["formula"] = formula
    r["proof"] = proof
    return r


def universal_rules() -> list[dict]:
    return [
        rule("moles_from_mass", "moles_reactant", "mol",
             [("mass_reactant", "g"), ("molar_mass_reactant", "g/mol")],
             "mass_reactant / molar_mass_reactant",
             "n = m / M. The amount of substance is the mass divided by the molar "
             "mass. Guard: molar mass must be non-zero. OpenStax Chemistry 2e, "
             "Ch 3.1 (Formula Mass and the Mole Concept).",
             guard="molar_mass_reactant != 0"),

        rule("moles_from_concentration_volume", "moles_reactant", "mol",
             [("concentration", "mol/L"), ("volume_solution", "L")],
             "concentration * volume_solution",
             "n = c x V. Molarity is moles of solute per litre of solution, so "
             "moles equals molarity times volume. OpenStax Chemistry 2e, Ch 3.3 "
             "(Molarity)."),

        rule("mole_ratio", "moles_product", "mol",
             [("moles_reactant", "mol"), ("coeff_reactant", "dimensionless"),
              ("coeff_product", "dimensionless"), ("other_reactants", "condition")],
             "moles_reactant * coeff_product / coeff_reactant",
             "The stoichiometric coefficients of a balanced equation are the mole "
             "ratio in which species are consumed and formed, so "
             "n(product) = n(reactant) x (coeff_product / coeff_reactant). "
             "Coefficients come from this KB's verified reaction DATA, not from a "
             "rule. Guard: the reactant coefficient must be non-zero. " + LAW +
             " CONDITION (2026-11-22): the given reactant fixes the product amount only "
             "if it is the limiting reactant, i.e. every other reactant is in excess; "
             "other_reactants is a categorical input guarded == 'excess', bound only "
             "when the query states it ('excess <reactant>', 'reacts completely', 'is "
             "completely consumed', 'is used up'), or from reaction data when the "
             "reaction has a single reactant (no other reactant: vacuously true).",
             guard="coeff_reactant != 0 and other_reactants == 'excess'"),

        rule("mass_from_moles", "mass_product", "g",
             [("moles_product", "mol"), ("molar_mass_product", "g/mol")],
             "moles_product * molar_mass_product",
             "m = n x M, the inverse of n = m / M. OpenStax Chemistry 2e, Ch 3.1."),

        rule("particles_from_moles", "particles_product", "particles",
             [("moles_product", "mol"), ("avogadro", "1/mol")],
             "moles_product * avogadro",
             "N = n x N_A. One mole contains exactly N_A constituent particles, "
             "N_A being an SI defining constant since the 2019 revision of the SI. "
             "OpenStax Chemistry 2e, Ch 3.1."),

        rule("gas_volume_from_moles_stp", "volume_gas_product", "L",
             [("moles_product", "mol"), ("molar_volume_stp", "L/mol"),
              ("gas_conditions", "condition")],
             "moles_product * molar_volume_stp",
             "V = n x V_m. At a stated temperature and pressure one mole of an "
             "ideal gas occupies the molar volume V_m; this KB's primitive is the "
             "modern IUPAC STP value (0 degC, 100 kPa). OpenStax Chemistry 2e, "
             "Ch 9.3 (Stoichiometry of Gaseous Substances). CONDITION: the molar "
             "volume holds only at STP; gas_conditions is a categorical input "
             "guarded == 'stp', bound only when the query states STP.",
             guard="gas_conditions == 'stp'"),

        rule("moles_a_from_mass", "moles_reactant_a", "mol",
             [("mass_reactant_a", "g"), ("molar_mass_reactant_a", "g/mol")],
             "mass_reactant_a / molar_mass_reactant_a",
             "n = m / M applied to the first of two reactants in a "
             "limiting-reactant problem. OpenStax Chemistry 2e, Ch 3.1.",
             guard="molar_mass_reactant_a != 0"),

        rule("moles_b_from_mass", "moles_reactant_b", "mol",
             [("mass_reactant_b", "g"), ("molar_mass_reactant_b", "g/mol")],
             "mass_reactant_b / molar_mass_reactant_b",
             "n = m / M applied to the second of two reactants in a "
             "limiting-reactant problem. OpenStax Chemistry 2e, Ch 3.1.",
             guard="molar_mass_reactant_b != 0"),

        rule("limiting_product_from_reactant_a", "moles_product_limited", "mol",
             [("moles_reactant_a", "mol"), ("coeff_reactant_a", "dimensionless"),
              ("moles_reactant_b", "mol"), ("coeff_reactant_b", "dimensionless"),
              ("coeff_product", "dimensionless")],
             "moles_reactant_a / coeff_reactant_a * coeff_product",
             "Limiting-reactant determination. Dividing each reactant's amount by "
             "its coefficient gives how many 'reaction units' each could supply; "
             "the smaller quotient limits the reaction. This rule APPLIES when "
             "reactant A is limiting, which is exactly what its guard states, and "
             "then the product amount follows from A's mole ratio. The applicability "
             "condition is in the guard, not the formula, so a non-match is a typed "
             "non-match rather than an error. OpenStax Chemistry 2e, Ch 4.4 "
             "(Reaction Yields).",
             guard=("coeff_reactant_a != 0 and coeff_reactant_b != 0 and "
                    "moles_reactant_a / coeff_reactant_a <= "
                    "moles_reactant_b / coeff_reactant_b")),

        rule("limiting_product_from_reactant_b", "moles_product_limited", "mol",
             [("moles_reactant_a", "mol"), ("coeff_reactant_a", "dimensionless"),
              ("moles_reactant_b", "mol"), ("coeff_reactant_b", "dimensionless"),
              ("coeff_product", "dimensionless")],
             "moles_reactant_b / coeff_reactant_b * coeff_product",
             "The complementary case of limiting_product_from_reactant_a: this rule "
             "applies when reactant B supplies the smaller number of reaction units "
             "and therefore limits. The two guards are mutually exclusive and "
             "jointly exhaustive over non-zero coefficients, so exactly one applies. "
             "OpenStax Chemistry 2e, Ch 4.4.",
             guard=("coeff_reactant_a != 0 and coeff_reactant_b != 0 and "
                    "moles_reactant_b / coeff_reactant_b < "
                    "moles_reactant_a / coeff_reactant_a")),

        rule("theoretical_mass_from_limited_moles", "theoretical_mass_product", "g",
             [("moles_product_limited", "mol"), ("molar_mass_product", "g/mol")],
             "moles_product_limited * molar_mass_product",
             "Theoretical yield: the mass of product obtainable if the limiting "
             "reactant is consumed completely, m = n x M. OpenStax Chemistry 2e, "
             "Ch 4.4 (Reaction Yields)."),

        rule("percent_yield_from_masses", "percent_yield", "percent",
             [("actual_mass_product", "g"), ("theoretical_mass_product", "g")],
             "actual_mass_product / theoretical_mass_product * 100",
             "Percent yield = (actual yield / theoretical yield) x 100. Guard: the "
             "theoretical yield must be non-zero. OpenStax Chemistry 2e, Ch 4.4.",
             guard="theoretical_mass_product != 0"),
    ]


def main() -> None:
    src = json.load(open(SRC, encoding="utf-8"))
    aw = src["atomic_weights"]
    comps = src["compositions"]
    reactions = src["reactions"]

    # ---- gate the build on element conservation --------------------------
    problems = {}
    for rx in reactions:
        bad = verify_balanced(rx, comps)
        if bad:
            problems[rx["id"]] = bad
    if problems:
        for k, v in problems.items():
            print(f"  UNBALANCED {k}: {v}")
        raise SystemExit("refusing to write: source reactions do not balance")
    print(f"verified: all {len(reactions)} source reactions balance for every element")

    # ---- primitives: universal constants + computed molar masses ---------
    prims = [
        {"id": "avogadro_constant", "predicate": "avogadro", "context": "constants",
         "value": src["constants"]["avogadro"]["value"], "unit": "1/mol",
         "source": src["constants"]["avogadro"]["source"]},
        {"id": "molar_volume_stp", "predicate": "molar_volume_stp",
         "context": "constants",
         "value": src["constants"]["molar_volume_stp"]["value"], "unit": "L/mol",
         "source": (src["constants"]["molar_volume_stp"]["source"] + " Conditions: "
                    + src["constants"]["molar_volume_stp"]["conditions"] + ". "
                    + src["constants"]["molar_volume_stp"]["note"])},
    ]
    species = {}
    for name, comp in sorted(comps.items()):
        mm = molar_mass(comp, aw)
        species[name] = {"composition": comp, "molar_mass": mm, "unit": "g/mol"}
        prims.append({
            "id": f"molar_mass_{name}", "predicate": f"molar_mass_of_{name}",
            "context": "molar_masses", "value": mm, "unit": "g/mol",
            "source": (f"Computed as the sum over the formula {name} of (atom count "
                       f"x IUPAC standard atomic weight): "
                       + " + ".join(f"{n}x{aw[e]}({e})" for e, n in comp.items())
                       + f" = {mm} g/mol. Atomic weights: IUPAC/CIAAW standard "
                         f"atomic weights.")})

    kb1 = {
        "_provenance": src["_provenance"],
        "_design_note": (
            "Rules are UNIVERSAL laws, species- and reaction-agnostic, each a single "
            "arithmetic expression over role-named inputs. Reaction-specific facts are "
            "DATA: `reactions` holds verified coefficients, `species` holds "
            "compositions and molar masses, and `primitives` holds the molar masses "
            "and the two universal constants. Adding a reaction adds data only, never "
            "a rule."),
        "_reaction_access_note": (
            "engine._find_primitive matches on predicate name and ignores context, so "
            "a primitive cannot vary per reaction; bindings are the only per-call "
            "channel. Universal rule inputs (coeff_reactant, molar_mass_product, ...) "
            "are therefore satisfied from BINDINGS, which tools/eval/eval_chemistry.py fills by "
            "looking up the query's named reaction and species in the `reactions` and "
            "`species` data below. That lookup is the parser's role in the bowtie; the "
            "engine only backward-chains over universal rules. `avogadro` and "
            "`molar_volume_stp` have fixed predicate names and are read directly from "
            "primitives by the rules."),
        "_limitation_note": (
            "The limiting-reactant rules cover exactly two candidate reactants "
            "(roles a and b), because a rule's input list has fixed arity. A "
            "three-reactant limiting problem would need another rule pair."),
        "context_transitions": [],
        "_context_transitions_note": (
            "Every rule lives in the single '" + CONTEXT + "' context, so no subgoal "
            "crosses a context boundary and no transitions are declared."),
        "predicate_aliases": {},
        "_predicate_aliases_note": (
            "No synonyms: each role-named quantity has exactly one predicate."),
        "domain_scope": [
            "Gases are ideal.",
            "Substances are pure; molar masses use IUPAC/CIAAW standard atomic weights.",
            "Reactions proceed as written by the recorded balanced equations; computed "
            "amounts are theoretical (the limiting reactant is consumed completely).",
        ],
        "primitives": prims,
        "species": species,
        "reactions": {rx["id"]: {k: v for k, v in rx.items() if k != "id"}
                      for rx in reactions},
        "rules": universal_rules(),
    }
    json.dump(kb1, open(KB1, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    open(KB1, "a", encoding="utf-8").write("\n")

    # ---- kb2 schema, derived structurally -------------------------------
    rules = kb1["rules"]
    produced = {}
    for r in rules:
        produced.setdefault(r["output"]["predicate"],
                            {"unit": r["output"]["unit"], "contexts": [r["context"]]})
    consumed = {}
    for r in rules:
        for i in r["inputs"]:
            consumed.setdefault(i["predicate"], i["unit"])

    desc = {
        "moles_reactant": "Amount of the reactant, in moles.",
        "moles_product": "Amount of the product, in moles.",
        "mass_product": "Mass of product formed.",
        "particles_product": "Number of product particles.",
        "volume_gas_product": "Volume of gaseous product at STP.",
        "moles_reactant_a": "Amount of the first candidate limiting reactant.",
        "moles_reactant_b": "Amount of the second candidate limiting reactant.",
        "moles_product_limited": "Product amount set by whichever reactant limits.",
        "theoretical_mass_product": "Mass of product if the limiting reactant is consumed completely.",
        "percent_yield": "Actual yield as a percentage of theoretical yield.",
    }
    binding_desc = {
        "mass_reactant": "Mass of reactant supplied by the question.",
        "molar_mass_reactant": "Molar mass of the reactant (from KB species data).",
        "coeff_reactant": "Stoichiometric coefficient of the reactant in the named reaction.",
        "coeff_product": "Stoichiometric coefficient of the product in the named reaction.",
        "molar_mass_product": "Molar mass of the product (from KB species data).",
        "concentration": "Molarity of the solution.",
        "volume_solution": "Volume of solution.",
        "mass_reactant_a": "Mass of the first candidate limiting reactant.",
        "molar_mass_reactant_a": "Molar mass of the first candidate limiting reactant.",
        "mass_reactant_b": "Mass of the second candidate limiting reactant.",
        "molar_mass_reactant_b": "Molar mass of the second candidate limiting reactant.",
        "coeff_reactant_a": "Coefficient of the first candidate limiting reactant.",
        "coeff_reactant_b": "Coefficient of the second candidate limiting reactant.",
        "actual_mass_product": "Mass of product actually obtained.",
        "avogadro": "Avogadro constant (KB primitive).",
        "molar_volume_stp": "Molar volume of an ideal gas at STP (KB primitive).",
        "gas_conditions": "Stated gas conditions; 'stp' when the query says STP.",
        "other_reactants": ("Stated condition that every reactant other than the given "
                            "one is in excess; 'excess'."),
    }
    kb2 = {
        "predicates": [
            {"predicate": p, "unit": produced[p]["unit"],
             "contexts": produced[p]["contexts"],
             "description": desc.get(p, ""),
             "example_queries": [f"what is the {p.replace('_', ' ')}"]}
            for p in sorted(produced)],
        "bindings_schema": [
            {"predicate": p, "unit": consumed[p],
             "aliases": [binding_desc.get(p, "")]}
            for p in sorted(c for c in consumed if c not in produced)],
    }
    json.dump(kb2, open(KB2, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    open(KB2, "a", encoding="utf-8").write("\n")

    n_data = len(prims) + len(kb1["reactions"]) + len(kb1["species"])
    print(f"wrote {KB1}")
    print(f"  UNIVERSAL RULES : {len(rules)}")
    print(f"  DATA            : {len(prims)} primitives + {len(kb1['reactions'])} "
          f"reactions + {len(kb1['species'])} species = {n_data} entries")
    print(f"  rules mentioning a reaction or species name: "
          f"{sum(1 for r in rules for rx in kb1['reactions'] if rx in json.dumps(r))}")
    print(f"  context_transitions={kb1['context_transitions']} "
          f"predicate_aliases={kb1['predicate_aliases']}")
    print(f"wrote {KB2}: {len(kb2['predicates'])} derived predicates, "
          f"{len(kb2['bindings_schema'])} input predicates")


if __name__ == "__main__":
    main()
