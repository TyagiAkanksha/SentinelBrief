# Evaluation results

Published, versioned results of the SentinelBrief evaluation harness (PRD §7).

## Publication policy

- Only runs over **golden set v2** (real honeypot sessions, hand-labeled by the author) are
  published here. Golden set v1 is synthetic development data; its numbers are never published.
- Every v2 run is **appended** — including runs whose numbers got worse than the previous row.
  Rows are never edited or removed.
- Each row carries the date, git sha, prompt version, model ids, and every PRD §7.3 metric.
  The metric columns are fixed by `evals/scoring.py::COLUMNS` from M1 on; the LLM-as-judge and
  per-severity/confusion-matrix columns are M7 additions.

## Runs

| date | git_sha | prompt_version | models | n | failed | sev_exact | sev_±1 | category | esc_prec | esc_rec | critical_rec | escalation_rate | cost_mean | cost_p95 | cost_total | lat_p50 | lat_p95 | sev4_rec | sev5_rec | sev_macro_f1 | judge_mean | judge_pct_le2 | injection_pass_rate | judge_cost_total_usd |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

_No published runs yet — the first appears at M7._
