from app.db.session import apply_postgres_rls_context


class _Dialect:
    name = "postgresql"


class _Connection:
    dialect = _Dialect()

    def __init__(self):
        self.calls = []

    def execute(self, statement, parameters):
        self.calls.append((str(statement), parameters))


def test_rls_context_uses_transaction_local_parameterized_settings():
    connection = _Connection()
    context = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "oidc_subject": "subject-a",
        "oidc_issuer": "https://id.example.com",
    }
    apply_postgres_rls_context(connection, context)

    assert len(connection.calls) == 4
    names = {parameters["setting_name"] for _, parameters in connection.calls}
    assert names == {"app.tenant_id", "app.user_id", "app.oidc_subject", "app.oidc_issuer"}
    for statement, parameters in connection.calls:
        assert "set_config" in statement
        assert parameters["setting_value"] in context.values()
        assert parameters["setting_value"] not in statement


def test_rls_context_fails_closed_when_identity_is_missing():
    connection = _Connection()
    apply_postgres_rls_context(connection, {})
    assert all(parameters["setting_value"] == "" for _, parameters in connection.calls)
