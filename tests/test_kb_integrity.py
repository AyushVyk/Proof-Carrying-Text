"""Structural integrity checks over every domain's kb1, independent of any query.

  repeated-predicate inputs   no rule may have two inputs with the same predicate.
                              The engine resolves a sub-goal by predicate, so two
                              such inputs (m1, m2: mass) would both read ONE binding
                              and the rule would silently compute with m1 = m2.
                              (2026-11-21 rule audit, Part B.)
  element conservation        chemistry: every reaction in the KB conserves every
                              element, using the build's own check
                              (tools/build/build_kb_chemistry.verify_balanced) on the
                              KB's coefficients and species compositions.
  biology envelopes           every higher taxon with an envelope (body_min/body_max,
                              regions, habitats) equals the union of its member
                              species' envelopes: body range = [min of mins, max of
                              maxes]; regions and habitats = union of sets.
  felid source values         every felid species' body range, regions and habitats
                              equal the source dataset
                              (domains/biology/source_felidae.json).

Run:
    python tests/test_kb_integrity.py
Exits nonzero on any violation.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for p in (ROOT, os.path.join(ROOT, "tools", "build")):
    if p not in sys.path:
        sys.path.insert(0, p)

from layout import DOMAINS  # noqa: E402

from pct.engine import load_kb  # noqa: E402


def repeated_predicate_inputs(kb: dict) -> tuple[int, list[str]]:
    bad = []
    for r in kb.get("rules", []):
        c = Counter(i["predicate"] for i in r["inputs"])
        dup = sorted(p for p, n in c.items() if n > 1)
        if dup:
            bad.append(f"{r['id']}: inputs repeat predicate(s) {dup}")
    return len(kb.get("rules", [])), bad


def element_conservation(kb: dict) -> tuple[int, list[str]]:
    from build_kb_chemistry import verify_balanced
    comps = {s: v["composition"] for s, v in kb["species"].items()}
    bad = []
    for rid, rx in kb["reactions"].items():
        bad += [f"{rid} ({rx.get('equation')}): {c}" for c in verify_balanced(rx, comps)]
    return len(kb["reactions"]), bad


ENVELOPE = ("body_min", "body_max", "regions", "habitats")


def _species_under(taxa: dict, name: str) -> list[str]:
    out = []
    for s, t in taxa.items():
        if t.get("rank") != "species":
            continue
        p = t.get("parent")
        while p is not None:
            if p == name:
                out.append(s)
                break
            p = taxa[p].get("parent")
    return out


def biology_envelopes(kb: dict) -> tuple[int, list[str]]:
    taxa = kb["taxa"]
    n, bad = 0, []
    for name, t in taxa.items():
        if t.get("rank") == "species" or not any(k in t for k in ENVELOPE):
            continue
        members = _species_under(taxa, name)
        if not members:
            bad.append(f"{name}: has an envelope but no member species")
            continue
        sp = [taxa[s] for s in members]
        expect = {"body_min": min(s["body_min"] for s in sp),
                  "body_max": max(s["body_max"] for s in sp),
                  "regions": set().union(*(s["regions"] for s in sp)),
                  "habitats": set().union(*(s["habitats"] for s in sp))}
        got = {"body_min": t.get("body_min"), "body_max": t.get("body_max"),
               "regions": set(t.get("regions") or []),
               "habitats": set(t.get("habitats") or [])}
        for field in ("body range", "regions", "habitats"):
            n += 1
            keys = ("body_min", "body_max") if field == "body range" else (field,)
            if any(got[k] != expect[k] for k in keys):
                bad.append(f"{name} {field}: KB {[got[k] for k in keys]} != union of "
                           f"{len(members)} species {[expect[k] for k in keys]}")
    return n, bad


def felid_source_values(kb: dict) -> tuple[int, list[str]]:
    taxa = kb["taxa"]
    src = json.load(open(DOMAINS["biology"]["sources"]["felidae"], encoding="utf-8"))
    rows = {r["taxon"]: r for r in src["species"]}
    felids = [s for s in _species_under(taxa, "felidae")]
    n, bad = 0, []
    for s in sorted(set(felids) | set(rows)):
        if s not in rows or s not in taxa:
            n += 1
            bad.append(f"{s}: in {'the KB' if s in taxa else 'the source'} only")
            continue
        t, r = taxa[s], rows[s]
        pairs = {"body range": ([t["body_min"], t["body_max"]], list(r["body_cm"])),
                 "regions": (set(t["regions"]), set(r["region_tokens"])),
                 "habitats": (set(t["habitats"]), set(r["habitat_tokens"]))}
        for field, (kbv, srcv) in pairs.items():
            n += 1
            if kbv != srcv:
                bad.append(f"{s} {field}: KB {kbv} != source {srcv}")
    return n, bad


CHECKS = {
    "physics": [("no rule has two inputs with the same predicate", repeated_predicate_inputs)],
    "chemistry": [("no rule has two inputs with the same predicate", repeated_predicate_inputs),
                  ("every reaction conserves every element", element_conservation)],
    "biology": [("no rule has two inputs with the same predicate", repeated_predicate_inputs),
                ("higher-taxon envelope = union of its species' envelopes", biology_envelopes),
                ("felid body range, regions, habitats = source dataset", felid_source_values)],
}


def main() -> int:
    total = passed = 0
    for name, d in DOMAINS.items():
        kb = load_kb(d["kb1"])
        for label, fn in CHECKS.get(name, []):
            n, bad = fn(kb)
            total += n
            passed += n - len(bad)
            print(f"[{'PASS' if not bad else 'FAIL'}] {name:<10} {label}: "
                  f"{n - len(bad)}/{n}")
            for b in bad:
                print(f"         {b}")
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
