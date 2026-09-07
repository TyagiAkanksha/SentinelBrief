# Evaluation results

Published, versioned results of the SentinelBrief evaluation harness (PRD §7).

## Publication policy

- Only runs over **golden set v2** (real honeypot sessions, hand-labeled by the author) are
  published here. Golden set v1 is synthetic development data; its numbers are never published.
- Every v2 run is **appended** — including runs whose numbers got worse than the previous row.
  Rows are never edited or removed.
- Each row carries the date, git sha, prompt version, model ids, and every PRD §7.3 metric.
  The metric columns are fixed by `evals/scoring.py::COLUMNS` from M1 on; the LLM-as-judge and
  per-severity columns are added at M7 when they exist.

## Runs

| date | git_sha | prompt_version | models | n | failed | sev_exact | sev_±1 | category | esc_prec | esc_rec | critical_rec | cost_mean | cost_p95 | cost_total | lat_p50 | lat_p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

_No published runs yet — the first appears at M7._
