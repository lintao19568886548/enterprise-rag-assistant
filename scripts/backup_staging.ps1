[CmdletBinding()]
param(
    [string]$ComposeFile = "compose.staging.yaml",
    [string]$BackupRoot = "backups/staging",
    [string]$ProjectName = "enterprise-rag-staging"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedBackupRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot $BackupRoot))
if (-not $resolvedBackupRoot.StartsWith([IO.Path]::GetFullPath($repositoryRoot), [StringComparison]::OrdinalIgnoreCase)) {
    throw "BackupRoot must remain inside the repository"
}
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$target = Join-Path $resolvedBackupRoot $stamp
if (Test-Path -LiteralPath $target) { throw "Refusing to overwrite an existing backup" }
New-Item -ItemType Directory -Path $target | Out-Null

$compose = Join-Path $repositoryRoot $ComposeFile
docker compose -p $ProjectName -f $compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' |
    Set-Content -LiteralPath (Join-Path $target "postgres.dump") -AsByteStream

$volumeMap = @{
    "minio-data" = "minio-data.tar.gz"
    "milvus-data" = "milvus-data.tar.gz"
    "etcd-data" = "etcd-data.tar.gz"
    "app-output" = "app-output.tar.gz"
}
foreach ($volumeName in $volumeMap.Keys) {
    $dockerVolume = "${ProjectName}_${volumeName}"
    docker run --rm -v "${dockerVolume}:/source:ro" alpine:3.21 tar -C /source -czf - . |
        Set-Content -LiteralPath (Join-Path $target $volumeMap[$volumeName]) -AsByteStream
}

$structure = Join-Path $target "configuration-structure"
New-Item -ItemType Directory -Path $structure | Out-Null
Copy-Item -LiteralPath $compose -Destination $structure
Copy-Item -LiteralPath (Join-Path $repositoryRoot ".env.staging.example") -Destination $structure
Copy-Item -LiteralPath (Join-Path $repositoryRoot "alembic.ini") -Destination $structure
Copy-Item -LiteralPath (Join-Path $repositoryRoot "deploy") -Destination $structure -Recurse
Get-ChildItem -LiteralPath $target -File | Get-FileHash -Algorithm SHA256 |
    Select-Object Path, Hash | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $target "sha256.json") -Encoding utf8
@{
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    rpo_target_minutes = 15
    project = $ProjectName
    contains_secrets = $false
    milvus_strategy = "filesystem snapshot plus SQL chunk metadata rebuild path"
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $target "manifest.json") -Encoding utf8
Write-Output "Backup completed: $target"
