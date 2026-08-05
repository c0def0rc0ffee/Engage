# Builds both release zips straight from the project root, plus the runnable
# App copy:
#   Engage Dist\service.engage-<version>.zip  - installable Kodi add-on zip
#   Engage Git\engage-v<version>-src.zip      - GitHub-bound source zip
#   Engage App\service.engage\                - mirror of the Dist zip contents
#
# The Dist zip deliberately keeps Kodi's <addon-id>-<version>.zip naming:
# Kodi repositories require it, and the README install instructions and all
# past GitHub Releases use it.
#
# Zips are written with .NET directly because Compress-Archive writes
# backslash entry paths, which Kodi rejects as "invalid structure".
#
# Run from the repo root:  powershell -File build-zip.ps1
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = (Get-Content (Join-Path $root 'VERSION') -Raw).Trim()

# VERSION drives the zip names; addon.xml is what Kodi actually reads.
# Refuse to build if they have drifted apart.
$addonXml = [xml](Get-Content (Join-Path $root 'service.engage\addon.xml'))
if ($addonXml.addon.version -ne $version) {
    throw "VERSION ($version) != addon.xml ($($addonXml.addon.version)) - update both before building."
}

$distDir = Join-Path $root 'Engage Dist'
$gitDir  = Join-Path $root 'Engage Git'
$appDir  = Join-Path $root 'Engage App'
$stage   = Join-Path $env:TEMP 'engage-build'

foreach ($d in @($distDir, $gitDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }

function New-ForwardSlashZip {
    # $RootEntryName '' zips SourceDir's contents at the archive root;
    # otherwise every entry is prefixed "<RootEntryName>/" (Kodi needs the
    # add-on folder as the top level of the zip).
    param([string]$SourceDir, [string]$ZipPath, [string]$RootEntryName)

    if (Test-Path $ZipPath) { Remove-Item -Force -LiteralPath $ZipPath }
    $fs  = [System.IO.File]::Open($ZipPath, [System.IO.FileMode]::Create)
    $zip = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        Get-ChildItem -Recurse -File $SourceDir | ForEach-Object {
            $rel = $_.FullName.Substring($SourceDir.Length + 1).Replace('\', '/')
            if ($RootEntryName) { $rel = "$RootEntryName/$rel" }
            $entry = $zip.CreateEntry($rel, [System.IO.Compression.CompressionLevel]::Optimal)
            $es = $entry.Open()
            $in = [System.IO.File]::OpenRead($_.FullName)
            $in.CopyTo($es)
            $in.Dispose(); $es.Dispose()
        }
    } finally {
        $zip.Dispose(); $fs.Dispose()
    }
    $size = [math]::Round((Get-Item $ZipPath).Length / 1KB)
    Write-Output "Built: $ZipPath ($size KB)"
}

# --- Dist: the installable add-on only, no bytecode / NAS metadata ---------
$distStage = Join-Path $stage 'dist\service.engage'
robocopy (Join-Path $root 'service.engage') $distStage /E `
    /XD '__pycache__' '@eaDir' `
    /XF '*.pyc' '*.pyo' '*.zip' '*.7z' '*.tmp' /NFL /NDL /NJH | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed staging dist (exit $LASTEXITCODE)" }

New-ForwardSlashZip -SourceDir $distStage `
    -ZipPath (Join-Path $distDir "service.engage-$version.zip") `
    -RootEntryName 'service.engage'

# App copy: exact mirror of the Dist zip contents.
# /MIR removes anything not in the stage - never hand-edit this folder.
robocopy $distStage (Join-Path $appDir 'service.engage') /MIR /NFL /NDL /NJH | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed mirroring App (exit $LASTEXITCODE)" }
Write-Output "Mirrored: $appDir\service.engage"

# --- Git: full source tree (housekeeping + working assets included) --------
# Every dot folder is dropped, so version control and editor state stay out of
# the snapshot without this list needing to name each one.
#
# The release tooling is dropped too. publish.conf and the release guide name
# the file server, and this snapshot is the copy handed to someone else for
# testing, so a private address must not ride along in it. Nothing here scans
# the snapshot, which is exactly why the exclusion has to be right.
$srcStage = Join-Path $stage 'src'
$dotDirs = @(Get-ChildItem -LiteralPath $root -Directory -Force -ErrorAction SilentlyContinue |
             Where-Object { $_.Name.StartsWith('.') } |
             ForEach-Object { $_.FullName })
robocopy $root $srcStage /E `
    /XD 'Engage Dist' 'Engage Git' 'Engage App' '$RECYCLE.BIN' '__pycache__' '@eaDir' @dotDirs `
    /XF '*.zip' '*.7z' '*.tmp' '*.log' '*.pyc' '*.pyo' 'deploy_log.txt' `
        'publish-github.sh' 'push-source.sh' 'publish.conf' '.publish-allow' `
        'GITHUB-RELEASE-GUIDE.md' 'Github repository' 'release-mirror.conf' `
        /NFL /NDL /NJH | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed staging src (exit $LASTEXITCODE)" }

New-ForwardSlashZip -SourceDir $srcStage `
    -ZipPath (Join-Path $gitDir "engage-v$version-src.zip") `
    -RootEntryName ''

Remove-Item $stage -Recurse -Force
