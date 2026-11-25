# Balancing probe
Can the unmodified engine balance chemical equations, one conservation rule per coefficient (`kb_balancing.json`)?
H2 + O2 and CH4 + O2 derive from a single basis coefficient; NO + NO2 -> N2O3 refuses with F5 from every basis.
Run `python experiments/balancing/run_balancing.py`; `output.txt` is the stored result.
`analyse_sequential.py` checks, for 8 reactions, which fixed first coefficient lets the rest follow one equation at a time; result in `analysis.txt`.
