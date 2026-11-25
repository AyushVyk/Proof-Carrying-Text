"""Split Def. 6 into a hallucination rate and a faithfulness error rate.

Paper definitions:
  hallucination       an emitted claim NOT derivable from the KB under the declared
                      bounds, given the bindings of the goal state it came from.
  faithfulness error  an emitted claim that IS derivable from its goal state, where
                      that goal state does not correspond to the query asked.

For PCT the first is checked by REPLAY, not assumed: every stored answer (deployed
and oracle, every domain) has its goal state and bindings rebuilt from the stored
parse (deployed) or the gold goal state (oracle), prove() is re-run on them, and
the returned derivation is checked step by step against the KB itself:

  a. status == success
  b. value (and unit, and the rule chain) equal the stored answer
  c. the derivation is complete: every step is
       - a binding of the goal state (provenance recorded: from the query/parse, or
         KB entity data the resolver put there -- the latter re-verified against
         the KB's own records),
       - a KB primitive (id, predicate, value, unit re-verified), or
       - a KB rule whose formula and guard match the KB, whose every input is the
         value of an EARLIER step producing that input's predicate, whose guard
         re-evaluates true, and whose formula re-evaluates to the stated output;
     and the final step produces the goal predicate with the answered value.

An answer failing any of a-c is a hallucination. An answer passing the replay that
the locked scorer (score.py, unmodified, imported read-only) judges wrong -- or any
answered out-of-scope query -- is a faithfulness error. RAG and raw keep the locked
rubric's Def. 6 rate as their hallucination rate; faithfulness is not applicable.

No model is called. Nothing under pct/, domains/, or results/<domain>/ is written.

Run:
    python tools/analysis/split_metrics.py            # replay report, all domains
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for p in (ROOT, _HERE, os.path.join(ROOT, "tools", "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from layout import DOMAINS, results_path  # noqa: E402

from pct.engine import SAFE_GLOBALS, DEFAULT_DEPTH, load_kb, predicate_aliases, prove  # noqa: E402

import score  # noqa: E402  (locked; read-only use)

DOMAIN_ORDER = ["physics", "chemistry", "biology"]
CONFIGS = ("deployed", "oracle")


# ===========================================================================
# goal-state reconstruction, per domain -- the SAME resolvers the runs used
# ===========================================================================
class Physics:
    name = "physics"

    def __init__(self):
        import eval_oracle_physics as EO
        self.EO = EO
        self.kb = load_kb(DOMAINS["physics"]["kb1"])
        self.rules_by_id = EO._kb_rules_by_id(self.kb)
        self.stored = json.load(open(DOMAINS["physics"]["queries"]["structured"],
                                     encoding="utf-8"))
        self.parses = {r["id"]: r["parsed"] for r in json.load(open(
            results_path("physics", "deployed_parses.json"), encoding="utf-8"))["rows"]}

    def goal_state(self, row: dict, config: str):
        """-> (goal, bindings, d_max, provenance{binding_key: source}) or None."""
        r = self.stored[row["id"] - 1]
        if config == "deployed":
            p = self.parses[row["id"]]
            goal = {"predicate": p["predicate"], "unit": p["unit"], "context": p["context"]}
            bnd = p["bindings"]
            return goal, bnd, DEFAULT_DEPTH, {k: "query" for k in bnd}
        bnd, _src = self.EO.build_gold_bindings(r, self.rules_by_id)
        if bnd is None:
            return None
        goal = {"predicate": self.EO.gold_goal_predicate(r), "unit": r["expected_unit"],
                "context": r["expected_context"]}
        return goal, bnd, self.EO.D_MAX, {k: "query" for k in bnd}

    def gold(self, row):
        r = self.stored[row["id"] - 1]
        if r["category"] == "oos":
            return None
        bnd, _ = self.EO.build_gold_bindings(r, self.rules_by_id)
        return {"predicate": self.EO.gold_goal_predicate(r), "context": r["expected_context"],
                "bindings": {k: v["value"] for k, v in (bnd or {}).items()},
                "mapped": bnd is not None}

    def parse(self, row):
        p = self.parses[row["id"]]
        return {"predicate": p["predicate"], "context": p["context"],
                "bindings": {k: v["value"] for k, v in p["bindings"].items()}}

    def verify_entity(self, key, value):
        return None  # physics has no resolver-supplied entity data


class Chemistry:
    name = "chemistry"
    ROLE_KEYS = ("reactant", "product", "reactant_a", "reactant_b")

    def __init__(self):
        import eval_chemistry as RS
        self.RS = RS
        self.kb = RS.KB
        self.parses = {r["id"]: r["parsed"] for r in json.load(open(
            results_path("chemistry", "deployed_parses.json"), encoding="utf-8"))["rows"]}
        self.queries = {q["id"]: q for q in json.load(open(
            DOMAINS["chemistry"]["queries"]["natural_language"], encoding="utf-8"))["queries"]}

    def _state(self, reaction, roles, given, goal_pred):
        bnd = self.RS.resolve({"reaction": reaction, "roles": roles, "given": given})
        goal = {"predicate": goal_pred, "unit": self.RS.UNIT[goal_pred],
                "context": self.RS.CONTEXT}
        prov = {k: ("query" if k in given else "entity") for k in bnd}
        self._roles = roles
        self._reaction = reaction
        return goal, bnd, 8, prov

    def goal_state(self, row, config):
        if config == "deployed":
            b = self.parses[row["id"]]["bindings"]
            roles = {k: b[k]["value"] for k in self.ROLE_KEYS if k in b}
            given = {k: v["value"] for k, v in b.items()
                     if k not in self.ROLE_KEYS + ("reaction",)}
            return self._state((b.get("reaction") or {}).get("value"), roles, given,
                               self.parses[row["id"]]["predicate"])
        g = self.RS.gold_goal(self.queries[row["id"]])     # no reaction for 1-substance forms
        if not g:
            return None
        return self._state(g["reaction"], g["roles"], g["given"], g["goal"])

    def verify_entity(self, key, value):
        """molar_mass_<role> / coeff_<role> must be the KB's species/reaction data;
        other_reactants = 'excess' is supplied only for a single-reactant reaction."""
        if key == "other_reactants":
            if self._reaction is None:
                return "other_reactants supplied with no reaction"
            rx = self.kb["reactions"][self._reaction]
            ok = value == "excess" and len(rx["reactants"]) == 1
            return None if ok else (f"other_reactants={value!r} supplied for a reaction "
                                    f"with {len(rx['reactants'])} reactants")
        m = re.match(r"(molar_mass|coeff)_(.+)$", key)
        if not m or m.group(2) not in self._roles:
            return f"unrecognised entity binding {key}"
        sp = self._roles[m.group(2)]
        if m.group(1) == "molar_mass":
            ok = self.kb["species"][sp]["molar_mass"] == value
        else:
            rx = self.kb["reactions"][self._reaction]
            ok = (rx["reactants"].get(sp) or rx["products"].get(sp)) == value
        return None if ok else f"{key}={value!r} differs from KB data for {sp}"

    def gold(self, row):
        g = self.RS.gold_goal(self.queries[row["id"]])
        if not g:
            return None
        return {"predicate": g["goal"], "context": self.RS.CONTEXT,
                "reaction": g["reaction"], "roles": g["roles"], "bindings": g["given"]}

    def parse(self, row):
        p = self.parses[row["id"]]
        b = {k: v["value"] for k, v in p["bindings"].items()}
        return {"predicate": p["predicate"], "context": p.get("context"),
                "reaction": b.get("reaction"),
                "roles": {k: b[k] for k in self.ROLE_KEYS if k in b},
                "bindings": {k: v for k, v in b.items()
                             if k not in self.ROLE_KEYS + ("reaction",)}}


class Biology:
    name = "biology"
    CHAR_KEYS = ("body_length", "geographic_range", "habitat")

    def __init__(self):
        import eval_biology as RB
        self.RB = RB
        self.kb = RB.KB
        self.parses = {r["id"]: r["parsed"] for r in json.load(open(
            results_path("biology", "deployed_parses.json"), encoding="utf-8"))["rows"]}
        self.queries = {q["id"]: q for q in json.load(open(
            DOMAINS["biology"]["queries"]["natural_language"], encoding="utf-8"))["queries"]}

    def _state(self, taxon, traits, chars):
        bnd = self.RB.resolve(taxon, traits or None, chars or None)
        goal = {"predicate": f"taxon_{self.RB.TAXA[taxon]['level']}", "unit": "taxon",
                "context": self.RB.CTX}
        self._traits, self._chars, self._taxon = traits, chars, taxon
        prov = {}
        for k, v in bnd.items():
            if k in self.CHAR_KEYS:
                prov[k] = "query"
            elif k == "is_felid":
                prov[k] = "resolver flag (felid evidence stated)" if chars else \
                    "resolver flag (trait evidence stated)"
            elif re.match(r"trait_\d+_L\d+$", k):
                prov[k] = "query" if self._trait_from_query(k) else "entity"
            else:
                prov[k] = "entity"
        return goal, bnd, self.RB.D_MAX, prov

    def _trait_from_query(self, key):
        k, n = map(int, re.match(r"trait_(\d+)_L(\d+)$", key).groups())
        t = self._level_taxon(n)
        req = self.RB.TAXA[t]["required_traits"] if t else []
        return k <= len(req) and req[k - 1] in self._traits

    def _level_taxon(self, n):
        return next((a for a in self.RB.ancestry(self._taxon)
                     if self.RB.TAXA[a]["level"] == n), None)

    def _evidence(self, row, config):
        if config == "deployed":
            b = self.parses[row["id"]].get("bindings", {})
            traits = {k: True for k, v in b.items()
                      if k not in self.CHAR_KEYS + ("candidate_taxon", "is_felid")
                      and v.get("value") is True}
            chars = {k: b[k]["value"] for k in self.CHAR_KEYS if k in b}
            return traits, chars
        q = self.queries[row["id"]]
        if q["part"] in ("vertebrate", "felid", "join"):   # join: traits AND characters
            return {t: True for t in q.get("gold_traits") or []}, q.get("gold_chars") or {}
        return None

    def goal_state(self, row, config):
        ev = self._evidence(row, config)
        if ev is None:
            return None
        claim = row[f"{config}_claim"]
        return self._state(claim["taxon"], *ev)

    def verify_entity(self, key, value):
        m = re.match(r"(candidate|body_min|body_max|regions|habitats)_L(\d+)$", key)
        if m:
            t = self._level_taxon(int(m.group(2)))
            if t is None:
                return f"{key}: no ancestor at level {m.group(2)}"
            want = {"candidate": t, "body_min": self.RB.TAXA[t].get("body_min"),
                    "body_max": self.RB.TAXA[t].get("body_max"),
                    "regions": self.RB.TAXA[t].get("regions"),
                    "habitats": self.RB.TAXA[t].get("habitats")}[m.group(1)]
            return None if want == value else f"{key}={value!r} differs from KB ({want!r})"
        m = re.match(r"trait_(\d+)_L(\d+)$", key)
        if m:   # neutral filler: only legal past the taxon's required-trait list
            t = self._level_taxon(int(m.group(2)))
            req = self.RB.TAXA[t]["required_traits"] if t else None
            if req is None or int(m.group(1)) <= len(req) or value is not True:
                return f"{key}={value!r} is not a KB-licensed filler"
            return None
        return f"unrecognised entity binding {key}"

    def gold(self, row):
        q = self.queries[row["id"]]
        if q.get("scope") == "out":
            return None
        return {"form": q.get("gold_form"), "target": q.get("gold_target"),
                "traits": q.get("gold_traits"), "chars": q.get("gold_chars")}

    def parse(self, row):
        p = self.parses[row["id"]]
        return {"predicate": p.get("predicate"), "context": p.get("context"),
                "bindings": {k: v["value"] for k, v in p.get("bindings", {}).items()}}


ADAPTERS = {"physics": Physics, "chemistry": Chemistry, "biology": Biology}


# ===========================================================================
# the derivation checker -- independent of the engine's search
# ===========================================================================
def check_derivation(res: dict, goal: dict, bindings: dict, prov: dict, kb: dict,
                     adapter) -> tuple[list[str], Counter, list[str]]:
    """-> (problems, step-kind counts, notes). Empty `problems` = complete."""
    problems, notes = [], []
    kinds: Counter = Counter()
    aliases = predicate_aliases(kb)
    rules = {r["id"]: r for r in kb.get("rules", [])}
    prims = {p["id"]: p for p in kb.get("primitives", [])}
    produced: list[tuple[str, object]] = []        # (predicate, value) per earlier step

    for s in res["derivation"]:
        t = s.get("type")
        if t == "binding":
            pred = s["predicate"]
            key = pred if pred in bindings else next(
                (a for a in aliases.get(pred, ()) if a in bindings), None)
            if key is None:
                problems.append(f"step {s['step']}: binding {pred} is not in the goal state")
                continue
            if bindings[key]["value"] != s["value"]:
                problems.append(f"step {s['step']}: binding {pred}={s['value']!r} but goal "
                                f"state has {bindings[key]['value']!r}")
            src = prov.get(key, "query")
            if src == "entity":
                bad = adapter.verify_entity(key, bindings[key]["value"])
                if bad:
                    problems.append(f"step {s['step']}: {bad}")
            kinds[f"binding:{src}"] += 1
            produced.append((pred, s["value"]))
        elif t == "primitive":
            p = prims.get(s.get("id"))
            if p is None or (p["predicate"], p["value"], p["unit"]) != \
                    (s["predicate"], s["value"], s["unit"]):
                problems.append(f"step {s['step']}: primitive {s.get('id')} does not match KB")
            kinds["primitive"] += 1
            produced.append((s["predicate"], s["value"]))
        elif t == "rule":
            r = rules.get(s.get("id"))
            if r is None:
                problems.append(f"step {s['step']}: rule {s.get('id')} not in KB")
                continue
            if r["formula"] != s.get("formula"):
                problems.append(f"step {s['step']}: rule {r['id']} formula differs from KB")
            vals = {}
            for inp in r["inputs"]:
                got = (s.get("inputs") or {}).get(inp["name"])
                if got is None:
                    problems.append(f"step {s['step']}: rule {r['id']} input "
                                    f"{inp['name']} missing")
                    continue
                vals[inp["name"]] = got["value"]
                if not any(p == inp["predicate"] and v == got["value"] for p, v in produced):
                    problems.append(f"step {s['step']}: rule {r['id']} input {inp['name']} "
                                    f"({inp['predicate']}={got['value']!r}) is not an earlier step")
            if len(vals) == len(r["inputs"]):
                try:
                    if "guard" in r and not eval(r["guard"], SAFE_GLOBALS, dict(vals)):
                        problems.append(f"step {s['step']}: rule {r['id']} guard is false")
                    out = eval(r["formula"], SAFE_GLOBALS, dict(vals))
                    if out != s.get("output"):
                        problems.append(f"step {s['step']}: rule {r['id']} recomputes to "
                                        f"{out!r}, step says {s.get('output')!r}")
                except Exception as e:  # noqa: BLE001
                    problems.append(f"step {s['step']}: rule {r['id']} re-evaluation raised {e}")
            kinds["rule"] += 1
            produced.append((r["output"]["predicate"], s.get("output")))
        else:
            problems.append(f"step {s.get('step')}: unknown step type {t!r}")

    last = res["derivation"][-1] if res["derivation"] else None
    if last is None:
        problems.append("empty derivation")
    else:
        lp = produced[-1][0] if produced else None
        if lp != goal["predicate"] and goal["predicate"] not in aliases.get(lp, ()) \
                and lp not in aliases.get(goal["predicate"], ()):
            problems.append(f"final step produces {lp}, goal is {goal['predicate']}")
        if produced and produced[-1][1] != res["value"]:
            problems.append("final step value differs from the result value")
        if last.get("type") != "rule":
            notes.append("goal satisfied directly by a binding (no rule step)")
    return problems, kinds, notes


def _same_value(a, b):
    if isinstance(a, float) or isinstance(b, float):
        try:
            return a == b or (math.isfinite(a) and abs(a - b) <= 1e-12 * max(1, abs(b)))
        except TypeError:
            return False
    return a == b


# ===========================================================================
# replay
# ===========================================================================
def replay_domain(domain: str) -> list[dict]:
    ad = ADAPTERS[domain]()
    rows = json.load(open(results_path(domain, "pipeline.json"), encoding="utf-8"))["rows"]
    out = []
    for row in rows:
        for cfg in CONFIGS:
            answer = row[cfg]
            if answer == "refuse":
                continue
            rec = {"domain": domain, "config": cfg, "id": row["id"], "scope": row["scope"],
                   "nl": row["nl"], "stored_answer": answer, "problems": [], "notes": []}
            claim = row.get(f"{cfg}_claim") or {}
            st = ad.goal_state(row, cfg)
            if st is None:
                rec["problems"].append("no goal state could be rebuilt for a stored answer")
                out.append(rec)
                continue
            goal, bnd, d_max, prov = st
            rec["goal"] = goal
            rec["bindings"] = {k: v["value"] for k, v in bnd.items()}
            res = prove(goal, bnd, ad.kb, d_max=d_max)
            rec["status"] = res.get("status")
            # (a)
            if res.get("status") != "success":
                rec["problems"].append(f"(a) replay status {res.get('status')} "
                                       f"{res.get('type')}: {res.get('reason')}")
                out.append(rec)
                continue
            rec["value"] = res["value"]
            # (b)
            want = claim.get("value") if claim.get("kind") == "value" else claim.get("taxon")
            if not _same_value(res["value"], want):
                rec["problems"].append(f"(b) replay value {res['value']!r} != stored {want!r}")
            if claim.get("kind") == "value":
                if not _same_value(res["value"], answer):
                    rec["problems"].append(f"(b) replay value {res['value']!r} != stored answer "
                                           f"{answer!r}")
                if res.get("unit") != claim.get("unit"):
                    rec["problems"].append(f"(b) unit {res.get('unit')!r} != stored "
                                           f"{claim.get('unit')!r}")
            elif answer == "yes":
                if claim.get("kind") != "member":
                    rec["problems"].append("(b) 'yes' without a membership claim")
            elif isinstance(answer, str) and answer.startswith("derived: "):
                if answer[len("derived: "):] != res["value"]:
                    rec["problems"].append(f"(b) stored {answer!r} != replay {res['value']!r}")
            elif answer != res["value"]:
                rec["problems"].append(f"(b) stored {answer!r} != replay {res['value']!r}")
            fired = [s["id"] for s in res["derivation"] if s["type"] == "rule"]
            if claim.get("rules") is not None and fired != claim["rules"]:
                rec["problems"].append(f"(b) rule chain {fired} != stored {claim['rules']}")
            if claim.get("derivation_steps") is not None and \
                    len(res["derivation"]) != claim["derivation_steps"]:
                rec["problems"].append(f"(b) {len(res['derivation'])} steps != stored "
                                       f"{claim['derivation_steps']}")
            # (c)
            probs, kinds, notes = check_derivation(res, goal, bnd, prov, ad.kb, ad)
            rec["problems"] += [f"(c) {p}" for p in probs]
            rec["notes"] += notes
            rec["step_kinds"] = dict(kinds)
            rec["rules"] = fired
            out.append(rec)
    return out


# ===========================================================================
# faithfulness-error kinds: stored parse vs gold goal state vs query text
# ===========================================================================
def _num_close(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return a == b
    return abs(a) < 1e-9 if b == 0 else abs(a - b) / abs(b) <= 0.01


def _in_query(v, nl):
    if isinstance(v, bool):
        return None                                  # traits: not a literal value
    if isinstance(v, (int, float)):
        return any(_num_close(v, x) for x, _ in score._numbers(nl))
    if isinstance(v, str):
        return score._norm(v) in score._norm(nl) if v else False
    return None


def faithfulness_kinds(domain: str, row: dict, ad) -> tuple[list[str], dict]:
    """The parser-error kinds a deployed faithfulness error exhibits."""
    gold, parse = ad.gold(row), ad.parse(row)
    kinds, ev = [], {"parse": parse, "gold": gold}
    aliases = predicate_aliases(ad.kb)

    def eq_pred(a, b):
        return a == b or b in aliases.get(a, ()) or a in aliases.get(b, ())

    if gold is None:
        kinds.append("out-of-scope query (no gold goal state)")
    else:
        if not eq_pred(parse["predicate"], gold["predicate"]):
            kinds.append("wrong predicate")
        if gold.get("context") is not None and parse.get("context") != gold["context"]:
            kinds.append("wrong context")
        gb, pb = gold.get("bindings") or {}, parse["bindings"]
        if domain == "chemistry":
            if parse.get("reaction") != gold["reaction"]:
                kinds.append("misassigned value")       # wrong reaction entity
                ev["reaction"] = (parse.get("reaction"), gold["reaction"])
            for role, sp in gold["roles"].items():
                if parse["roles"].get(role) != sp:
                    where = [r for r, s in parse["roles"].items() if s == sp]
                    kinds.append("misassigned value" if where else "dropped value")
                    ev.setdefault("roles", []).append((role, sp, parse["roles"].get(role)))
        for k, v in gb.items():
            hit = [pk for pk, pv in pb.items() if _num_close(pv, v)]
            if not hit:
                kinds.append("dropped value")
                ev.setdefault("dropped", []).append((k, v))
            elif not any(eq_pred(pk, k) for pk in hit):
                kinds.append("misassigned value")
                ev.setdefault("misassigned", []).append((k, v, hit))
            elif k in pb and not _num_close(pb[k], v):
                kinds.append("misassigned value")
                ev.setdefault("misassigned", []).append((k, v, pb[k]))
    stated = (gold or {}).get("bindings") or {}
    for pk, pv in parse["bindings"].items():
        if pk in stated and _num_close(pv, stated[pk]):
            continue      # a stated condition ("from rest" -> 0): in the query, in words
        if _in_query(pv, row["nl"]) is False:
            kinds.append("value bound that does not appear in the query")
            ev.setdefault("not_in_query", []).append((pk, pv))
    return sorted(set(kinds)), ev


# ===========================================================================
# the split, per domain and system
# ===========================================================================
def split_domain(domain: str, scored: dict, replay: list[dict]) -> dict:
    """scored: score.score_domain(domain) output (locked scorer, unmodified)."""
    ad = ADAPTERS[domain]()
    pipe = {r["id"]: r for r in json.load(open(results_path(domain, "pipeline.json"),
                                               encoding="utf-8"))["rows"]}
    meta = score.query_meta(domain)
    outcome = {(c["system"], c["id"]): c["outcome"] for c in scored["cases"]}
    rep = {(r["config"], r["id"]): r for r in replay}
    res = {}
    for cfg in CONFIGS:
        sysname = f"pct_{cfg}"
        hall, faith, correct, residual, mr, partial = [], [], [], [], [], []
        for qid, q in meta.items():
            if pipe[qid][cfg] == "refuse":
                continue
            o = outcome[(sysname, qid)]
            if o == score.MANUAL:
                mr.append(qid)
                continue
            r = rep[(cfg, qid)]
            if r["problems"]:
                hall.append(qid)
            elif o == score.CORRECT:
                correct.append(qid)
            # final scoring pass (tools/analysis/final_scoring.py): a manual verdict
            # "overspecific" passes replay and is not supported by the stated
            # characters -> faithfulness error. "partial_ancestor" (a true but less
            # specific answer) passes replay but is not the answer the query asks
            # for -> also a faithfulness error; `partial` lists them as a subset.
            elif o in (score.WRONG, "overspecific"):
                faith.append(qid)
            elif o == "partial_ancestor":
                faith.append(qid)
                partial.append(qid)
            else:
                residual.append((qid, o))
        a = scored["systems"][sysname]
        den = a["answered"] - a["manual_review"] + a["oos_answered"]
        assert len(hall) + len(faith) + len(correct) == den, \
            (domain, cfg, len(hall), len(faith), len(correct), len(partial), den, residual)
        kinds = {}
        for qid in faith:
            if qid in partial:
                ks, ev = ["less specific answer (true ancestor)"], {}
            elif cfg == "deployed":
                ks, ev = faithfulness_kinds(domain, pipe[qid], ad)
            else:
                ks, ev = ["gold goal state (not a parser error)"], {}
            kinds[qid] = {"kinds": ks, "evidence": ev}
        res[sysname] = {"answered_den": den, "hallucinations": hall,
                        "faithfulness_errors": faith, "correct_answers": correct,
                        "partial_ancestor": partial,
                        "manual_review_excluded": mr, "residual": residual,
                        "faithfulness_kinds": kinds}
    for sysname in ("rag", "baseline"):
        a = scored["systems"].get(sysname)
        if a is None:
            continue
        den = a["answered"] - a["manual_review"] + a["oos_answered"]
        res[sysname] = {"answered_den": den,
                        "hallucination_num": a["wrong_answers"] + a["oos_answered"],
                        "hallucination_bounds": a["hallucination_rate_bounds"],
                        "faithfulness": None}
    return res


# ===========================================================================
# answers resting on a condition the query does not state
# ===========================================================================
# Condition predicates introduced by the 2026-11-21 rule audit, with the value a
# stated condition takes. A deployed parse that binds one of these where the gold
# goal state does not is answering with a value the query never stated -- even
# when the locked scorer judges the number correct (gold labels assume the
# condition). Reported separately; the two rates are not changed by it.
CONDITION_PREDICATES = {
    "physics": {"initial_velocity": 0, "incline_angle": 0, "acceleration": 0,
                "friction_force": 0, "applied_force_angle": 0, "torque_angle": 90,
                "nonconservative_work": None, "pendulum_amplitude": None,
                "collision_type": None},
    "chemistry": {"gas_conditions": None, "other_reactants": None},
    "biology": {},
}


# wording rule for chemistry other_reactants = 'excess' (2026-11-22)
OTHER_REACTANTS_RX = (r"\bexcess\b|reacts? completely|(?:is|are) completely consumed"
                      r"|(?:is|are) used up")


def _text_states(domain: str, k: str, v, nl: str) -> bool:
    """True if the query text asserts condition k = v under the wording rules the
    gold conditions were built with (tools/build/build_physics_gold_conditions.py;
    chemistry: 'at STP' / standard temperature and pressure)."""
    if domain == "chemistry":
        if k == "other_reactants":
            return v == "excess" and bool(re.search(OTHER_REACTANTS_RX, nl, re.I))
        return k == "gas_conditions" and v == "stp" and bool(
            re.search(r"\bSTP\b|standard temperature and pressure", nl, re.I))
    if domain != "physics":
        return False
    bdir = os.path.join(ROOT, "tools", "build")
    if bdir not in sys.path:
        sys.path.insert(0, bdir)
    import build_physics_gold_conditions as G
    if k == "collision_type":
        for val, rx in G.COLLISION:
            if re.search(rx, nl, re.I):
                return v == val
        return False
    if k not in G.COND:
        return False
    val, _unit, rx = G.COND[k]
    return v == val and bool(re.search(rx, nl, re.I))


def unstated_condition_answers(domain: str, scored: dict) -> list[dict]:
    ad = ADAPTERS[domain]()
    conds = CONDITION_PREDICATES[domain]
    if not conds:
        return []
    pipe = json.load(open(results_path(domain, "pipeline.json"), encoding="utf-8"))["rows"]
    outcome = {c["id"]: c["outcome"] for c in scored["cases"] if c["system"] == "pct_deployed"}
    out = []
    for row in pipe:
        if row["deployed"] == "refuse":
            continue
        parse, gold = ad.parse(row), ad.gold(row)
        gb = (gold or {}).get("bindings") or {}
        hits = []
        for k, v in parse["bindings"].items():
            if k not in conds:
                continue
            cv = conds[k]
            is_condition_value = cv is None or (isinstance(v, (int, float))
                                                and not isinstance(v, bool) and v == cv)
            if is_condition_value and not (k in gb and _num_close(v, gb[k])) \
                    and not _text_states(domain, k, v, row["nl"]) \
                    and _in_query(v, row["nl"]) is not True:
                hits.append({k: v})
        if domain == "physics":
            # net-qualified quantity bound without "net" wording (NET_WORDING)
            for k, v in parse["bindings"].items():
                if k in ad.EO.NET_WORDING and not ad.EO.net_stated(k, row["nl"]):
                    hits.append({k: v})
        if hits:
            out.append({"id": row["id"], "scope": row["scope"], "nl": row["nl"],
                        "unstated_bindings": hits, "locked_scorer_outcome": outcome[row["id"]]})
    return out


# ===========================================================================
# results/paper_numbers.json
# ===========================================================================
def wilson(n, d, z=1.959963984540054):
    """Wilson score 95% interval for n successes in d trials, as fractions."""
    if not d:
        return None
    p = n / d
    den = 1 + z * z / d
    mid = (p + z * z / (2 * d)) / den
    half = z * ((p * (1 - p) / d + z * z / (4 * d * d)) ** 0.5) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def _r(n, d):
    ci = wilson(n, d)
    return {"n": n, "d": d, "pct": round(100 * n / d, 2) if d else None,
            "ci95_pct": None if ci is None else [round(100 * ci[0], 2),
                                                 round(100 * ci[1], 2)]}


def _refusals(rows: list[dict], cfg: str) -> dict:
    out: dict = {}
    for r in rows:
        if r[cfg] != "refuse":
            continue
        t = r.get(f"{cfg}_type") or "?"
        if cfg == "oracle" and t == "-":
            stage = "no_gold_goal_state"
        elif cfg == "deployed" and r.get("stage") == "parser":
            stage = "parse_stage"
        else:
            stage = "engine_stage"
        out.setdefault(r["scope"] + "_scope", {}).setdefault(stage, Counter())[t] += 1
    return {sc: {st: dict(sorted(c.items())) for st, c in v.items()} for sc, v in out.items()}


def paper_numbers(scores: dict, splits: dict, replays: dict) -> dict:
    out = {"_note": ("Every value is a raw count n over a denominator d with the derived "
                     "percentage. Computed from stored runs only by "
                     "tools/analysis/make_domain_table.py via split_metrics.paper_numbers. "
                     "Per-query judgments come from the locked scorer (score.py)."),
           "definitions": {
               "answered": "answers to in-scope plus out-of-scope queries",
               "unconditional_accuracy": "correct (incl. correct refusals) / (in-scope "
                                         "- manual review)",
               "conditional_accuracy": "correct answers / (in-scope answered - manual review)",
               "hallucination_rate": "PCT: answers failing replay / answered (excl. manual "
                                     "review); RAG, raw: rubric Sec. 6",
               "faithfulness_error_rate": "PCT only: replay-passing answers scored wrong, "
                                          "over-specific or less specific + answered "
                                          "out-of-scope / answered",
               "F5a_F5b": "deployed in-scope engine-stage F5: F5a if the gold goal state "
                          "also fails (KB gap), F5b if it succeeds (parser-induced)"},
           "domains": {}}
    for d, sc in scores.items():
        if d.startswith("_"):       # metadata (e.g. _final_scoring), not a domain
            continue
        pipe = json.load(open(results_path(d, "pipeline.json"), encoding="utf-8"))["rows"]
        dom = {}
        for sysname, a in sc["systems"].items():
            mr = a["manual_review"]
            e = {"queries": {"in_scope": a["in_scope"], "out_of_scope": a["out_of_scope"]},
                 "answered": {"in_scope": a["answered"], "out_of_scope": a["oos_answered"],
                              "total": a["answered"] + a["oos_answered"]},
                 "manual_review": mr,
                 "correct": {"total": a["correct"], "answers": a["correct_answers"],
                             "refusals": a["correct_refusals"]},
                 "wrong_answers_in_scope": a["wrong_answers"],
                 # final scoring pass (2026-11-24): manual verdicts; wrong_answers
                 # above includes overspecific (hallucination numerator for baselines)
                 "partial_ancestor_in_scope": a.get("partial_ancestor", 0),
                 "overspecific_in_scope": a.get("overspecific", 0),
                 "unconditional_accuracy": _r(a["correct"], a["in_scope"] - mr),
                 "conditional_accuracy": _r(a["correct_answers"], a["answered"] - mr),
                 "out_of_scope_refusal_rate": _r(a["oos_refused"], a["out_of_scope"])}
            if mr:
                e["unconditional_accuracy_bounds"] = [
                    _r(a["correct"], a["in_scope"]), _r(a["correct"] + mr, a["in_scope"])]
                e["conditional_accuracy_bounds"] = [
                    _r(a["correct_answers"], a["answered"]),
                    _r(a["correct_answers"] + mr, a["answered"])]
            sp = splits[d][sysname]
            if sysname.startswith("pct"):
                cfg = sysname[4:]
                e["hallucination_rate"] = _r(len(sp["hallucinations"]), sp["answered_den"])
                e["faithfulness_error_rate"] = _r(len(sp["faithfulness_errors"]),
                                                  sp["answered_den"])
                e["identity"] = {"hallucinations": len(sp["hallucinations"]),
                                 "faithfulness_errors": len(sp["faithfulness_errors"]),
                                 "correct_answers": len(sp["correct_answers"]),
                                 "partial_ancestor": len(sp.get("partial_ancestor") or []),
                                 "answered": sp["answered_den"]}
                e["refused_by_type"] = _refusals(pipe, cfg)
                rs = [r for r in replays[d] if r["config"] == cfg]
                e["replay"] = {"answers_checked": len(rs),
                               "failures": sum(1 for r in rs if r["problems"])}
                kinds = Counter()
                for v in sp["faithfulness_kinds"].values():
                    kinds.update(v["kinds"])
                e["faithfulness_error_kinds"] = {
                    "counts_per_kind": dict(sorted(kinds.items())),
                    "rows_with_more_than_one_kind": sorted(
                        q for q, v in sp["faithfulness_kinds"].items() if len(v["kinds"]) > 1),
                    "per_query": {str(q): v["kinds"]
                                  for q, v in sorted(sp["faithfulness_kinds"].items())}}
                if cfg == "deployed":
                    f5 = [r for r in pipe if r["scope"] == "in" and r["deployed"] == "refuse"
                          and r.get("stage") != "parser"
                          and str(r.get("deployed_type") or "").startswith("F5")]
                    f5a = [r["id"] for r in f5 if r["oracle_status"] != "success"]
                    f5b = [r["id"] for r in f5 if r["oracle_status"] == "success"]
                    e["F5a_F5b"] = {"F5a": len(f5a), "F5b": len(f5b),
                                    "F5a_ids": f5a, "F5b_ids": f5b}
                    uc = unstated_condition_answers(d, sc)
                    e["answers_on_an_unstated_condition"] = {
                        "n": len(uc),
                        "scored_correct": sum(1 for u in uc
                                              if u["locked_scorer_outcome"] == "correct"),
                        "rows": uc}
            else:
                e["hallucination_rate"] = _r(sp["hallucination_num"], sp["answered_den"])
                lo, hi = sp["hallucination_bounds"]
                e["hallucination_rate_bounds_pct"] = [
                    None if lo is None else round(100 * lo, 2),
                    None if hi is None else round(100 * hi, 2)]
                e["faithfulness_error_rate"] = None
                e["refused_in_scope"] = a["in_scope"] - a["answered"]
            dom[sysname] = e
        if "pct_deployed" in dom and "pct_oracle" in dom:
            dep, orc = sc["systems"]["pct_deployed"], sc["systems"]["pct_oracle"]
            dom["deployed_vs_oracle"] = {
                "answer_rate": {"deployed": _r(dep["answered"], dep["in_scope"]),
                                "oracle": _r(orc["answered"], orc["in_scope"])},
                "unconditional_accuracy": {
                    "deployed": dom["pct_deployed"]["unconditional_accuracy"],
                    "oracle": dom["pct_oracle"]["unconditional_accuracy"]}}
        out["domains"][d] = dom
    return out


def main() -> int:
    total_fail = 0
    for d in DOMAIN_ORDER:
        rep = replay_domain(d)
        for cfg in CONFIGS:
            rs = [r for r in rep if r["config"] == cfg]
            bad = [r for r in rs if r["problems"]]
            total_fail += len(bad)
            notes = Counter(n for r in rs for n in r["notes"])
            kinds = Counter()
            for r in rs:
                kinds.update(r.get("step_kinds", {}))
            print(f"{d:<10} {cfg:<9} answers checked {len(rs):>4}  "
                  f"(in {sum(r['scope'] == 'in' for r in rs)}, "
                  f"oos {sum(r['scope'] == 'out' for r in rs)})  failures {len(bad)}")
            print(f"{'':<20} steps {dict(kinds)}  notes {dict(notes)}")
            for r in bad:
                print("   FAIL", json.dumps(r, default=str))
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
