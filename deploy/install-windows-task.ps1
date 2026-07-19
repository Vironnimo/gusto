param(
    [string]$DataDir = "",
    [string]$InstallDir = "",
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Installer = Join-Path $ProjectDir "install.py"

if (-not $InstallDir) {
    $localData = $env:LOCALAPPDATA
    if (-not $localData) {
        $localData = [Environment]::GetFolderPath("LocalApplicationData")
    }
    $InstallDir = Join-Path $localData "Programs\Gusto"
}
$InstallDir = [IO.Path]::GetFullPath(
    [Environment]::ExpandEnvironmentVariables($InstallDir)
)
$VenvPython = Join-Path $InstallDir "Scripts\python.exe"

if (-not $DataDir) {
    $localData = $env:LOCALAPPDATA
    if (-not $localData) {
        $localData = [Environment]::GetFolderPath("LocalApplicationData")
    }
    $DataDir = Join-Path $localData "Gusto"
}
$DataDir = [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($DataDir))

$bootstrap = Get-Command py.exe -ErrorAction SilentlyContinue
$bootstrapArguments = @()
if ($bootstrap) {
    $bootstrapArguments += "-3"
} else {
    $bootstrap = Get-Command python.exe -ErrorAction Stop
}
$bootstrapArguments += $Installer
$bootstrapArguments += @("--venv", $InstallDir)

Write-Output "Quelle:       $ProjectDir"
Write-Output "Installation: $InstallDir"
Write-Output "Daten:        $DataDir"
Write-Output "Port:         $Port"

if ($DryRun) {
    Write-Output ""
    Write-Output "Geplant:"
    Write-Output "  $($bootstrap.Source) $($bootstrapArguments -join ' ')"
    Write-Output "  Autostart-Aufgabe 'Gusto' bei der Windows-Anmeldung"
    Write-Output "  $VenvPython -m gusto serve --host 0.0.0.0 --port $Port"
    exit 0
}

$previousHome = $env:GUSTO_HOME
$env:GUSTO_HOME = $DataDir
try {
    & $bootstrap.Source @bootstrapArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Die allgemeine Gusto-Installation ist fehlgeschlagen."
    }
} finally {
    $env:GUSTO_HOME = $previousHome
}

foreach ($name in @("recipes", "images", "data")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $DataDir $name) | Out-Null
}

$launcher = Join-Path $DataDir "start-gusto.ps1"
$escapedPython = $VenvPython.Replace("'", "''")
@(
    '$ErrorActionPreference = "Stop"'
    "& '$escapedPython' -m gusto serve --host 0.0.0.0 --port $Port"
) | Set-Content -LiteralPath $launcher -Encoding utf8

$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$actionArguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
$action = New-ScheduledTaskAction `
    -Execute $powershell `
    -Argument $actionArguments `
    -WorkingDirectory $DataDir
$user = "$env:USERDOMAIN\$env:USERNAME"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
Register-ScheduledTask `
    -TaskName "Gusto" `
    -Action $action `
    -Trigger $trigger `
    -Description "Gusto Rezept-Webserver beim Anmelden starten" `
    -Force | Out-Null
Start-ScheduledTask -TaskName "Gusto"

Write-Output ""
Write-Output "Der optionale Windows-Autostart ist aktiv. Öffne:"
Write-Output "  http://localhost:$Port"
