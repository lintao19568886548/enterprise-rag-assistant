[CmdletBinding()]
param(
    [string]$ComposeFile = "compose.staging.yaml",
    [string]$EnvFile = ".env.staging",
    [string]$ProjectName = "enterprise-rag-staging",
    [int]$WaitTimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$compose = (Resolve-Path (Join-Path $repositoryRoot $ComposeFile)).Path
$environment = (Resolve-Path (Join-Path $repositoryRoot $EnvFile)).Path

docker compose --env-file $environment -p $ProjectName -f $compose config --quiet
if ($LASTEXITCODE -ne 0) { throw "Staging Compose configuration validation failed" }

docker compose --env-file $environment -p $ProjectName -f $compose up -d --build --wait --wait-timeout $WaitTimeoutSeconds
if ($LASTEXITCODE -ne 0) { throw "Staging stack did not become healthy" }

docker compose --env-file $environment -p $ProjectName -f $compose ps
Write-Output "Staging stack is healthy. Public entry points are configured by deploy/nginx/nginx.staging.conf."
