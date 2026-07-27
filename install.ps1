param(
    [string]$ReleaseBase = $env:GUSTO_RELEASE_BASE,
    [string]$InstallDir = "",
    [string]$DataDir = "",
    [string]$HostName = "0.0.0.0",
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$Repair,
    [switch]$DryRun,
    [switch]$Json
)

$ErrorActionPreference = "Stop"
if (-not $ReleaseBase) {
    $ReleaseBase = "https://github.com/Vironnimo/gusto/releases/latest/download"
}
$ReleaseBase = $ReleaseBase.TrimEnd("/")

$python = Get-Command py.exe -ErrorAction SilentlyContinue
$pythonPrefix = @("-3")
if (-not $python) {
    $python = Get-Command python.exe -ErrorAction Stop
    $pythonPrefix = @()
}
& $python.Source @pythonPrefix -c "import sys; raise SystemExit(sys.version_info < (3, 10))"
if ($LASTEXITCODE -ne 0) {
    throw "Gusto braucht Python 3.10 oder neuer."
}

$temporary = Join-Path ([IO.Path]::GetTempPath()) ("gusto-install-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    function Receive-Asset([string]$Source, [string]$Destination) {
        if ($Source.StartsWith("https://")) {
            Invoke-WebRequest -UseBasicParsing -Uri $Source -OutFile $Destination
        } elseif ($Source.StartsWith("file://")) {
            Copy-Item -LiteralPath ([Uri]$Source).LocalPath -Destination $Destination
        } else {
            Copy-Item -LiteralPath $Source -Destination $Destination
        }
    }

    $manifestPath = Join-Path $temporary "gusto-release.json"
    Receive-Asset "$ReleaseBase/gusto-release.json" $manifestPath
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.schema_version -ne 1 -or -not $manifest.archive `
            -or -not $manifest.checksum -or -not $manifest.sha256) {
        throw "Das Release-Manifest ist unvollständig oder unbekannt."
    }
    foreach ($name in @($manifest.archive, $manifest.checksum)) {
        if ([IO.Path]::GetFileName($name) -ne $name) {
            throw "Release-Assets müssen einfache Dateinamen sein."
        }
    }
    $archivePath = Join-Path $temporary $manifest.archive
    $checksumPath = Join-Path $temporary $manifest.checksum
    Receive-Asset "$ReleaseBase/$($manifest.archive)" $archivePath
    Receive-Asset "$ReleaseBase/$($manifest.checksum)" $checksumPath

    $checksumWords = (Get-Content -Raw -LiteralPath $checksumPath).Trim().Split()
    if ($checksumWords.Count -lt 1 -or
            $checksumWords[0].ToLowerInvariant() -ne $manifest.sha256.ToLowerInvariant()) {
        throw "Manifest und Checksum-Datei stimmen nicht überein."
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
    if ($actual -ne $manifest.sha256.ToLowerInvariant()) {
        throw "SHA-256-Prüfung des Release-Archivs fehlgeschlagen."
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $extract = Join-Path $temporary "release"
    New-Item -ItemType Directory -Path $extract | Out-Null
    $destinationRoot = [IO.Path]::GetFullPath($extract) + [IO.Path]::DirectorySeparatorChar
    $zip = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        foreach ($entry in $zip.Entries) {
            $target = [IO.Path]::GetFullPath((Join-Path $extract $entry.FullName))
            if (-not $target.StartsWith($destinationRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Unsicherer ZIP-Eintrag: $($entry.FullName)"
            }
        }
    } finally {
        $zip.Dispose()
    }
    [IO.Compression.ZipFile]::ExtractToDirectory($archivePath, $extract)
    $installers = @(Get-ChildItem -LiteralPath $extract -Filter install.py -File -Recurse)
    if ($installers.Count -ne 1) {
        throw "Release enthält nicht genau einen Installer."
    }

    $arguments = @()
    $arguments += $pythonPrefix
    $arguments += $installers[0].FullName
    $arguments += @("--manifest-url", "$ReleaseBase/gusto-release.json")
    if ($InstallDir) { $arguments += @("--install-dir", $InstallDir) }
    if ($DataDir) { $arguments += @("--data-dir", $DataDir) }
    $arguments += @("--host", $HostName, "--port", "$Port")
    if ($Repair) { $arguments += "--repair" }
    if ($DryRun) { $arguments += "--dry-run" }
    if ($Json) { $arguments += "--json" }
    & $python.Source @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Die Gusto-Installation ist mit Status $LASTEXITCODE fehlgeschlagen."
    }
} finally {
    Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
