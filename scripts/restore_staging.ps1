[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [string]$ComposeFile = "compose.staging.yaml",
    [string]$RecoveryProjectName = "enterprise-rag-recovery",
    [switch]$ConfirmRestore
)

$ErrorActionPreference = "Stop"
if (-not $ConfirmRestore) { throw "Pass -ConfirmRestore after verifying the target is a disposable recovery stack" }
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backup = (Resolve-Path -LiteralPath $BackupPath).Path
if (-not (Test-Path -LiteralPath (Join-Path $backup "manifest.json"))) { throw "Backup manifest is missing" }
if ($RecoveryProjectName -eq "enterprise-rag-staging") { throw "Recovery must use an isolated project name" }

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
    Get-Content -LiteralPath $archive -AsByteStream -ReadCount 0 |
        docker run --rm -i -v "${dockerVolume}:/restore" alpine:3.21 tar -C /restore -xzf -
}

$compose = Join-Path $repositoryRoot $ComposeFile
docker compose -p $RecoveryProjectName -f $compose up -d postgres redis minio etcd milvus
docker compose -p $RecoveryProjectName -f $compose exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB"; createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
Get-Content -LiteralPath (Join-Path $backup "postgres.dump") -AsByteStream -ReadCount 0 |
    docker compose -p $RecoveryProjectName -f $compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges'
docker compose -p $RecoveryProjectName -f $compose run --rm migrate
Write-Output "Isolated recovery stack restored. Run docs/DISASTER_RECOVERY.md consistency checks before promotion."
