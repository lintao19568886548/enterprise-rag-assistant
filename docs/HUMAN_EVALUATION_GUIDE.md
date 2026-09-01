# Human evaluation guide

## Non-negotiable rule

The 70 pending rows require a person who understands the enterprise source material and access
policy. AI and automation may validate schema and score responses, but must not invent questions,
answers, source pages, permissions, reviewer identity, timestamps or approval decisions.

The authoritative dataset is `evaluation/rag_cases.phase1.jsonl`. The editable expert packages are:

- `outputs/phase3_evaluation/business_expert_annotation.xlsx`
- `outputs/phase3_evaluation/business_expert_annotation.csv`

## What the expert must provide

For each pending `case_id`, preserve the ID and category and complete the real question,
reference material, expected answer/keywords, source/document/page/chunk expectations,
tenant/knowledge-base/user scope, refusal behavior, citation requirements and reviewer comment.
Security cases also require the permission scope or injection type. Image/table cases require the
actual image/table citation expectation.

Keep `approval_status=needs_human_label` while drafting. Only an authorized reviewer may change it
to `approved`, populate `approved_by`, and provide a timezone-aware ISO-8601 `approved_at` value.
Multi-value fields use `|` as their delimiter. Do not alter or delete the 30 existing approved
records through the import file.

The final approved set must cover, with real evidence:

- ordinary fact and unanswerable questions;
- tenant and knowledge-base permission isolation;
- prompt-injection containment;
- invalid or missing citations;
- table and image citation correctness.

## Import workflow

Export the completed worksheet as UTF-8 CSV without renaming columns. Run the default dry-run
first; it performs no write:

```powershell
uv run python scripts/import_evaluation_labels.py path\to\expert-labels.csv
```

Resolve every validation error and have a second person review the diff. Then apply the validated
file:

```powershell
uv run python scripts/import_evaluation_labels.py path\to\expert-labels.csv --apply
```

The importer validates the entire result, protects existing approvals, creates a timestamped
backup and atomically replaces the JSONL only after validation succeeds.

## Gates after import

First validate labels and run the deterministic PR control:

```powershell
uv run python -m app.evaluation.run_eval `
  --dataset evaluation/rag_cases.phase1.jsonl `
  --validate-only

uv run python -m app.evaluation.run_eval `
  --dataset evaluation/rag_cases.phase1.jsonl `
  --responses evaluation/offline_responses.phase1.jsonl `
  --gate-profile pr `
  --output evaluation/reports/pre-merge-pr-gate.json
```

The production decision requires an online run against the approved staging service; omit
`--responses` and require online execution:

```powershell
uv run python -m app.evaluation.run_eval `
  --base-url https://staging.example.internal/query `
  --dataset evaluation/rag_cases.phase1.jsonl `
  --require-online `
  --gate-profile release `
  --output evaluation/reports/pre-release-online-gate.json
```

Use the real internal staging URL and provide authentication through the approved local/CI secret
mechanism, never on the command line. A failed release gate must be fixed at the data, retrieval,
authorization or model layer; thresholds and required categories must not be weakened.

## Reviewer sign-off

The business owner signs the label content, the security owner signs isolation/injection cases,
and the release owner signs the final online report. Archive the immutable dataset hash, report,
review identities and timestamp with the change record. Approval of evaluation data does not by
itself authorize PR merge or production release.
