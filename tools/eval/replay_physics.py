"""Physics PCT deployed and oracle runs, by REPLAY of stored parses.

Physics's deployed parses are frozen in results/physics/deployed_parses.json (written
once by tools/eval/capture_parses.py physics). This replays them
through the engine, and runs the oracle on the gold goal states built by
eval_oracle_physics.build_gold_bindings. No model call. Writes results/physics/pipeline.json in the same row shape eval_pipeline.py
writes for chemistry and biology, so one scorer reads all three domains.

Run:
    python tools/eval/replay_physics.py
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for p in (ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from layout import DOMAINS, results_path  # noqa: E402

from pct.engine import load_kb, prove  # noqa: E402
from eval_oracle_physics import (D_MAX, _kb_rules_by_id, build_gold_bindings,  # noqa: E402
                                 gold_goal_predicate)


def _claim(res: dict):
    if res.get("status") != "success":
        return None
    return {"kind": "value", "value": res.get("value"), "unit": res.get("unit"),
            "rules": [s.get("id") for s in res.get("derivation", []) if s.get("type") == "rule"],
            "derivation_steps": len(res.get("derivation", []))}


def _failure(res: dict):
    if res.get("status") == "success":
        return None
    return {"type": res.get("type"), "reason": res.get("reason"),
            "goal": res.get("goal"), "rule": res.get("rule")}


def main() -> int:
    kb = load_kb(DOMAINS["physics"]["kb1"])
    rules_by_id = _kb_rules_by_id(kb)
    stored = json.load(open(DOMAINS["physics"]["queries"]["structured"], encoding="utf-8"))
    frozen = json.load(open(results_path("physics", "deployed_parses.json"), encoding="utf-8"))
    parses = {x["id"]: x for x in frozen["rows"]}
    rows = []
    for i, r in enumerate(stored, 1):
        oos = r["category"] == "oos"
        assert parses[i]["nl"] == r["query"], i
        parsed = parses[i]["parsed"]
        if not parsed or parsed.get("status") == "failure":
            dres = {"status": "failure", "type": (parsed or {}).get("type"),
                    "reason": (parsed or {}).get("reason"), "stage": "parser"}
        else:
            dres = prove({"predicate": parsed["predicate"], "unit": parsed["unit"],
                          "context": parsed["context"]}, parsed["bindings"], kb)
        if oos:
            ores = {"status": "failure", "type": "-",
                    "reason": "no gold goal state exists for this query"}
        else:
            bnd, _src = build_gold_bindings(r, rules_by_id)
            ores = ({"status": "failure", "type": "unmapped"} if bnd is None else
                    prove({"predicate": gold_goal_predicate(r),
                           "unit": r.get("expected_unit"),
                           "context": r.get("expected_context")}, bnd, kb, d_max=D_MAX))
        rows.append({
            "id": i, "part": r["category"], "nl": r["query"],
            "scope": "out" if oos else "in",
            "qtype": "numeric",
            "truth": None if oos else r.get("expected_value"),
            "expected_unit": None if oos else r.get("expected_unit"),
            "deployed": dres.get("value") if dres.get("status") == "success" else "refuse",
            "deployed_status": dres.get("status"),
            "deployed_type": None if dres.get("status") == "success" else dres.get("type"),
            "stage": dres.get("stage", "engine"),
            "deployed_failure": _failure(dres),
            "deployed_claim": _claim(dres),
            "oracle": ores.get("value") if ores.get("status") == "success" else "refuse",
            "oracle_status": ores.get("status"),
            "oracle_type": None if ores.get("status") == "success" else ores.get("type"),
            "oracle_failure": _failure(ores),
            "oracle_claim": _claim(ores),
        })
    out = {"domain": "physics", "n": len(rows),
           "frozen_parses": "results/physics/deployed_parses.json",
           "captured_at": frozen.get("captured_at"), "settings": frozen.get("settings"),
           "rows": rows}
    path = results_path("physics", "pipeline.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    ins = [x for x in rows if x["scope"] == "in"]
    dep = sum(1 for x in ins if x["deployed_status"] == "success")
    orc = sum(1 for x in ins if x["oracle_status"] == "success")
    print(f"physics replay: in-scope {len(ins)}, deployed answered {dep}, oracle answered {orc}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
