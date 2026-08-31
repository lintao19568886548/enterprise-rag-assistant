# RAG evaluation and release gates

## Authoritative dataset

`evaluation/rag_cases.phase1.jsonl` remains the authoritative, versioned dataset. It currently
contains 100 records: 30 approved cases and 70 `needs_human_label` records. Phase 3 does not
invent questions, expected answers, citations, or approvals for the pending records.

The business-expert package is available in both Excel and UTF-8 CSV form:

- `outputs/phase3_evaluation/business_expert_annotation.xlsx`
- `outputs/phase3_evaluation/business_expert_annotation.csv`

The workbook contains instructions, the 70 editable pending records, field definitions, data
validation, and formula-driven approval counts. `case_id` and `category` are read-only identity
fields. Multi-value fields use `|` as the delimiter.

## Expert labeling workflow

1. A domain expert fills only the `待标注用例` sheet.
2. The expert records the real question, reference material, answer points, allowed citations,
   tenant/knowledge-base scope, refusal intent, and security scenario where applicable.
3. Until review is complete, `approval_status` remains `needs_human_label` and approval identity
   fields stay empty.
4. After expert review, set `approval_status=approved`, identify the reviewer, and enter an
   ISO-8601 timestamp including the timezone.
5. Save the edited table as UTF-8 CSV without renaming or deleting columns.
6. Run a non-writing validation first:

   ```powershell
   uv run python scripts/import_evaluation_labels.py path\to\expert-labels.csv
   ```

7. Review the summary. Only then apply the validated file:

   ```powershell
   uv run python scripts/import_evaluation_labels.py path\to\expert-labels.csv --apply
   ```

The apply operation creates a timestamped copy under `backups/` and atomically replaces the
JSONL only after every row and the complete dataset pass validation. Existing approved cases
cannot be modified through this importer.

## Validation rules

Approved rows require a question, source/reference context, citation requirements, tenant and
knowledge-base scope, explicit refusal intent, reviewer identity, and timezone-aware review time.
Answerable rows additionally require answer keywords and expected sources. Prompt-injection and
permission-isolation rows require their corresponding threat/scope descriptions.

The dataset validator rejects duplicate IDs, malformed approval provenance, approval metadata on
pending rows, missing tenant scope, invalid list fields, and invalid timestamps.

## Metrics

The evaluator emits machine-readable JSON and a Markdown companion report. Metrics include:

- answer correctness and abstention accuracy;
- faithfulness proxy and citation validity;
- citation precision, citation recall, and retrieval recall;
- permission isolation/leakage;
- prompt-injection resistance;
- image-citation correctness;
- wall, local-service, and model latency when reported by the service;
- input/output/total tokens and cost when reported by the provider.

The current deterministic response fixture is marked `synthetic_scorer_contract`. It verifies
scorer and gate behavior only and is not evidence of live-model quality.

## PR and release gates

The PR gate requires at least the 30 existing approved cases and all existing deterministic
quality/security tests. The current PR scorer-contract gate passes 30/30.

The release gate requires all 100 cases to have real expert approval plus approved coverage for
permission isolation, prompt injection, unanswerable questions, bad citations, and table/image
citations. It also enforces quality, latency, model-failure, and security thresholds. At present
the release gate intentionally fails: only 30 records are approved, and approved security/image
categories are incomplete. This blocked state must not be bypassed by lowering thresholds.

Run the local checks:

```powershell
uv run python -m app.evaluation.run_eval `
  --dataset evaluation/rag_cases.phase1.jsonl `
  --responses evaluation/offline_responses.phase1.jsonl `
  --gate-profile pr `
  --output evaluation/reports/phase3-pr-gate.json

uv run python -m app.evaluation.run_eval `
  --dataset evaluation/rag_cases.phase1.jsonl `
  --responses evaluation/offline_responses.phase1.jsonl `
  --gate-profile release `
  --output evaluation/reports/phase3-release-gate.json
```

Online staging/release evaluation must omit `--responses`, add `--require-online`, and use real
approved labels. It cannot be claimed from the offline scorer-contract fixture.
