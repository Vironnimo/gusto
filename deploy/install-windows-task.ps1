param(
    [string]$AppRoot = (Join-Path $env:LOCALAPPDATA "Programs\Gusto")
)

$ErrorActionPreference = "Stop"
$AppRoot = [IO.Path]::GetFullPath(
    [Environment]::ExpandEnvironmentVariables($AppRoot)
)
$current = Get-Content -Raw -LiteralPath (Join-Path $AppRoot "current.json") |
    ConvertFrom-Json
$state = Get-Content -Raw -LiteralPath (Join-Path $AppRoot "install-state.json") |
    ConvertFrom-Json
$launcher = Join-Path $current.runtime "Scripts\gusto-autostart.exe"
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Gusto-Autostart-Launcher fehlt: $launcher"
}
$action = New-ScheduledTaskAction -Execute $launcher `
    -Argument "--host `"$($state.host)`" --port $($state.port)" `
    -WorkingDirectory $state.data_dir
$user = "$env:USERDOMAIN\$env:USERNAME"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)
$old = Get-ScheduledTask -TaskName "Gusto" -ErrorAction SilentlyContinue
if ($null -ne $old -and $old.State -eq "Running") {
    Stop-ScheduledTask -TaskName "Gusto"
}
Register-ScheduledTask -TaskName "Gusto" -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "Gusto Rezeptserver beim Anmelden starten" -Force | Out-Null
Start-ScheduledTask -TaskName "Gusto"
Write-Output "Gusto läuft als Current-User-Aufgabe."
