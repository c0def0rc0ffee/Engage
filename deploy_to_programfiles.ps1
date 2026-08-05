# Copies the built add-on into the local Kodi install.
# Deploys from Engage App\service.engage (the mirror of the Dist zip
# contents), so run build-zip.ps1 first. Needs an elevated prompt that can
# still see this project's drive.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $root 'deploy_log.txt'
try {
    $src = Join-Path $root 'Engage App\service.engage'
    if (-not (Test-Path $src)) { throw "Engage App\service.engage not found - run build-zip.ps1 first." }
    Copy-Item -Recurse -Force $src 'C:\Program Files\Kodi\addons\'
    "OK $(Get-Date)" | Out-File -Encoding utf8 $log
    exit 0
} catch {
    "FAILED: $($_.Exception.Message)" | Out-File -Encoding utf8 $log
    exit 1
}
