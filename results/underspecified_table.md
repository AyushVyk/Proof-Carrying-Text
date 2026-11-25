# Underspecified in-scope queries: additional scoring layer

Beside, not replacing, the locked rubric (docs/scoring_rubric.md, score.py). Definition and rules: tools/analysis/underspecified.py. (CI a-b) = Wilson 95%.

## physics

Underspecified in-scope queries: 42 {'initial_velocity': 12, 'net_force': 4, 'applied_force_angle': 2, 'pendulum_amplitude': 7, 'net_work': 1, 'nonconservative_work': 7, 'net_impulse': 3, 'force_displacement_angle': 6}; answerable in-scope: 208.

| Metric | PCT dep. | PCT oracle | RAG | Raw |
|---|---|---|---|---|
| **Locked rubric, all in-scope** (score.py + manual verdicts) | | | | |
| answered (in scope) | 99 | 200 | 232 | 238 |
| correct (in scope) | 89 | 200 | 51 | 51 |
| unconditional accuracy | 35.6% | 80.0% | 20.4% | 20.4% |
| conditional accuracy | 89.9% | 100.0% | 22.0% | 21.4% |
| **Answerable in-scope only** (locked per-row outcomes) | | | | |
| in-scope queries | 208 | 208 | 208 | 208 |
| answered (in scope) | 96 | 200 | 199 | 200 |
| less specific / over-specific | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| manual review | 0 | 0 | 0 | 0 |
| no response (generation exceeded 15 min; not correct, not answered) | 0 | 0 | 1 | 0 |
| correct (in scope) | 89 | 200 | 44 | 42 |
| unconditional accuracy | 42.8% (89/208) (CI 36.3-49.6) | 96.2% (200/208) (CI 92.6-98.0) | 21.2% (44/208) (CI 16.2-27.2) | 20.2% (42/208) (CI 15.3-26.2) |
| conditional accuracy | 92.7% (89/96) (CI 85.7-96.4) | 100.0% (200/200) (CI 98.1-100.0) | 22.1% (44/199) (CI 16.9-28.4) | 21.0% (42/200) (CI 15.9-27.2) |
| out-of-scope refusal rate | 100.0% (50/50) (CI 92.9-100.0) | 100.0% (50/50) (CI 92.9-100.0) | 82.0% (41/50) (CI 69.2-90.2) | 2.0% (1/50) (CI 0.4-10.5) |
| hallucination rate | 0.0% (0/96) (CI 0.0-3.8) | 0.0% (0/200) (CI 0.0-1.9) | 78.8% (164/208) (CI 72.8-83.8) | 83.1% (207/249) (CI 78.0-87.3) |
| faithfulness error rate | 7.3% (7/96) (CI 3.6-14.3) | 0.0% (0/200) (CI 0.0-1.9) | - | - |
| **Underspecified block** (expected: refusal) | | | | |
| n | 42 | 42 | 42 | 42 |
| correct refusals | 14 | 35 | 9 | 4 |
| refusals naming another cause | 25 | 7 | - | - |
| answers | 3 | 0 | 33 | 38 |
| no response (generation exceeded 15 min) | 0 | 0 | 0 | 0 |
| hallucinations (RAG/raw: every answer; PCT: replay fails) | 0 | 0 | 33 | 38 |
| manual review | 0 | 0 | 0 | 0 |
| correct-refusal rate | 33.3% (14/42) (CI 21.0-48.4) | 83.3% (35/42) (CI 69.4-91.7) | 21.4% (9/42) (CI 11.7-35.9) | 9.5% (4/42) (CI 3.8-22.1) |
| hallucination rate | 0.0% (0/42) (CI 0.0-8.4) | 0.0% (0/42) (CI 0.0-8.4) | 78.6% (33/42) (CI 64.1-88.3) | 90.5% (38/42) (CI 77.9-96.2) |

## chemistry

Underspecified in-scope queries: 54 {'other_reactants': 39, 'gas_conditions': 17}; answerable in-scope: 196.

| Metric | PCT dep. | PCT oracle | RAG | Raw |
|---|---|---|---|---|
| **Locked rubric, all in-scope** (score.py + manual verdicts) | | | | |
| answered (in scope) | 58 | 196 | 232 | 238 |
| correct (in scope) | 48 | 196 | 48 | 36 |
| unconditional accuracy | 19.2% | 78.4% | 19.2% | 14.4% |
| conditional accuracy | 82.8% | 100.0% | 20.7% | 15.1% |
| **Answerable in-scope only** (locked per-row outcomes) | | | | |
| in-scope queries | 196 | 196 | 196 | 196 |
| answered (in scope) | 57 | 196 | 186 | 188 |
| less specific / over-specific | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| manual review | 0 | 0 | 0 | 0 |
| no response (generation exceeded 15 min; not correct, not answered) | 0 | 0 | 3 | 4 |
| correct (in scope) | 48 | 196 | 46 | 32 |
| unconditional accuracy | 24.5% (48/196) (CI 19.0-31.0) | 100.0% (196/196) (CI 98.1-100.0) | 23.5% (46/196) (CI 18.1-29.9) | 16.3% (32/196) (CI 11.8-22.1) |
| conditional accuracy | 84.2% (48/57) (CI 72.6-91.5) | 100.0% (196/196) (CI 98.1-100.0) | 24.7% (46/186) (CI 19.1-31.4) | 17.0% (32/188) (CI 12.3-23.0) |
| out-of-scope refusal rate | 98.0% (49/50) (CI 89.5-99.6) | 100.0% (50/50) (CI 92.9-100.0) | 70.0% (35/50) (CI 56.2-80.9) | 0.0% (0/50) (CI 0.0-7.1) |
| hallucination rate | 0.0% (0/58) (CI 0.0-6.2) | 0.0% (0/196) (CI 0.0-1.9) | 77.1% (155/201) (CI 70.8-82.4) | 86.6% (206/238) (CI 81.6-90.3) |
| faithfulness error rate | 17.2% (10/58) (CI 9.6-28.9) | 0.0% (0/196) (CI 0.0-1.9) | - | - |
| **Underspecified block** (expected: refusal) | | | | |
| n | 54 | 54 | 54 | 54 |
| correct refusals | 19 | 54 | 8 | 14 |
| refusals naming another cause | 34 | 0 | - | - |
| answers | 1 | 0 | 44 | 36 |
| no response (generation exceeded 15 min) | 0 | 0 | 2 | 4 |
| hallucinations (RAG/raw: every answer; PCT: replay fails) | 0 | 0 | 44 | 36 |
| manual review | 0 | 0 | 0 | 0 |
| correct-refusal rate | 35.2% (19/54) (CI 23.8-48.5) | 100.0% (54/54) (CI 93.4-100.0) | 14.8% (8/54) (CI 7.7-26.6) | 25.9% (14/54) (CI 16.1-38.9) |
| hallucination rate | 0.0% (0/54) (CI 0.0-6.6) | 0.0% (0/54) (CI 0.0-6.6) | 81.5% (44/54) (CI 69.2-89.6) | 66.7% (36/54) (CI 53.4-77.8) |

## biology

Underspecified in-scope queries: 0 ; answerable in-scope: 250.

| Metric | PCT dep. | PCT oracle | RAG | Raw |
|---|---|---|---|---|
| **Locked rubric, all in-scope** (score.py + manual verdicts) | | | | |
| answered (in scope) | 133 | 230 | 113 | 206 |
| correct (in scope) | 137 | 247 | 72 | 139 |
| unconditional accuracy | 54.8% | 98.8% | 28.8% | 55.6% |
| conditional accuracy | 88.7% | 100.0% | 63.7% | 67.5% |
| **Answerable in-scope only** (locked per-row outcomes) | | | | |
| in-scope queries | 250 | 250 | 250 | 250 |
| answered (in scope) | 133 | 230 | 113 | 206 |
| less specific / over-specific | 15 / 0 | 0 / 0 | 6 / 13 | 8 / 13 |
| manual review | 0 | 0 | 0 | 0 |
| no response (generation exceeded 15 min; not correct, not answered) | 0 | 0 | 0 | 1 |
| correct (in scope) | 137 | 247 | 72 | 139 |
| unconditional accuracy | 54.8% (137/250) (CI 48.6-60.9) | 98.8% (247/250) (CI 96.5-99.6) | 28.8% (72/250) (CI 23.5-34.7) | 55.6% (139/250) (CI 49.4-61.6) |
| conditional accuracy | 88.7% (118/133) (CI 82.2-93.0) | 100.0% (230/230) (CI 98.4-100.0) | 63.7% (72/113) (CI 54.5-72.0) | 67.5% (139/206) (CI 60.8-73.5) |
| out-of-scope refusal rate | 100.0% (50/50) (CI 92.9-100.0) | 100.0% (50/50) (CI 92.9-100.0) | 94.0% (47/50) (CI 83.8-97.9) | 0.0% (0/50) (CI 0.0-7.1) |
| hallucination rate | 0.0% (0/133) (CI 0.0-2.8) | 0.0% (0/230) (CI 0.0-1.6) | 32.8% (38/116) (CI 24.9-41.7) | 42.6% (109/256) (CI 36.7-48.7) |
| faithfulness error rate | 0.0% (0/133) (CI 0.0-2.8) | 0.0% (0/230) (CI 0.0-1.6) | - | - |
| **Underspecified block** (expected: refusal) | | | | |
| n | 0 | 0 | 0 | 0 |
| correct refusals | 0 | 0 | 0 | 0 |
| refusals naming another cause | 0 | 0 | - | - |
| answers | 0 | 0 | 0 | 0 |
| no response (generation exceeded 15 min) | 0 | 0 | 0 | 0 |
| hallucinations (RAG/raw: every answer; PCT: replay fails) | 0 | 0 | 0 | 0 |
| manual review | 0 | 0 | 0 | 0 |
| correct-refusal rate | - | - | - | - |
| hallucination rate | - | - | - | - |

