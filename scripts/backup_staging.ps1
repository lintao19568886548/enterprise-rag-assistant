[CmdletBinding()]
param(
    [string]$ComposeFile = "compose.staging.yaml",
    [string]$EnvFile = ".env.staging",
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
$environment = (Resolve-Path (Join-Path $repositoryRoot $EnvFile)).Path
$databaseContainer = (docker compose --env-file $environment -p $ProjectName -f $compose ps -q postgres).Trim()
if (-not $databaseContainer) { throw "PostgreSQL container is not running" }
$containerDump = "/tmp/enterprise-rag-backup-$stamp.dump"
$dumpCommand = 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges > ' + $containerDump
try {
    docker compose --env-file $environment -p $ProjectName -f $compose exec -T postgres sh -c $dumpCommand
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL backup failed" }
    docker cp "${databaseContainer}:$containerDump" (Join-Path $target "postgres.dump")
    if ($LASTEXITCODE -ne 0) { throw "Copying the PostgreSQL backup failed" }
}
finally {
    docker exec $databaseContainer rm -f $containerDump 2>$null | Out-Null
}

$volumeMap = @{
    "minio-data" = "minio-data.tar.gz"
    "milvus-data" = "milvus-data.tar.gz"
    "etcd-data" = "etcd-data.tar.gz"
    "app-output" = "app-output.tar.gz"
}
foreach ($volumeName in $volumeMap.Keys) {
    $dockerVolume = "${ProjectName}_${volumeName}"
    $temporaryContainer = "${ProjectName}-backup-${stamp}-$($volumeName -replace '[^a-zA-Z0-9_.-]', '-')"
    try {
        docker run --name $temporaryContainer -v "${dockerVolume}:/source:ro" alpine:3.21 `
            tar -C /source -czf /tmp/volume.tar.gz .
        if ($LASTEXITCODE -ne 0) { throw "Volume backup failed: $volumeName" }
        docker cp "${temporaryContainer}:/tmp/volume.tar.gz" (Join-Path $target $volumeMap[$volumeName])
        if ($LASTEXITCODE -ne 0) { throw "Copying the volume backup failed: $volumeName" }
    }
    finally {
        docker rm -f $temporaryContainer 2>$null | Out-Null
    }
}

$structure = Join-Path $target "configuration-structure"
New-Item -ItemType Directory -Path $structure | Out-Null
Copy-Item -LiteralPath $compose -Destination $structure
Copy-Item -LiteralPath (Join-Path $repositoryRoot ".env.staging.example") -Destination $structure
Copy-Item -LiteralPath (Join-Path $repositoryRoot "alembic.ini") -Destination $structure
Copy-Item -LiteralPath (Join-Path $repositoryRoot "deploy") -Destination $structure -Recurse
Get-ChildItem -LiteralPath $target -File | Get-FileHash -Algorithm SHA256 |
    Select-Object Path, Hash | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $target "sha256.json") -Encoding utf8
$migrationImageRef = "${ProjectName}-migrate:latest"
$migrationImageId = docker image inspect $migrationImageRef --format '{{.Id}}' 2>$null
if ($LASTEXITCODE -ne 0) { $migrationImageId = $null }
@{
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    rpo_target_minutes = 15
    project = $ProjectName
    contains_secrets = $false
    migration_image_id = $migrationImageId
    migration_image_ref = $migrationImageRef
    milvus_strategy = "filesystem snapshot plus SQL chunk metadata rebuild path"
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $target "manifest.json") -Encoding utf8
Write-Output "Backup completed: $target"
