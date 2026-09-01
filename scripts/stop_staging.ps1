[CmdletBinding()]
param(
    [string]$ComposeFile = "compose.staging.yaml",
    [string]$EnvFile = ".env.staging",
    [string]$ProjectName = "enterprise-rag-staging",
    [switch]$RemoveContainers
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$compose = (Resolve-Path (Join-Path $repositoryRoot $ComposeFile)).Path
$environment = (Resolve-Path (Join-Path $repositoryRoot $EnvFile)).Path

if ($RemoveContainers) {
    docker compose --env-file $environment -p $ProjectName -f $compose down --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw "Failed to remove staging containers" }
    Write-Output "Staging containers were removed; named data volumes were preserved."
} else {
    docker compose --env-file $environment -p $ProjectName -f $compose stop
    if ($LASTEXITCODE -ne 0) { throw "Failed to stop staging services" }
    Write-Output "Staging services were stopped; containers and named data volumes were preserved."
}

Write-Output "This script never invokes docker compose down -v."
