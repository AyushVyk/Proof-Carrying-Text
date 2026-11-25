# PCT frozen test set: queries and stored outputs

One JSON-lines file per domain, one line per query (300 each): the query, its gold goal state and expected answer, the stored output of all four systems (PCT deployed, PCT oracle, RAG, raw/unconstrained) and the final verdict of each. Exported read-only from the frozen test run of 2026-11-24 (run 4) after the final scoring pass, by tools/analysis/export_supplement.py. No model was called and no system re-run; the only computation is the PCT replay check (the engine re-proving each stored answer from its stored goal state), which is how the hallucination numbers were produced.

Systems: **PCT deployed** = Mistral 7B parser -> backward-chaining engine; **PCT oracle** = the engine on the gold goal state; **RAG** = Mistral 7B with retrieved KB passages; **raw** = Mistral 7B unconstrained.

## Fields (one JSON object per line)

| field | meaning |
|---|---|
| `id` | query id within the domain (1-300) |
| `domain` | physics / chemistry / biology |
| `category` | `single` (one rule application), `multi` (a chain of rules), `oos` (out of scope). Chemistry and biology: the query set's `step_class`. Physics: the query set's `shallow` -> `single`, `medium` -> `multi` (the generator's design: single-rule vs 2-4 rule chains); the stored label is kept in `category_source`. |
| `scope` | `in` / `out` (out = `oos`) |
| `query` | the query text, verbatim as sent to every system |
| `underspecified` | true if the query is in the underspecified layer (results/underspecified.json, `underspecified_queries`): an in-scope query whose answer depends on a condition the query does not state |
| `missing_conditions` | the unstated condition predicate(s) for an underspecified query, else `[]` |
| `gold_goal_state` | the gold goal state the PCT oracle is run on. Physics: `gold_goal` of results/physics/oracle_eval.json (predicate, unit, context, bindings). Chemistry: the query set's `gold` (reaction, species roles, given values, goal predicate). Biology: gold form, target taxon, traits, characters. `null` for out-of-scope queries and for the 3 biology `name_only` queries (q33-q35: the query states no trait, so a goal state has no evidence) |
| `expected` | physics / chemistry: `{value, unit}`. Biology: `{answer, truth, expected_classification, gold_organism}`: `answer` is the query set's expected output (`yes`, a taxon, or `refuse`), `truth` the true answer (`yes`/`no` for is-a, the taxon for identification). `null` out of scope |
| `query_type` | biology only: `identification` ("What is it?", gold form `what_is`), `membership` ("Is it a t?", gold form `is_a`), `other` (out of scope). `query_type_detail` gives the stored `part` and `gold_form` |
| `pct_refusal_scored_correct` | biology only: true if the locked rubric scores a PCT typed failure on this query as a **correct** refusal: out-of-scope queries (rubric §4.4) and is-a queries whose truth is `no` (§4.3: the KB declares no sibling disjointness, so non-membership is not derivable and the typed failure is the right output) |
| `expected_refuse_in_query_set` | biology only: the query set's expected output is `refuse`. This includes q33-q35 (`refusal_by_design_note`), whose truth is `yes`/a taxon: the rubric scores PCT's refusal there as a refusal, **not** correct |
| `pct_deployed.parsed_goal_state` | the stored run-4 parser output (results/<d>/deployed_parses.json `parsed`): the goal state, or the parser's own typed failure (F4) with its reason and, where the model returned something, `raw` |
| `pct_deployed.answer` | the stored answer: a number, `yes`, a taxon, `derived: X`, or `refuse` |
| `pct_deployed.claim` | the stored claim (value/taxon, unit, rule ids, step count) |
| `pct_deployed.failure` | for a refusal: `type` (F1-F6), `stage` (`parser`, `resolution`, `engine`), `F5_subtype` (in-scope engine-stage F5 only: `F5a` = the gold goal state also fails, a KB gap; `F5b` = the gold goal state succeeds, parser-induced), `reason`, `named_missing_inputs` (the predicate the failure certificate names as underivable or cyclic), `failure_goal`, `failure_rule` |
| `pct_deployed.replay` | for an answer: the replay check (prove() re-run on the stored goal state; derivation checked step by step against the KB): `passed`, `problems`, `replay_value`, `rules`. `null` for a refusal |
| `pct_oracle` | the same for PCT run on the gold goal state: `answer`, `status`, `claim`, `failure` (`type` `-` = no gold goal state, the engine is not called), `replay` |
| `rag`, `raw` | the stored response text **verbatim** (`response`; raw = the unconstrained model), `extracted_answer` (the value / yes-no / taxon the locked scorer read, from its note `read X`; `null` when the scorer read nothing or the row went to manual review), `scorer_note` (the locked scorer's note), `no_response_cause` (only for "no response" rows), `transport_error_original` (rows re-sent after a transport error in the original run: that error; the response shown is the re-sent one, or the no-response record) |
| `verdicts.<system>` | per system (`pct_deployed`, `pct_oracle`, `rag`, `raw`): see below |

### `verdicts.<system>`

| field | meaning |
|---|---|
| `verdict` | `correct`, `incorrect`, `refusal`, `less specific` (biology: a true ancestor of the expected taxon), `over-specific` (biology: a descendant the query does not support), `no response` (generation exceeded 15 min or failed twice in transport: not correct, not answered) |
| `counts_as_correct` | true for `correct`, and for a `refusal` the rubric scores as correct (out of scope; biology negative is-a) |
| `answered` | true for `correct`, `incorrect`, `less specific`, `over-specific` (the conditional-accuracy and hallucination denominators) |
| `locked_scorer_outcome` | the locked scorer's (score.py) automatic outcome; `manual_review` where it deferred |
| `manual_verdict`, `manual_note` | the blind manual verdict that resolved a `manual_review` row (results/manual_review_*_final.jsonl via their key files), else `null` |
| `hallucination` | answered rows only (else `null`). PCT: the answer fails the replay check. RAG / raw (rubric §6): an in-scope answer verdict `incorrect` or `over-specific`, or any answered out-of-scope query |
| `faithfulness_error` | PCT only, answered rows: passes replay but is `incorrect`/`over-specific`/`less specific`, or answers an out-of-scope query. `null` for RAG / raw (not applicable) |
| `faithfulness_error_kinds` | PCT faithfulness errors: the parser-error kinds (dropped value, misassigned value, value bound that does not appear in the query, wrong predicate, ...); oracle: `gold goal state (not a parser error)` |

## Counts per file

| file | lines | in scope | out of scope | single | multi | underspecified |
|---|---|---|---|---|---|---|
| physics_queries.jsonl | 300 | 250 | 50 | 150 | 100 | 42 |
| chemistry_queries.jsonl | 300 | 250 | 50 | 150 | 100 | 54 |
| biology_queries.jsonl | 300 | 250 | 50 | 150 | 100 | 0 |

Verdicts per system (all 300 queries):

| file | system | correct | incorrect | refusal (correct) | refusal (not correct) | less specific | over-specific | no response | hallucinations | faithfulness errors |
|---|---|---|---|---|---|---|---|---|---|---|
| physics | pct_deployed | 89 | 10 | 50 | 151 | 0 | 0 | 0 | 0 | 10 |
| physics | pct_oracle | 200 | 0 | 50 | 50 | 0 | 0 | 0 | 0 | 0 |
| physics | rag | 51 | 190 | 41 | 17 | 0 | 0 | 1 | 190 | n/a |
| physics | raw | 51 | 236 | 1 | 12 | 0 | 0 | 0 | 236 | n/a |
| chemistry | pct_deployed | 48 | 11 | 49 | 192 | 0 | 0 | 0 | 0 | 11 |
| chemistry | pct_oracle | 196 | 0 | 50 | 54 | 0 | 0 | 0 | 0 | 0 |
| chemistry | rag | 48 | 199 | 35 | 13 | 0 | 0 | 5 | 199 | n/a |
| chemistry | raw | 36 | 252 | 0 | 4 | 0 | 0 | 8 | 252 | n/a |
| biology | pct_deployed | 118 | 0 | 69 | 98 | 15 | 0 | 0 | 0 | 15 |
| biology | pct_oracle | 230 | 0 | 67 | 3 | 0 | 0 | 0 | 0 | 0 |
| biology | rag | 72 | 25 | 47 | 137 | 6 | 13 | 0 | 38 | n/a |
| biology | raw | 139 | 96 | 0 | 43 | 8 | 13 | 1 | 109 | n/a |

## Check against results/paper_numbers.json

Every per-domain total in results/paper_numbers.json (sha256 `038a76468532e75adc7a1542ab16d748dfd6cd830f4d91c8b993ceeae82d8375`) that the per-query fields determine was rebuilt from these JSONL files alone and compared for exact equality: query and answered counts, correct answers and refusals, wrong / less specific / over-specific, unconditional and conditional accuracy, out-of-scope refusal rate, hallucination and faithfulness-error rates (n, d, percent and Wilson 95% interval), the PCT identity, refusals by failure type and stage, replay counts, faithfulness-error kinds per query, and the F5a/F5b split with ids.

| domain | values compared | mismatches |
|---|---|---|
| physics | 178 | 0 |
| chemistry | 183 | 0 |
| biology | 184 | 0 |

Not rebuilt (not determined by the exported fields): `baseline.hallucination_rate_bounds_pct`, `pct_deployed.answers_on_an_unstated_condition`, `rag.hallucination_rate_bounds_pct`. Full result: rebuild_check.json.

## SHA-256 of every source file read

| file | sha256 |
|---|---|
| docs/scoring_rubric.md | `85c6d729408fe1e689abbcdd26c602ef89d6211aac8c1deba7322ccd54e46320` |
| domains/biology/kb1_biology.json | `b67b31ec982cee0a616b2b110058d7092d497d4436625beafa80d68231c9c4af` |
| domains/biology/queries/natural_language.json | `86a23d5ff5896c8122bf191b91791df926cb4696f72836853999f28359c3883c` |
| domains/chemistry/kb1_chemistry.json | `6cb29290d2aaf95e999adb286b955591cdec540177889fe9831fdbcfe139cc66` |
| domains/chemistry/queries/natural_language.json | `44d49ab79c047334187adae3f02fbed34a39b7f7a1087af84632e8a78ea07217` |
| domains/physics/kb1_physics.json | `bcfc5cf8f9e857246658f88f6d679b40d848229ed43cc5de2d7c4cf984259cf5` |
| domains/physics/queries/queries_300.json | `be61d51bd742fa487348df60c032edd12914ec286ec9bf7d9db3318f8a6f4b2b` |
| pct/engine.py | `6059f55c53be64a7f6c70315ce5b0711afe11691f2e0f62f041873a935b0a4ee` |
| results/biology/baselines.json | `6e5f5ea7c3cd1bb8bac98a1d453880240626818fbf793af7cc4942098270a551` |
| results/biology/deployed_parses.json | `e505e802ebae05a16a621f2cf8c60f4df2886392459b1b0f1e631d07a8e44e89` |
| results/biology/pipeline.json | `62783684cb23cb386e4ede43f1e5569dfecc97ed00d9809a55f43e1998b19c70` |
| results/chemistry/baselines.json | `99c84598171c6bf9b28b8e34c98f6a450d59999fd41ba20603624b3072ef6960` |
| results/chemistry/deployed_parses.json | `3148bba23fe28e225657ca974b525b75766b6db48178d301874fccd874cafe6d` |
| results/chemistry/pipeline.json | `8a17e265cffdbcdf7ee898f36cdf916df69c3cf830fd71c4dd598b96d385b7f3` |
| results/paper_numbers.json | `038a76468532e75adc7a1542ab16d748dfd6cd830f4d91c8b993ceeae82d8375` |
| results/physics/deployed_parses.json | `00c839dc0b5be047b45ff96e7ba7aefe97ddb57fad6f219f3b206b0141ced54d` |
| results/physics/evaluation_300.json | `2eb71a3f7c52dd08c6b481ab870605a2de01ea99d18f363f7bcdd7e1b327d699` |
| results/physics/oracle_eval.json | `b0e08c7348f01f8e3b30508b3a1da64773babbbff4c3e045c5bbc5acc1af7b57` |
| results/physics/pipeline.json | `d9693716b03ea45542de5882465975d50603e8afc3629f65b32680cee96672fe` |
| results/scores.json | `493ab56dd27a510e51235aefb8ca2a1a27767cd3e8dbf2301c63da0b0da806ab` |
| results/underspecified.json | `b3eb4a3e7ee9c0ab69dc5a9f96e3345b86959b1b966d005b94c04ab0ad8dee73` |
| tools/analysis/score.py | `43fe64ef88565953d3c500048980e6b8b8c9f562ab1daed8d980bf168eba2de2` |
| tools/analysis/split_metrics.py | `39972dd9d914af32b9fcf0a48129f3450621eab7664002e182fbe93269cc9383` |
| tools/eval/eval_biology.py | `55b4ce09a18c69579cf656673f29eecb3f81cac974e20bd1af371e519f32c6dd` |
| tools/eval/eval_chemistry.py | `f36113790253b585de4492b33fcc6ed2f94e983e5366168a2143f13d6a916297` |
| tools/eval/eval_oracle_physics.py | `e15e2e2a5ec65fccd41ba241d23e42fa05fba121d9283bd68c99db428ece2340` |
