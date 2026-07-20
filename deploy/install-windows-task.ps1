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
$AutostartLauncher = Join-Path $InstallDir "Scripts\gusto-autostart.exe"

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
    Write-Output "  `"$AutostartLauncher`" --host 0.0.0.0 --port $Port"
    exit 0
}

$existingTask = Get-ScheduledTask -TaskName "Gusto" -ErrorAction SilentlyContinue
$existingTaskWasRunning = $null -ne $existingTask -and $existingTask.State -eq "Running"
if ($existingTaskWasRunning) {
    Stop-ScheduledTask -TaskName "Gusto"
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 200
        $existingTask = Get-ScheduledTask -TaskName "Gusto" -ErrorAction Stop
    } while ($existingTask.State -eq "Running" -and [DateTime]::UtcNow -lt $deadline)
    if ($existingTask.State -eq "Running") {
        throw "Die bisherige Gusto-Autostart-Instanz konnte nicht beendet werden."
    }
}

try {
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

    $AutostartLauncher = [IO.Path]::GetFullPath($AutostartLauncher)
    if (-not (Test-Path -LiteralPath $AutostartLauncher -PathType Leaf)) {
        throw "Der fensterlose Gusto-Autostart-Launcher fehlt: $AutostartLauncher"
    }

    $actionArguments = "--host 0.0.0.0 --port $Port"
    $action = New-ScheduledTaskAction `
        -Execute $AutostartLauncher `
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

    $legacyLauncher = Join-Path $DataDir "start-gusto.ps1"
    if (Test-Path -LiteralPath $legacyLauncher -PathType Leaf) {
        Remove-Item -LiteralPath $legacyLauncher -Force
    }
    Start-ScheduledTask -TaskName "Gusto"
} catch {
    if ($existingTaskWasRunning) {
        try {
            Start-ScheduledTask -TaskName "Gusto" -ErrorAction Stop
        } catch {
            Write-Warning "Die bisherige Gusto-Aufgabe konnte nach dem Fehler nicht neu gestartet werden: $_"
        }
    }
    throw
}

Write-Output ""
Write-Output "Der optionale Windows-Autostart ist aktiv. Öffne:"
Write-Output "  http://localhost:$Port"
Write-Output "Log:"
Write-Output "  $(Join-Path $DataDir 'gusto-autostart.log')"
