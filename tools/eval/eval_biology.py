"""The biology domain on the UNMODIFIED PCT engine -- one KB, one rule set.

Supersedes the former split vertebrate/felid runners. The engine backward-chains over 11
UNIVERSAL rules in pct/kb1_biology.json; all 52 taxa are data. This module
resolves a candidate taxon and its ancestor chain out of that data into the
level-indexed bindings the rules ask for. That resolution is the parser's role in
the bowtie; the engine never sees a taxon name in rule logic.

Two discriminator kinds, preserved exactly as the two source KBs behaved:
  traits    required traits are rule INPUTS -> an absent trait is F5
  envelope  characters are inputs, range test in the GUARD -> a non-match is F6

Run:
    python tools/eval/eval_biology.py
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
from pct.renderer import render  # noqa: E402

KB = load_kb(DOMAINS["biology"]["kb1"])
TAXA = KB["taxa"]
CTX = "biology_taxonomy"
MAX_TRAITS = 2
D_MAX = 12          # the merged tree is 10 levels deep

SPECIES = [t for t, v in TAXA.items() if v["rank"] == "species"]
VERT_TAXA = [t for t, v in TAXA.items() if v["discriminator"] == "traits"]


def ancestry(taxon: str) -> list[str]:
    chain = [taxon]
    while TAXA[chain[-1]]["parent"] is not None:
        chain.append(TAXA[chain[-1]]["parent"])
    return list(reversed(chain))


def resolve(taxon: str, traits: dict | None = None, chars: dict | None = None,
            stop_at_entry: bool = True) -> dict:
    """Candidate + ancestors -> level-indexed bindings.

    `stop_at_entry` truncates the chain at `felidae` when no vertebrate traits are
    supplied, which is how a felid-only query enters the sub-tree.
    """
    traits = traits or {}
    chars = chars or {}
    bnd: dict = {}
    if chars:
        bnd["is_felid"] = {"value": True, "unit": "trait"}
        for k, u in (("body_length", "cm"), ("geographic_range", "region"),
                     ("habitat", "habitat")):
            if k in chars:
                bnd[k] = {"value": chars[k], "unit": u}
    if traits:
        bnd["is_felid"] = bnd.get("is_felid", {"value": True, "unit": "trait"})

    chain = ancestry(taxon)
    if stop_at_entry and not traits and "felidae" in chain:
        chain = chain[chain.index("felidae"):]

    for label in chain:
        t = TAXA[label]
        n = t["level"]
        bnd[f"candidate_L{n}"] = {"value": label, "unit": "label"}
        disc = t["discriminator"]
        if disc == "traits":
            req = t["required_traits"]
            for k in range(1, MAX_TRAITS + 1):
                key = f"trait_{k}_L{n}"
                if k <= len(req):
                    name = req[k - 1]
                    if name in traits:          # absent -> LEAVE UNBOUND -> F5
                        bnd[key] = {"value": traits[name], "unit": "trait"}
                else:
                    bnd[key] = {"value": True, "unit": "trait"}   # neutral filler
        elif disc == "envelope":
            bnd[f"body_min_L{n}"] = {"value": t["body_min"], "unit": "cm"}
            bnd[f"body_max_L{n}"] = {"value": t["body_max"], "unit": "cm"}
            bnd[f"regions_L{n}"] = {"value": t["regions"], "unit": "regionset"}
            bnd[f"habitats_L{n}"] = {"value": t["habitats"], "unit": "habitatset"}
    return bnd


def ask(taxon, traits=None, chars=None, stop_at_entry=True):
    goal = {"predicate": f"taxon_{TAXA[taxon]['level']}", "unit": "taxon",
            "context": CTX}
    return prove(goal, resolve(taxon, traits, chars, stop_at_entry), KB, d_max=D_MAX)


def rules_of(r):
    return [s["id"] for s in r.get("derivation", []) if s["type"] == "rule"]


def taxa_of(r):
    return [s["output"] for s in r.get("derivation", []) if s["type"] == "rule"]


def finest_common(taxa):
    if len(taxa) == 1:
        return taxa[0], "species"
    for rank in ("genus", "lineage", "subfamily", "family"):
        anc = {next((a for a in ancestry(t) if TAXA[a]["rank"] == rank), None)
               for t in taxa}
        if len(anc) == 1 and None not in anc:
            return anc.pop(), rank
    return "felidae", "family"


# ===========================================================================
# CLASSIFICATION AND MEMBERSHIP -- the only two ways this domain answers
# ===========================================================================
VERT_ORDER = sorted(VERT_TAXA, key=lambda t: -TAXA[t]["level"])


def classify(traits=None, chars=None):
    """The deepest taxon derivable from the stated evidence, with its proof.

    trait evidence   the deepest trait taxon whose chain proves
    felid evidence   the finest taxon shared by every species whose chain proves;
                     species are tried first because upper-taxon envelopes are
                     boxes of independent ranges and can admit a specimen no
                     member species supports
    Returns (taxon, result). taxon is None unless `result` is a successful proof
    of exactly that taxon -- a classification is never reported without one.
    """
    if chars:
        cons, last = [], {}
        for sp in SPECIES:
            r = ask(sp, chars=chars)
            if r.get("status") == "success":
                cons.append(r["value"])
            else:
                last = r
        if not cons:
            return None, last
        taxon, _rank = finest_common(cons)
        r = ask(taxon, chars=chars)
        if r.get("status") == "success" and r.get("value") == taxon:
            return taxon, r
        return None, r
    last = {}
    for cand in VERT_ORDER:
        r = ask(cand, traits=traits)
        if r.get("status") == "success":
            return r["value"], r
        last = r
    return None, last


def typed_failure(res: dict) -> dict:
    """The refusal a user is shown: failure type, reason, and the failing goal."""
    return {"type": res.get("type"), "reason": res.get("reason"),
            "goal": res.get("goal"), "rule": res.get("rule")}


def answer_is_a(target, traits=None, chars=None):
    """Answer "is the specimen a <target>?" without ever asserting "no".

    Membership derived           -> "yes", with the derivation of <target>.
    Membership NOT derived       -> the typed failure for <target>; then the
                                    specimen is classified, and if that succeeds
                                    the answer is "derived: <taxon>" with ITS
                                    derivation. Non-membership in <target> is
                                    never stated: the KB declares no disjointness
                                    between sibling taxa, so it is not derivable.
    Returns {"answer", "result", "failure"}: `result` is the proof behind the
    answer (or the failure when nothing was derived).
    """
    res = ask(target, traits=traits, chars=chars)
    if res.get("status") == "success" and res.get("value") == target:
        return {"answer": "yes", "result": res, "failure": None}
    failure = typed_failure(res)
    taxon, cres = classify(traits=traits, chars=chars)
    if taxon is not None:
        return {"answer": f"derived: {taxon}", "result": cres, "failure": failure}
    return {"answer": "refuse", "result": res, "failure": failure}


# ===========================================================================
# PART 1 -- vertebrate classification (trait evidence)
# ===========================================================================
def run_vertebrate():
    spec = json.load(open(DOMAINS["biology"]["queries"]["structured"], encoding="utf-8"))
    organisms, queries = spec["organisms"], spec["vertebrate_queries"]
    correct = answered = refused = 0
    ftypes, depths = Counter(), Counter()
    rows = []
    for q in queries:
        traits = {t: True for t in organisms[q["organism"]]}
        if q["form"] == "is_a":
            out = answer_is_a(q["target"], traits=traits)
            got, res = out["answer"], out["result"]
        elif q["form"] == "why_is":
            res = ask(q["target"], traits=traits)
            hit = res.get("status") == "success" and res["value"] == q["target"]
            got = res["value"] if hit else "refuse"
        else:
            best, res = classify(traits=traits)
            got = best if best else "refuse"
        ok = got == q["expected"]
        correct += ok
        if res.get("status") == "success":
            answered += 1
            depths[len(rules_of(res))] += 1
        else:
            refused += 1
            ftypes[res.get("type")] += 1
        rows.append((q, got, ok, res))
        print(f"[{'ok ' if ok else 'FAIL'}] V{q['id']:<3} {q['form']:<9} "
              f"{q['question'][:52]:<54} -> {str(got):<22} (exp {q['expected']})")
    return correct, len(queries), answered, refused, ftypes, depths, rows


# ===========================================================================
# PART 2 -- felid classification (envelope evidence)
# ===========================================================================
def run_felid():
    spec = json.load(open(DOMAINS["biology"]["queries"]["structured"], encoding="utf-8"))
    queries = spec["felid_queries"]
    correct = answered = refused = 0
    ftypes, depths, levels = Counter(), Counter(), Counter()
    rows = []
    for q in queries:
        chars = {k: q[k] for k in ("body_length", "geographic_range", "habitat")}
        if q["form"] == "is_a":
            out = answer_is_a(q["target"], chars=chars)
            got, res = out["answer"], out["result"]
            rank, cons = "target", []
        elif q["form"] == "why_is":
            res = ask(q["target"], chars=chars)
            hit = res.get("status") == "success" and res["value"] == q["target"]
            got = res["value"] if hit else "refuse"
            rank, cons = "target", []
        else:
            cons, res = [], {}
            for sp in SPECIES:
                r = ask(sp, chars=chars)
                if r.get("status") == "success":
                    cons.append(r["value"])
                else:
                    res = r
            if cons:
                taxon, rank = finest_common(cons)
                res = ask(taxon, chars=chars)
                got = taxon
            else:
                got, rank = "refuse", "refuse"
        ok = got == q["expected"]
        correct += ok
        if res.get("status") == "success":
            answered += 1
            depths[len(rules_of(res))] += 1
        else:
            refused += 1
            ftypes[res.get("type")] += 1
        if q["form"] in ("what_is", "why_isnt"):
            levels[rank] += 1
        rows.append((q, got, ok, rank, cons, res))
        print(f"[{'ok ' if ok else 'FAIL'}] F{q['id']:<3} {q['form']:<9} "
              f"{q['question'][:52]:<54} -> {str(got):<22} (exp {q['expected']})")
    return correct, len(queries), answered, refused, ftypes, depths, levels, rows


def main() -> int:
    print("=" * 98)
    print("PCT BIOLOGY DOMAIN  --  one KB, 11 universal rules, 52 taxa as data")
    print("=" * 98)
    print(f"  UNIVERSAL RULES : {len(KB['rules'])}")
    print(f"  DATA            : {len(TAXA)} taxa  "
          f"{dict(sorted(Counter(t['level'] for t in TAXA.values()).items()))}")
    logic_leak = sum(1 for r in KB["rules"]
                     if any(t in json.dumps({k: v for k, v in r.items() if k != 'proof'})
                            for t in TAXA))
    print(f"  rules whose LOGIC names a taxon: {logic_leak}")
    print(f"  primitives={len(KB['primitives'])} "
          f"context_transitions={KB['context_transitions']} "
          f"predicate_aliases={KB['predicate_aliases']}\n")

    print("-" * 98)
    print("PART 1 -- vertebrate classification (trait evidence, F5 on absent trait)")
    print("-" * 98)
    vc, vn, va, vr, vft, vd, vrows = run_vertebrate()
    print()
    print("-" * 98)
    print("PART 2 -- felid classification (envelope evidence, F6 on non-match)")
    print("-" * 98)
    fc, fn, fa, fr, fft, fd, flv, frows = run_felid()

    print()
    print("=" * 98)
    print("BIOLOGY DOMAIN TOTALS")
    print("=" * 98)
    print(f"  vertebrate part : {vc}/{vn}  answered {va} refused {vr}  "
          f"types {dict(vft)}  depths {dict(sorted(vd.items()))}")
    print(f"  felid part      : {fc}/{fn}  answered {fa} refused {fr}  "
          f"types {dict(fft)}  depths {dict(sorted(fd.items()))}")
    print(f"  COMBINED        : {vc+fc}/{vn+fn} "
          f"({100*(vc+fc)/(vn+fn):.1f}%)  answered {va+fa} refused {vr+fr}")
    print(f"  refusal types   : trait-absent {dict(vft)} | envelope-nonmatch {dict(fft)}")
    print(f"  felid resolution levels: "
          f"{ {k: v for k, v in flv.items()} }")

    # ---- the join crossing ----
    print()
    print("=" * 98)
    print("THE JOIN -- one derivation crossing trait-based upper tree -> envelope felid")
    print("=" * 98)
    lion_traits = {"has_backbone": True, "amniotic_egg": True, "produces_milk": True,
                   "has_hair": True, "live_birth": True, "has_placenta": True,
                   "has_carnassial_teeth": True}
    chars = {"body_length": 193, "geographic_range": "sub_saharan_africa",
             "habitat": "forest"}
    res = ask("lion", traits=lion_traits, chars=chars, stop_at_entry=False)
    print(f"  evidence: mammalian traits {sorted(lion_traits)}")
    print(f"            + felid characters {chars}")
    print(f"  goal    : taxon_10 (species), candidate chain resolved from KB data")
    if res.get("status") == "success":
        print(f"  RESULT  : {res['value']!r}  via {len(rules_of(res))} rules")
        for s in res["derivation"]:
            if s["type"] == "rule":
                kind = ("traits" if "by_traits" in s["id"] or s["id"].endswith("root")
                        else "JOIN" if s["id"].startswith("taxon_L6_") else "envelope")
                print(f"      [{kind:<8}] {s['id']:<32} -> {s['output']}")
    else:
        print(f"  REFUSED : {res.get('type')} {res.get('reason')}")

    total_ok = (vc == vn) and (fc == fn) and res.get("status") == "success"
    print()
    print("-" * 98)
    print(f"{'PASS' if total_ok else 'FAIL'}: {vc+fc}/{vn+fn} queries, join "
          f"{'crosses' if res.get('status') == 'success' else 'FAILS'}")
    return 0 if total_ok else 1


if __name__ == "__main__":
    sys.exit(main())
