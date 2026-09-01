# Phase 2 security acceptance

Executed on 2026-09-01 with:

```text
58 passed in 29.60s
```

The focused suite includes API, identity, tenant ACL, OIDC, upload, lifecycle, Milvus filtering, RLS context, log redaction and SSE cleanup tests. Permission and prompt-injection tests passed 100% in this suite.

## Required checklist

| # | Control | Automated evidence | Result |
|---:|---|---|---|
| 1 | Cross-tenant knowledge-base read | `test_cross_tenant_knowledge_base_is_not_disclosed` | Pass |
| 2 | Cross-tenant knowledge-base write | `test_private_knowledge_base_grant_is_enforced_server_side`, PostgreSQL RLS test | Pass locally; real PostgreSQL run pending external URL |
| 3 | Cross-tenant document detail | `test_cross_tenant_document_task_write_and_sse_are_not_disclosed` | Pass |
| 4 | Cross-tenant task status | same API boundary test | Pass |
| 5 | Cross-tenant session history | `test_cross_tenant_history_is_not_disclosed` | Pass |
| 6 | Cross-tenant SSE | same API boundary test | Pass |
| 7 | Cross-tenant image | `test_cross_tenant_local_image_is_not_disclosed` | Pass |
| 8 | Cross-tenant Milvus retrieval | 100-query isolation test | Pass, zero leaks |
| 9 | Forged `tenant_id` | `test_forged_tenant_and_user_fields_are_ignored` | Pass |
| 10 | Forged `user_id` | same test | Pass |
| 11 | Expired JWT | parametrized OIDC claim test | Pass |
| 12 | Wrong audience | parametrized OIDC claim test | Pass |
| 13 | Wrong issuer | parametrized OIDC claim test | Pass |
| 14 | Wrong JWT signature | `test_wrong_oidc_signature_is_rejected` | Pass |
| 15 | Viewer writes | `test_viewer_cannot_write_and_editor_cannot_manage_tenant` | Pass, 403 |
| 16 | Editor manages tenant | same role-boundary test | Pass, 403 |
| 17 | User requests system prompt | final model-output disclosure guard test | Pass |
| 18 | Document says ignore instructions | untrusted-context prompt contract plus output guard | Pass |
| 19 | Document contains fake tool call | `test_prompt_or_tool_call_leak_is_blocked_after_model_generation` | Pass |
| 20 | Upload path traversal | upload metadata and lifecycle path tests | Pass |
| 21 | MIME/signature spoofing | upload MIME and fake-PDF tests | Pass |
| 22 | Repeated delete | lifecycle/API idempotency tests | Pass, same job ID |
| 23 | Worker crash during deletion | resumable cleanup stage test | Pass |
| 24 | Pool tenant context retention | fail-closed RLS context unit tests | Pass; real PostgreSQL test is present but skipped without owner/runtime URLs |
| 25 | Token/API key in logs | redaction test covers Authorization, Cookie, JWT, named keys and URL passwords | Pass |

## Defense details

- Tenant and user context comes from OIDC, a verified service account or development identity, never request JSON.
- PostgreSQL policies fail closed when session tenant context is missing and use transaction-local settings.
- Milvus searches require tenant, knowledge base and active-version predicates; vector batches reject mixed tenants.
- Model context and history are explicitly marked untrusted. A final output guard rejects internal-instruction leakage, credential-like output and fake tool/function calls even if a provider response is compromised.
- HTML workbench data is rendered with DOM `textContent`; dangerous actions name the target and require confirmation. External answer links are restricted to normalized HTTP/HTTPS URLs.
- Log records redact configured secrets and generic tokens; staging/production exception details are suppressed unless sensitive logging is explicitly enabled.

## External acceptance still required

The project includes real PostgreSQL owner/runtime RLS tests. They are intentionally skipped on this Windows host because PostgreSQL server/client and `TEST_POSTGRES_OWNER_URL` / `TEST_POSTGRES_RUNTIME_URL` are absent. No success was fabricated. These two tests must run in staging before release.
