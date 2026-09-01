[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [string]$ComposeFile = "compose.staging.yaml",
    [string]$EnvFile = ".env.staging",
    [string]$RecoveryProjectName = "enterprise-rag-recovery",
    [switch]$ConfirmRestore
)

$ErrorActionPreference = "Stop"
if (-not $ConfirmRestore) { throw "Pass -ConfirmRestore after verifying the target is a disposable recovery stack" }
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backup = (Resolve-Path -LiteralPath $BackupPath).Path
if (-not (Test-Path -LiteralPath (Join-Path $backup "manifest.json"))) { throw "Backup manifest is missing" }
if ($RecoveryProjectName -eq "enterprise-rag-staging") { throw "Recovery must use an isolated project name" }
$compose = Join-Path $repositoryRoot $ComposeFile
$environment = (Resolve-Path (Join-Path $repositoryRoot $EnvFile)).Path
$existingContainers = @(docker ps -a --filter "label=com.docker.compose.project=$RecoveryProjectName" -q)
$existingVolumes = @(docker volume ls --format '{{.Name}}' | Where-Object { $_ -like "${RecoveryProjectName}_*" })
if ($existingContainers.Count -gt 0 -or $existingVolumes.Count -gt 0) {
    throw "Recovery project already has containers or volumes; choose a new disposable RecoveryProjectName"
}
$hashManifest = Join-Path $backup "sha256.json"
if (-not (Test-Path -LiteralPath $hashManifest)) { throw "Backup hash manifest is missing" }
foreach ($entry in @((Get-Content -LiteralPath $hashManifest -Raw | ConvertFrom-Json))) {
    $candidate = Join-Path $backup (Split-Path -Leaf $entry.Path)
    if (-not (Test-Path -LiteralPath $candidate)) { throw "Hashed backup file is missing: $candidate" }
    $actual = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash
    if ($actual -ne $entry.Hash) { throw "Backup hash mismatch: $candidate" }
}

$volumeMap = @{
    "minio-data" = "minio-data.tar.gz"
    "milvus-data" = "milvus-data.tar.gz"
    "etcd-data" = "etcd-data.tar.gz"
    "app-output" = "app-output.tar.gz"
}
foreach ($volumeName in $volumeMap.Keys) {
    $archive = Join-Path $backup $volumeMap[$volumeName]
    if (-not (Test-Path -LiteralPath $archive)) { throw "Missing archive: $($volumeMap[$volumeName])" }
    $dockerVolume = "${RecoveryProjectName}_${volumeName}"
    docker volume create $dockerVolume | Out-Null
    $temporaryContainer = "${RecoveryProjectName}-restore-$($volumeName -replace '[^a-zA-Z0-9_.-]', '-')"
    try {
        docker create --name $temporaryContainer -v "${dockerVolume}:/restore" alpine:3.21 `
            tar -C /restore -xzf /tmp/volume.tar.gz | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Creating the volume restore container failed: $volumeName" }
        docker cp $archive "${temporaryContainer}:/tmp/volume.tar.gz"
        if ($LASTEXITCODE -ne 0) { throw "Copying the volume backup failed: $volumeName" }
        docker start -a $temporaryContainer
        if ($LASTEXITCODE -ne 0) { throw "Volume restore failed: $volumeName" }
    }
    finally {
        docker rm -f $temporaryContainer 2>$null | Out-Null
    }
}

docker compose --env-file $environment -p $RecoveryProjectName -f $compose up -d postgres redis minio etcd milvus
if ($LASTEXITCODE -ne 0) { throw "Recovery infrastructure failed to start" }
docker compose --env-file $environment -p $RecoveryProjectName -f $compose exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB"; createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
if ($LASTEXITCODE -ne 0) { throw "Recovery database reset failed" }
$databaseContainer = (docker compose --env-file $environment -p $RecoveryProjectName -f $compose ps -q postgres).Trim()
if (-not $databaseContainer) { throw "Recovery PostgreSQL container is not running" }
$containerDump = "/tmp/enterprise-rag-recovery.dump"
try {
    docker cp (Join-Path $backup "postgres.dump") "${databaseContainer}:$containerDump"
    if ($LASTEXITCODE -ne 0) { throw "Copying the PostgreSQL backup into recovery failed" }
    docker compose --env-file $environment -p $RecoveryProjectName -f $compose exec -T postgres sh -c `
        'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges /tmp/enterprise-rag-recovery.dump'
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL restore failed" }
}
finally {
    docker exec $databaseContainer rm -f $containerDump 2>$null | Out-Null
}
$backupManifest = Get-Content -LiteralPath (Join-Path $backup "manifest.json") -Raw | ConvertFrom-Json
$sourceImage = $backupManifest.migration_image_id
if (-not $sourceImage) { $sourceImage = "$($backupManifest.project)-migrate:latest" }
$recoveryImage = "${RecoveryProjectName}-migrate:latest"
docker image inspect $sourceImage *> $null
if ($LASTEXITCODE -eq 0) {
    docker tag $sourceImage $recoveryImage
    if ($LASTEXITCODE -ne 0) { throw "Tagging the recorded source migration image failed" }
    docker compose --env-file $environment -p $RecoveryProjectName -f $compose run --rm migrate
}
else {
    docker compose --env-file $environment -p $RecoveryProjectName -f $compose run --rm migrate
}
if ($LASTEXITCODE -ne 0) { throw "Recovery migration failed" }
Write-Output "Isolated recovery stack restored. Run docs/DISASTER_RECOVERY.md consistency checks before promotion."
