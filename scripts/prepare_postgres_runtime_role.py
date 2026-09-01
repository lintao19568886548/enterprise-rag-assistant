"""Create or rotate the least-privileged PostgreSQL application role.

Credentials are accepted only through environment variables so they do not
appear in process listings. The script never prints either connection string
or password.
"""

from __future__ import annotations

import os
import re

import psycopg
from psycopg import sql

ROLE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def main() -> int:
    owner_url = os.environ.get("POSTGRES_OWNER_URL")
    runtime_password = os.environ.get("POSTGRES_RUNTIME_PASSWORD")
    runtime_role = os.environ.get("POSTGRES_RUNTIME_ROLE", "knowledge_app")
    if not owner_url:
        raise SystemExit("POSTGRES_OWNER_URL is required")
    if not runtime_password or len(runtime_password) < 16:
        raise SystemExit("POSTGRES_RUNTIME_PASSWORD must contain at least 16 characters")
    if not ROLE_PATTERN.fullmatch(runtime_role):
        raise SystemExit("POSTGRES_RUNTIME_ROLE is invalid")

    role_identifier = sql.Identifier(runtime_role)
    with psycopg.connect(owner_url, autocommit=True) as connection:
        database_row = connection.execute("SELECT current_database()").fetchone()
        if database_row is None:
            raise RuntimeError("PostgreSQL did not return the current database")
        database_name = str(database_row[0])
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (runtime_role,),
        ).fetchone()
        if exists:
            connection.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    role_identifier,
                    sql.Literal(runtime_password),
                )
            )
        else:
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    role_identifier,
                    sql.Literal(runtime_password),
                )
            )
        connection.execute(
            sql.SQL(
                "ALTER ROLE {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
            ).format(role_identifier)
        )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database_name),
                role_identifier,
            )
        )
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role_identifier))
        connection.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
            ).format(role_identifier)
        )
        connection.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                role_identifier
            )
        )
        connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
            ).format(role_identifier)
        )
        connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT USAGE, SELECT ON SEQUENCES TO {}"
            ).format(role_identifier)
        )
    print(f"PostgreSQL runtime role prepared: role={runtime_role}, database={database_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
