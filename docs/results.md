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

_No published runs yet. The table header is fixed at M1; the first published row appears at M7,
after golden set v2 is labeled and the nightly gate's baselines are recorded from that run._
