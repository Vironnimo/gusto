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

$temporary = Join-Path ([IO.Path]::GetTempPath()) ("gusto-install-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    function Receive-Asset([string]$Source, [string]$Destination) {
        if ($Source.StartsWith("https://")) {
            Invoke-WebRequest -UseBasicParsing -Uri $Source -OutFile $Destination
        } elseif ($Source.StartsWith("file://")) {
            Copy-Item -LiteralPath ([Uri]$Source).LocalPath -Destination $Destination
        } elseif ($Source -match "^[a-zA-Z][a-zA-Z0-9+.-]*://") {
            throw "Release-URLs müssen HTTPS, file:// oder lokale Pfade sein."
        } else {
            Copy-Item -LiteralPath $Source -Destination $Destination
        }
    }

    function Assert-Asset([object]$Asset, [string]$Label) {
        if (-not $Asset -or -not ($Asset.name -is [string]) `
                -or [IO.Path]::GetFileName($Asset.name) -ne $Asset.name `
                -or -not ($Asset.sha256 -is [string]) `
                -or $Asset.sha256 -notmatch "^[0-9a-fA-F]{64}$") {
            throw "Manifest-Asset '$Label' ist ungültig."
        }
    }

    function Assert-Sha256([string]$Path, [string]$Expected, [string]$Label) {
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try {
            $stream = [IO.File]::OpenRead($Path)
            try {
                $digest = $sha256.ComputeHash($stream)
            } finally {
                $stream.Dispose()
            }
        } finally {
            $sha256.Dispose()
        }
        $actual = [BitConverter]::ToString($digest).Replace("-", "").ToLowerInvariant()
        if ($actual -ne $Expected.ToLowerInvariant()) {
            throw "SHA-256-Prüfung für '$Label' fehlgeschlagen."
        }
    }

    $manifestPath = Join-Path $temporary "gusto-release.json"
    Receive-Asset "$ReleaseBase/gusto-release.json" $manifestPath
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.schema_version -ne 2 -or -not ($manifest.version -is [string]) `
            -or $manifest.version -notmatch "^[0-9]+\.[0-9]+\.[0-9]+$") {
        throw "Das Release-Manifest ist unvollständig oder unbekannt."
    }
    $wheelAsset = $manifest.assets.wheel
    $installerAsset = $manifest.assets.installer
    $pythonAsset = $manifest.assets.windows_python_x64
    Assert-Asset $wheelAsset "wheel"
    Assert-Asset $installerAsset "installer"
    Assert-Asset $pythonAsset "windows_python_x64"
    if (-not ($pythonAsset.version -is [string]) `
            -or $pythonAsset.version -notmatch "^[0-9]+\.[0-9]+\.[0-9]+$" `
            -or $pythonAsset.layout -ne "tools") {
        throw "Die Windows-Python-Runtime im Manifest ist ungültig."
    }

    $payload = Join-Path $temporary "payload"
    New-Item -ItemType Directory -Path $payload | Out-Null
    $wheelPath = Join-Path $payload $wheelAsset.name
    $installerPath = Join-Path $payload $installerAsset.name
    $pythonPackage = Join-Path $temporary $pythonAsset.name
    foreach ($item in @(
        @("$ReleaseBase/$($wheelAsset.name)", $wheelPath, $wheelAsset.sha256, "Gusto-Wheel"),
        @("$ReleaseBase/$($installerAsset.name)", $installerPath, $installerAsset.sha256, "Installer"),
        @("$ReleaseBase/$($pythonAsset.name)", $pythonPackage, $pythonAsset.sha256, "Python-Runtime")
    )) {
        Receive-Asset $item[0] $item[1]
        Assert-Sha256 $item[1] $item[2] $item[3]
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $runtimeSource = Join-Path $temporary "python-runtime"
    New-Item -ItemType Directory -Path $runtimeSource | Out-Null
    $destinationRoot = [IO.Path]::GetFullPath($runtimeSource) + [IO.Path]::DirectorySeparatorChar
    $zip = [IO.Compression.ZipFile]::OpenRead($pythonPackage)
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace("\", "/")
            if (-not $name.StartsWith("tools/", [StringComparison]::Ordinal)) {
                continue
            }
            $relative = $name.Substring(6)
            if (-not $relative) { continue }
            if ($relative.Contains(":")) {
                throw "Unsicherer Python-Paketeintrag: $name"
            }
            $target = [IO.Path]::GetFullPath((Join-Path $runtimeSource $relative))
            if (-not $target.StartsWith($destinationRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Unsicherer Python-Paketeintrag: $name"
            }
            if (-not $entry.Name) {
                New-Item -ItemType Directory -Force -Path $target | Out-Null
                continue
            }
            $parent = [IO.Path]::GetDirectoryName($target)
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
            $inputStream = $entry.Open()
            try {
                $outputStream = [IO.File]::Create($target)
                try {
                    $inputStream.CopyTo($outputStream)
                } finally {
                    $outputStream.Dispose()
                }
            } finally {
                $inputStream.Dispose()
            }
        }
    } finally {
        $zip.Dispose()
    }
    $python = Join-Path $runtimeSource "python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Das Python-Runtime-Paket enthält tools/python.exe nicht."
    }

    $arguments = @(
        $installerPath,
        "--manifest-url", "$ReleaseBase/gusto-release.json",
        "--managed-python-source", $runtimeSource,
        "--managed-python-version", $pythonAsset.version,
        "--managed-python-sha256", $pythonAsset.sha256,
        "--host", $HostName,
        "--port", "$Port"
    )
    if ($InstallDir) { $arguments += @("--install-dir", $InstallDir) }
    if ($DataDir) { $arguments += @("--data-dir", $DataDir) }
    if ($Repair) { $arguments += "--repair" }
    if ($DryRun) { $arguments += "--dry-run" }
    if ($Json) { $arguments += "--json" }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Die Gusto-Installation ist mit Status $LASTEXITCODE fehlgeschlagen."
    }
} finally {
    Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
