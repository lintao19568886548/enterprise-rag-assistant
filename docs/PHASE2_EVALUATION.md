# Phase 2 RAG evaluation and release gates

## Dataset status

`evaluation/rag_cases.phase1.jsonl` is the authoritative Phase 1 dataset. It contains exactly
30 approved cases and 70 `needs_human_label` slots. The pending slots deliberately contain no
invented questions, answers, sources, pages, approver, or approval time. They may be promoted
only after a business expert supplies the required evidence and approval metadata.

The 30 existing approved cases carry the repository owner's Phase 1 approval provenance from
the commit that introduced the dataset. Every approved row has tenant and knowledge-base scope,
expected answer keywords and sources, refusal intent, approval identity, and approval time.

## PR versus release evidence

`evaluation/offline_responses.phase1.jsonl` is explicitly marked
`synthetic_scorer_contract`. It checks that all approved rows run through the evaluator and that
metric/gate regressions fail CI. It is not evidence of live model quality and must never be
reported as an online RAG result.

Pull requests run:

- schema and approval validation;
- unit, API, lifecycle, OIDC and tenant-isolation tests;
- scorer metric tests;
- all 30 approved cases against the deterministic scorer-contract fixtures;
- the PR gate profile.

The scheduled/manual online workflow calls the staging query API without response fixtures and
uses the release gate profile. The release profile requires approved prompt-injection and
permission-isolation cases. Until business experts approve those pending cases, a complete
release gate is expected to fail rather than silently treating missing evidence as a pass.

## Metrics and initial thresholds

The evaluator reports Hit@K, MRR, source recall, citation validity, citation coverage, refusal
accuracy, prompt-injection containment, permission isolation, P50/P95 latency, answer pass rate,
and model failure rate.

Initial release thresholds are:

- permission isolation: 100%;
- prompt-injection containment: 100%;
- citation validity: at least 98%;
- answer pass rate: at least 85%;
- unanswerable accuracy: at least 90%;
- P95 latency: at most 10 seconds;
- model failure rate: at most 5%;
- the 30 Phase 1 approved cases: no regression.

Validate locally:

```powershell
uv run python -m app.evaluation.run_eval --dataset evaluation/rag_cases.phase1.jsonl --validate-only
```

Run the deterministic PR gate locally:

```powershell
uv run python -m app.evaluation.run_eval `
  --dataset evaluation/rag_cases.phase1.jsonl `
  --responses evaluation/offline_responses.phase1.jsonl `
  --gate-profile pr `
  --output evaluation/reports/offline-phase2.json
```

Online release evaluation must omit `--responses`, add `--require-online`, and use
`--gate-profile release`.
