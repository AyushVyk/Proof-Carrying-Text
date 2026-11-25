"""PCT proof engine: backward-chaining over a closed-world physics KB.

Pure standard library. Returns either a derivation chain or a typed failure
(F1: depth exceeded, F2: missing primitive, F3: missing rule, F4: defect -- a
reached guard or formula raised, F5: underivable, F6: no applicable rule). Where several candidate rules fail with different
types, the most informative certificate is reported -- see _FAILURE_PRECEDENCE.

F5 results also carry "family": "F5", so callers can group the family without
string-matching the type. `prove()` deliberately does NOT sub-classify F5 into
"genuine KB gap" vs "parser-induced": that distinction is a property of the
QUERY, not of a goal state, and deciding it requires the query's gold parse in
addition to the deployed one. A single-goal prover has no access to that, so the
classification lives in the reconciliation layer -- see reconcile_f5.py.
"""
from __future__ import annotations

import json
import math
from typing import Any

SAFE_GLOBALS: dict[str, Any] = {
    "__builtins__": {},
    "math": math,
    "__import__": __import__,
}

DEFAULT_DEPTH = 5
DEFAULT_CANDIDATES = 100


def predicate_aliases(kb: dict) -> dict:
    """Surface synonyms declared by the loaded KB.

    A KB may map a predicate to other predicate names that denote the same
    quantity, so that a sub-goal for one is satisfied by a binding of another.
    The map is domain knowledge and lives with the domain: it is read from the
    KB's top-level `predicate_aliases` field, e.g. a query saying "a net force
    of 83.2 N" binds `net_force` while the rule consuming it declares its input
    as `force`. Without such an alias the search treats the input as unbound and
    derives it from whatever rule outputs it, manufacturing a quantity the query
    already supplied.

    Aliases are directional by design. A synonym pair may be declared both ways
    only when neither predicate decomposes into the other; where one does have
    decomposition rules, aliasing into it would let a goal short-circuit past
    them. Which direction is safe is a domain judgement, so the KB states it.

    DEFAULT: a KB that omits the field declares no aliases, so a sub-goal is
    satisfied only by a binding of its own exact name. That is the safe default
    -- a new domain gets strict exact-match until it explicitly opts in.
    """
    return kb.get("predicate_aliases", {}) or {}


def _lookup_binding(bindings: dict, predicate: str, kb: dict) -> dict | None:
    if predicate in bindings:
        return bindings[predicate]
    for alias in predicate_aliases(kb).get(predicate, ()):
        if alias in bindings:
            return bindings[alias]
    return None


def load_kb(path: str) -> dict:
    """Load a knowledge base from an explicit path.

    The path is REQUIRED. The engine has no default domain: physics, chemistry
    and biology are peers, and nothing in this module knows which one a caller
    means. A default here would have made one science the implicit meaning of
    "the knowledge base".
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class _Counter:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n


def _transition_allowed(
    kb: dict, parent_ctx: str | None, predicate: str, rule_ctx: str | None
) -> bool:
    """Is this cross-context step declared by the loaded KB?

    A subgoal inherits the context of the rule that raised it, and by default
    only rules in that same context may satisfy it. Without that constraint a
    subgoal is unconstrained, every rule matches it, and the search drifts into
    an unrelated regime to "derive" a quantity the query never implied.

    Some chains legitimately cross regimes, so a KB may declare the crossings it
    permits in a top-level `context_transitions` field: a list of
    [parent context, subgoal predicate, satisfying rule context] triples. The
    list is domain knowledge and lives with the domain, not in the engine.

    DEFAULT: a KB that omits the field declares no transitions, so every subgoal
    is confined to its parent rule's context. That is the safe default -- a new
    domain gets strict single-context behaviour until it explicitly opts in.
    """
    target = [parent_ctx, predicate, rule_ctx]
    for entry in kb.get("context_transitions", ()):
        if list(entry) == target:
            return True
    return False


def _context_matches(rule_ctx: str | None, goal_ctx: str | None) -> bool:
    if goal_ctx is None:
        return True
    return rule_ctx == goal_ctx


def _find_primitive(kb: dict, predicate: str) -> dict | None:
    for prim in kb.get("primitives", []):
        if prim["predicate"] == predicate:
            return prim
    return None


def _find_rules(
    kb: dict,
    predicate: str,
    context: str | None,
    allow_transitions: bool = False,
) -> list[dict]:
    """Rules that output `predicate` and are reachable from `context`.

    `context=None` is unconstrained and matches every rule; that form is used
    only for the F3 probe ("does any rule anywhere output this predicate?").

    `allow_transitions` widens an otherwise exact context match by the
    transitions the KB itself declares. It is set for subgoals only: a
    top-level goal keeps the exact match its parsed context implies, so this
    cannot change which rules a top-level goal may use.
    """
    out = []
    for rule in kb.get("rules", []):
        if rule["output"]["predicate"] != predicate:
            continue
        rule_ctx = rule.get("context")
        if _context_matches(rule_ctx, context):
            out.append(rule)
        elif allow_transitions and _transition_allowed(kb, context, predicate, rule_ctx):
            out.append(rule)
    return out


# Failure-type precedence, most informative and most certain first. When a goal
# has several candidate rules and more than one fails, the reported type must
# describe the actual reason the goal is not derivable -- not whichever
# candidate happened to be tried last.
#
#   F4  DEFECT            -- a reached guard or formula raised. The KB itself is
#                            broken, which makes every other diagnosis on this
#                            goal untrustworthy, so it is reported loudest.
#   F5  leaf exhaustion   -- the sub-goal predicate is the output of no rule.
#                            A certificate: no extra budget can ever help.
#   F2  missing primitive -- a named terminal is absent from the KB. Certificate.
#   F3  missing rule      -- rules output this predicate but none in this
#                            context. Certificate at the signature level.
#   F6  no applicable rule-- reachable rules were evaluated with all inputs
#                            bound and no guard held. A certificate, but unlike
#                            F5/F2/F3 it names nothing missing: the KB is
#                            complete as far as it goes and the facts simply do
#                            not match. Ranked below the types that DO name a
#                            missing element, because those are actionable.
#   F1  depth cutoff      -- the search ran out of budget. NOT a certificate:
#                            the branch may well be derivable at greater depth.
#
# F5/F2/F3 keep their relative order from before this table gained F4-as-defect
# and F6, so multi-candidate resolutions among them are unchanged.
_FAILURE_PRECEDENCE: dict[str, int] = {
    "F4": 0, "F5": 1, "F2": 2, "F3": 3, "F6": 4, "F1": 5,
}


def _failure_rank(failure: dict) -> tuple[int, int]:
    # Within F5, a genuine leaf exhaustion outranks a cycle cutoff: both are
    # certificates for their own branch, but only the former names a KB gap.
    return (
        _FAILURE_PRECEDENCE.get(failure.get("type"), 99),
        1 if failure.get("cycle") else 0,
    )


def _stronger_failure(current: dict | None, candidate: dict | None) -> dict | None:
    """Return whichever of the two failures is the more informative certificate.

    Ties keep `current`, so among equally-ranked failures the earliest candidate
    rule wins and the choice stays deterministic.
    """
    if current is None:
        return candidate
    if candidate is None:
        return current
    return candidate if _failure_rank(candidate) < _failure_rank(current) else current


def prove(
    goal: dict,
    bindings: dict,
    kb: dict,
    d_max: int = DEFAULT_DEPTH,
    c_max: int = DEFAULT_CANDIDATES,
) -> dict:
    """Prove `goal` from `bindings` and `kb`. Returns a result dict.

    Success: {"status": "success", "goal", "value", "unit", "derivation": [...]}.
    Failure: {"status": "failure", "type": "F1"|"F2"|"F3"|"F4"|"F5"|"F6",
              "reason", ...}. F5 results also carry "family": "F5".
    """
    memo: dict = {}
    counter = _Counter()
    result = _prove(
        goal, bindings, kb, d_max, c_max, memo, counter, depth=0, path=frozenset()
    )
    if result["status"] == "success":
        result["derivation"] = _normalize_chain(result["derivation"])
    return result


def _has_rule_step(deriv: list[dict]) -> bool:
    return any(s["type"] == "rule" for s in deriv)


def _normalize_chain(chain: list[dict]) -> list[dict]:
    seen: set = set()
    out: list[dict] = []
    for step in chain:
        sid = step["step"]
        if sid in seen:
            continue
        seen.add(sid)
        out.append(step)
    for i, step in enumerate(out, 1):
        step["step"] = i
    return out


def _goal_key(goal: dict) -> tuple:
    return (goal["predicate"], goal.get("context"), goal.get("unit"))


def _path_key(goal: dict) -> tuple:
    return (goal["predicate"], goal.get("context"))


def _prove(
    goal: dict,
    bindings: dict,
    kb: dict,
    d_max: int,
    c_max: int,
    memo: dict,
    counter: _Counter,
    depth: int,
    path: frozenset = frozenset(),
) -> dict:
    """Prove `goal`; `path` is the set of (predicate, context) already being
    proved on the current branch, used to cut cycles in the predicate graph."""
    key = _goal_key(goal)
    if key in memo:
        return memo[key]

    pred = goal["predicate"]

    b = _lookup_binding(bindings, pred, kb)
    if b is not None:
        result = {
            "status": "success",
            "goal": goal,
            "value": b["value"],
            "unit": b.get("unit", goal.get("unit")),
            "derivation": [
                {
                    "step": counter.next(),
                    "type": "binding",
                    "predicate": pred,
                    "value": b["value"],
                    "unit": b.get("unit", goal.get("unit")),
                }
            ],
        }
        memo[key] = result
        return result

    # Primitives serve as TERMINALS for sub-goals only. A top-level goal must
    # derive through a rule -- otherwise a query like "what is the pressure
    # of a 50N force on 5 m^2" short-circuits to the standard_atm_pressure
    # primitive instead of computing F/A.
    if depth > 0:
        prim = _find_primitive(kb, pred)
        if prim is not None:
            result = {
                "status": "success",
                "goal": goal,
                "value": prim["value"],
                "unit": prim["unit"],
                "derivation": [
                    {
                        "step": counter.next(),
                        "type": "primitive",
                        "id": prim["id"],
                        "predicate": pred,
                        "value": prim["value"],
                        "unit": prim["unit"],
                    }
                ],
            }
            memo[key] = result
            return result

    # Cycle guard. The predicate graph has real cycles (force <-> acceleration;
    # velocity <-> angular_velocity <-> period), so a branch with no terminating
    # binding can walk one forever. Re-deriving a (predicate, context) already
    # open on this branch cannot yield anything new -- the bindings are the same
    # ones that failed to terminate it the first time -- so the branch is
    # genuinely non-derivable, not merely out of budget. Checked after the
    # binding and primitive lookups so it can never suppress a terminal that
    # would have succeeded.
    #
    # Safe alongside `memo` because only successes are memoized, and a success
    # is path-independent. Memoizing a failure would not be: a branch pruned
    # here is unprovable only on *this* path, not on every path.
    pkey = _path_key(goal)
    if pkey in path:
        return {
            "status": "failure",
            "type": "F5",
            "family": "F5",
            "reason": f"cycle: {pred} in context {goal.get('context')!r} is already on the derivation path",
            "goal": goal,
            "cycle": True,
        }

    if depth >= d_max:
        return {
            "status": "failure",
            "type": "F1",
            "reason": "depth exceeded",
            "depth": depth,
        }

    # Subgoals (depth > 0) may additionally use a whitelisted cross-context
    # transition; a top-level goal is held to its parsed context exactly.
    candidates = _find_rules(
        kb, pred, goal.get("context"), allow_transitions=depth > 0
    )
    if not candidates:
        any_match = _find_rules(kb, pred, None)
        if not any_match:
            # `any_match` is context-blind, so an empty result means no rule in
            # the KB produces this predicate at all.
            return {
                "status": "failure",
                "type": "F5",
                "family": "F5",
                "reason": "underivable: no rule in the KB produces this predicate",
                "goal": goal,
            }
        return {
            "status": "failure",
            "type": "F3",
            "reason": "missing rule",
            "required_signature": f"{pred} in context {goal.get('context')!r}",
        }

    best_failure: dict | None = None
    # Every candidate that failed with F5: (rule, the input it stopped at, the
    # leaf predicate nothing could supply). Reported when the result is F5 and
    # more than one candidate contributed, so the certificate names every way
    # the goal could have been derived, not only the first. Failure path only:
    # type, precedence and every success are unchanged.
    f5_candidates: list[dict] = []
    for rule in candidates[:c_max]:
        sub_outcomes: list = []
        sub_failure: dict | None = None
        for inp in rule["inputs"]:
            # The subgoal inherits this rule's context rather than being left
            # unconstrained. This is what closes the leak: `context: None` made
            # every rule in the KB a candidate for every subgoal.
            sub_goal = {
                "predicate": inp["predicate"],
                "unit": inp["unit"],
                "context": rule.get("context"),
            }
            sub = _prove(
                sub_goal, bindings, kb, d_max, c_max, memo, counter,
                depth + 1, path | {pkey},
            )
            if sub["status"] == "failure":
                sub_failure = sub
                if sub.get("type") == "F5":
                    entry = {"rule": rule["id"], "input": inp["predicate"],
                             "missing": (sub.get("goal") or {}).get("predicate",
                                                                    inp["predicate"])}
                    if sub.get("cycle"):
                        entry["cycle"] = True
                    if sub.get("candidates"):
                        entry["candidates"] = sub["candidates"]
                    f5_candidates.append(entry)
                break
            sub_outcomes.append((inp["name"], sub["value"], sub["derivation"]))
        if sub_failure is not None:
            best_failure = _stronger_failure(best_failure, sub_failure)
            continue

        compound = [t for t in sub_outcomes if _has_rule_step(t[2])]
        direct = [t for t in sub_outcomes if not _has_rule_step(t[2])]
        sub_chain: list = []
        for _, _, deriv in compound + direct:
            sub_chain.extend(deriv)
        sub_values = {name: val for name, val, _ in sub_outcomes}

        # Applicability, checked before computation. A rule may declare a
        # `guard`: a boolean expression over its bound inputs saying WHEN the
        # rule applies, kept separate from the `formula` that says WHAT it
        # computes. A guard that evaluates false means the rule simply does not
        # apply to these facts -- a non-match (F6), not an error. A guard that
        # RAISES is a defect in the guard expression itself, so it is reported
        # like a formula defect (F4).
        #
        # No guard means "applies whenever the inputs are present", which is the
        # behaviour of every rule that predates this field.
        if "guard" in rule:
            try:
                applies = eval(rule["guard"], SAFE_GLOBALS, dict(sub_values))
            except Exception as e:
                best_failure = _stronger_failure(
                    best_failure,
                    {
                        "status": "failure",
                        "type": "F4",
                        "reason": f"guard evaluation error: {e}",
                        "rule": rule["id"],
                    },
                )
                continue
            if not applies:
                best_failure = _stronger_failure(
                    best_failure,
                    {
                        "status": "failure",
                        "type": "F6",
                        "reason": "no applicable rule: guard not satisfied",
                        "rule": rule["id"],
                        "goal": goal,
                    },
                )
                continue

        try:
            value = eval(rule["formula"], SAFE_GLOBALS, dict(sub_values))
        except Exception as e:
            best_failure = _stronger_failure(
                best_failure,
                {
                    "status": "failure",
                    "type": "F4",
                    "reason": f"formula evaluation error: {e}",
                    "rule": rule["id"],
                },
            )
            continue

        input_units = {inp["name"]: inp["unit"] for inp in rule["inputs"]}
        structured_inputs = {
            name: {"value": val, "unit": input_units[name]}
            for name, val in sub_values.items()
        }
        step = {
            "step": counter.next(),
            "type": "rule",
            "id": rule["id"],
            "formula": rule["formula"],
            "proof": rule.get("proof", ""),
            "inputs": structured_inputs,
            "output": value,
            "unit": rule["output"]["unit"],
        }
        result = {
            "status": "success",
            "goal": goal,
            "value": value,
            "unit": rule["output"]["unit"],
            "derivation": sub_chain + [step],
        }
        memo[key] = result
        return result

    if best_failure is not None:
        if best_failure.get("type") == "F5" and len(f5_candidates) > 1:
            best_failure = dict(best_failure, candidates=f5_candidates)
        return best_failure
    return {
        "status": "failure",
        "type": "F5",
        "family": "F5",
        "reason": "underivable",
        "goal": goal,
    }
