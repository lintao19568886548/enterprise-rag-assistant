# Staging deployment

`compose.staging.yaml` is the production-shaped staging topology. Only Nginx publishes host
ports. PostgreSQL, Redis, Milvus, etcd, and MinIO stay on an internal Docker network. Query,
import, and MinIO traffic enter through TLS on ports 8443, 8444, and 9443 respectively.

## Required configuration

Copy `.env.staging.example` to the ignored `.env.staging` file or inject equivalent values from
your secret manager. Never commit that file. Use separate PostgreSQL owner and runtime
passwords; application services connect as the non-superuser `knowledge_app`, while only the
one-shot migration job uses the owner account.

The repository does not contain a TLS private key. Put a staging certificate at:

- `deploy/nginx/certs/staging.crt`
- `deploy/nginx/certs/staging.key`

Use a certificate issued by the enterprise CA when available. A self-signed certificate is
acceptable only for an isolated developer staging machine.

Load environment variables in PowerShell without printing their values, then validate:

```powershell
Get-Content .env.staging | ForEach-Object {
    if ($_ -and -not $_.StartsWith('#')) {
        $name, $value = $_ -split '=', 2
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}
docker compose -f compose.staging.yaml config --quiet
```

Start and verify:

```powershell
docker compose -f compose.staging.yaml up -d --build
docker compose -f compose.staging.yaml ps
curl.exe -k https://127.0.0.1:8443/health/ready
curl.exe -k https://127.0.0.1:8444/health/ready
```

The import, cleanup, and evaluation workers consume independent `import`, `cleanup`, and
`evaluation` queues. Workers use late acknowledgement, one-message prefetch, bounded child
lifetime, graceful shutdown, persisted Redis broker data, and SQL-backed failed/dead-letter
state for cleanup jobs.

Do not use `docker compose down -v` for this environment. Normal shutdown is:

```powershell
docker compose -f compose.staging.yaml stop
```

The current Windows host had no Docker CLI during Phase 2 implementation, so only YAML and
policy-level static validation could be executed locally. Real image build, `docker compose
config`, health checks, and staging startup remain mandatory on a Docker-capable host before
release.
