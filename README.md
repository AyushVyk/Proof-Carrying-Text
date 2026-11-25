# Proof-Carrying Text (PCT)

PCT answers a science question by having an LLM translate it into a small typed goal state, which a deterministic engine then proves from a knowledge base (KB).
Every answer carries its derivation; when no derivation exists, the system refuses with a typed failure (F1-F6) instead of guessing.

## Layout

```
pct/                    the system: engine.py (proof engine), parser.py (LLM parser), renderer.py
layout.py               where each domain's files live
domains/<domain>/       kb1_<domain>.json (rules, primitives, data), kb2_<domain>.json (parser
                        schema and prompt), queries/ (the 300-query test set with gold goal states)
domains/query_set_locks.json   hashes of the test sets
docs/scoring_rubric.md  the pre-registered scoring rubric (hash-locked in scoring_rubric.lock.json)
results/<domain>/       stored outputs of the test run: parser output (deployed_parses.json),
                        PCT answers and typed failures (pipeline.json), RAG and raw-LLM baseline
                        responses (baselines.json; physics: evaluation_300.json), physics gold
                        goal states (oracle_eval.json)
results/                scores, paper numbers, tables, manual-review verdicts, figures/
supplement/             one JSONL line per query and system, with field definitions in its README
experiments/balancing/  probe: chemical-equation balancing as rule derivation
tools/eval/             run the systems (parser, engine, baselines)
tools/analysis/         score and tabulate stored outputs (no model calls)
demos/                  engine_demo.py (no model), parser_demo.py, pipeline_demo.py, app.py (web UI)
tests/test_soundness.py soundness suite: checks derivation structure, not just values
tests/test_kb_integrity.py KB integrity: rule inputs, element conservation, taxon envelopes, felid source values
scripts/reproduce.sh    rebuild every number, table and figure from the stored outputs
```

Domains: physics (kinematics, dynamics, energy), chemistry (stoichiometry), biology
(vertebrate and felid taxonomy). One engine serves all three, with no domain-specific code.

## Requirements

- Python 3.10 or later (tested with 3.13): `pip install -r requirements.txt`
- For anything that calls the model (running a query, re-running the pipeline):
  [Ollama](https://ollama.com) serving Mistral-7B-Instruct, 4-bit `q4_K_M`:
  `ollama pull mistral:7b-instruct-q4_K_M` (default address `http://127.0.0.1:11434`;
  set `OLLAMA_URL` to change it)

Rebuilding the numbers from stored outputs and running the tests need neither Ollama nor a GPU.

## Run one query

From the repository root, with Ollama running:

```bash
PYTHONPATH=. python - <<'EOF'
import sys
sys.path.insert(0, "tools/eval")
from layout import DOMAINS
from pct.parser import Parser
from pct.engine import load_kb, prove
from pct.renderer import render
import eval_pipeline

domain = "physics"          # or "chemistry", "biology"
query = "What is the kinetic energy of a 3 kg ball moving at 4 m/s?"

parsed = Parser(DOMAINS[domain]["kb2"]).parse(query)       # Stage 1: LLM -> goal state
if parsed.get("status") == "failure":                       # the parser refused
    result = parsed
elif domain == "physics":                                   # Stage 2: the engine
    goal = {k: parsed[k] for k in ("predicate", "unit", "context")}
    result = prove(goal, parsed["bindings"], load_kb(DOMAINS[domain]["kb1"]))
else:                                                       # entity data from the KB, then the engine
    deployed, _, _ = eval_pipeline.RUNNERS[domain]()
    result = deployed(parsed)["result"]
print(render(result))                                       # Stage 3: answer + derivation
EOF
```

Output: `Result: 24 J`, followed by each given value and the rule applied, with its source.

Or run the demos: `PYTHONPATH=. python demos/pipeline_demo.py` (five physics queries end to end),
`demos/engine_demo.py` (engine only, no model), or `demos/app.py` (web UI; needs `pip install gradio`).

## What produces each table and figure

All of these read stored outputs only. `bash scripts/reproduce.sh` runs them in order
(set `PYTHON` to choose the interpreter).

| output | script |
|---|---|
| `results/scores.json` (per-query outcomes, all systems) | `tools/analysis/final_scoring.py`: the locked scorer's outcomes (`results/final_scoring/scores_locked_auto.json`, from `tools/analysis/score.py`) with the blind manual verdicts (`results/manual_review_*final.jsonl`) merged in |
| `results/three_domain_table.md`, `.txt` (main results table), `results/paper_numbers.json` (every reported rate with n, d and Wilson 95% interval), `results/manual_review.md` | `tools/analysis/make_domain_table.py` (replays every PCT answer through the engine) |
| `results/underspecified_table.md`, `underspecified_baseline_answers.md`, `underspecified.json` | `tools/analysis/underspecified.py` |
| `results/figures/tractability.png`, `.pdf` | `tools/analysis/figure_tractability.py` |
| `supplement/*.jsonl`, `supplement/README.md`, `supplement/rebuild_check.json` | `tools/analysis/export_supplement.py` (rebuilds the totals from the JSONL and checks them against `paper_numbers.json`) |
| `experiments/balancing/output.txt` | `experiments/balancing/run_balancing.py` |
| `experiments/balancing/analysis.txt` | `experiments/balancing/analyse_sequential.py` |

Tests: `PYTHONPATH=. python tests/test_soundness.py && PYTHONPATH=. python tests/test_kb_integrity.py`
(75 soundness cases; 194 KB integrity checks).

## Re-run the full pipeline

This calls the model and replaces the stored outputs. Parser output varies slightly between
runs, so the numbers will not match exactly. For each domain `<d>` in physics, chemistry, biology:

```bash
export PYTHONPATH=.
python tools/eval/capture_parses.py <d> --run 1        # Stage 1 on all 300 queries -> results/<d>/deployed_runs/run1.json
python tools/eval/capture_parses.py <d> --install 1    # use that run -> results/<d>/deployed_parses.json

python tools/eval/eval_oracle_physics.py               # physics gold goal states -> results/physics/oracle_eval.json
python tools/eval/replay_physics.py                    # physics engine, deployed + oracle -> results/physics/pipeline.json
python tools/eval/eval_pipeline.py chemistry           # same for chemistry and biology -> results/<d>/pipeline.json
python tools/eval/eval_pipeline.py biology

python tools/eval/eval_three_system_physics.py         # physics RAG + raw baselines -> results/physics/evaluation_300.json
python tools/eval/run_baselines.py chemistry           # chemistry / biology baselines -> results/<d>/baselines.json
python tools/eval/run_baselines.py biology

python tools/analysis/score.py                         # locked scorer -> results/scores.json
```

Then copy `results/scores.json` to `results/final_scoring/scores_locked_auto.json`, review the
cases the scorer defers to manual review (listed in `results/manual_review.md`) and record the
verdicts in `results/manual_review_final.jsonl` (keyed through `results/manual_review_key.json`),
and run `bash scripts/reproduce.sh`. In the stored run, `results/transport_rerun.json` records
the rows whose model call failed in transport and were sent again, and
`results/manual_review_remaining_*` holds a second review round; `final_scoring.py` reads
both, so replace or empty them for a new run.

## License

MIT (see `LICENSE`).
