# PostgreSQL RLS acceptance

## Design

Alembic head `d4e5f6a7b8c9` enables tenant Row Level Security for enterprise metadata. Every runtime
transaction installs `app.tenant_id`, `app.user_id`, `app.oidc_subject` and `app.oidc_issuer` with
transaction-local `set_config`. Missing context is fail-closed. Connection reuse resets at the
transaction boundary.

`scripts/prepare_postgres_runtime_role.py` creates/rotates the fixed application role with
`NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT` and `NOBYPASSRLS`, then grants only the
required schema/table/sequence privileges. Credentials come from environment variables and are
never logged.

## Automated real-PostgreSQL gate

The `postgres-rls` GitHub Actions job starts PostgreSQL 17, upgrades a blank database to head,
prepares the runtime role and runs `tests/integration/test_postgres_rls.py`. Tests assert:

- runtime role attributes do not bypass RLS;
- tenant A cannot read, update or delete tenant B rows;
- cross-tenant inserts are rejected;
- missing tenant context rejects read/write operations;
- connection-pool reuse does not retain the previous tenant;
- administrative changes produce audit evidence.

The job uses CI-only credentials and no production secrets.

## Current evidence

On 2026-09-01 the acceptance Compose environment started PostgreSQL 17 from an empty named
volume. The full Alembic chain completed at `d4e5f6a7b8c9 (head)`. The fresh-database run exposed
and fixed an ordering defect in `b2c3d4e5f6a7_add_enterprise_identity.py`: the default identity is
now seeded before its membership. The resulting database contained 18 public tables, 16 with RLS
enabled, and zero unvalidated foreign keys.

The runtime role reported:

`rolsuper=false`, `rolcreatedb=false`, `rolcreaterole=false`, `rolinherit=false`,
`rolbypassrls=false`.

`tests/integration/test_postgres_rls.py` then ran against the real owner/runtime roles in an
isolated Docker network and completed `4 passed`. The test proved cross-tenant select/update/
delete/insert denial, missing-context fail-closed behavior, least privilege, and clean context on
pool reuse. A later image-level `alembic current` again returned `d4e5f6a7b8c9 (head)`.

This is valid local PostgreSQL/RLS evidence. The same job must still pass in CI and on the target
staging database before production promotion.

## Operations

Use the migration owner only for Alembic and role/grant operations. APIs/workers use the runtime
URL. Before promotion, verify `rolsuper=false` and `rolbypassrls=false`, run the two-tenant suite,
check orphan/FK queries, and record output without credentials. Rehearse downgrade/upgrade on an
isolated copy; do not downgrade live data without a reviewed rollback decision.
