# Run in Windows PowerShell inside the extracted repository on your AWS server.
# Installs into this folder and starts a paper-only task for the CURRENT Windows user.
[CmdletBinding()]
param([switch]$RequirePostgres)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$taskName = 'EURUSD-Paper-Bot'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw 'EURUSD-Paper-Bot task already exists. Use Task Scheduler to inspect or restart it.'
}
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Install 64-bit Python 3.12 with its Python launcher, then reopen PowerShell and rerun.'
}
& py -3.12 -c "import struct; assert struct.calcsize('P') == 8, '64-bit Python required'"
if ($LASTEXITCODE -ne 0) { throw '64-bit Python 3.12 is required.' }
& py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python -m pip install -r requirements-mt5.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

if (-not (Test-Path 'mt5-settings.json')) {
    Write-Host 'First open MT5 on this server and log in to your Exness DEMO account.'
    Write-Host 'No password is requested by this installer.'
    $terminalPath = (Read-Host 'Full path to the Exness MT5 terminal64.exe').Trim().Trim('"')
    if (-not (Test-Path -LiteralPath $terminalPath -PathType Leaf)) { throw 'MT5 executable not found.' }
    $demoLogin = [long](Read-Host 'Numeric DEMO account login shown in MT5')
    $demoServer = (Read-Host 'Exact DEMO server name shown in MT5').Trim()
    $fxSymbol = (Read-Host 'Exact EUR/USD Market Watch symbol (for example EURUSDm)').Trim()
    @{
        terminal_path = $terminalPath
        login = $demoLogin
        server = $demoServer
        symbol = $fxSymbol
    } | ConvertTo-Json | Set-Content -Encoding UTF8 'mt5-settings.json'
}
& $python windows_worker.py --check
if ($LASTEXITCODE -ne 0) { throw 'MT5 check failed. Review logs\mt5.log and fix setup before retrying.' }
if ($RequirePostgres -and -not $env:DATABASE_URL) {
    throw 'Set DATABASE_URL privately before installing with -RequirePostgres.'
}

# Interactive logon keeps Python in the same desktop session as MT5.
# No stored Windows password, auto-logon or SYSTEM task is created.
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$workerFile = Join-Path $PSScriptRoot 'windows_worker.py'
$taskArgs = '-u "' + $workerFile + '"'
if ($RequirePostgres -or $env:DATABASE_URL) { $taskArgs += ' --require-postgres' }
$action = New-ScheduledTaskAction -Execute $python -Argument $taskArgs -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
$principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$options = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $options -Description 'Read-only Exness demo quotes; internal $10 paper simulation.' | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host 'Task registered and start requested. Verify live quotes in logs\mt5.log.'
Write-Host 'Disconnect Remote Desktop; do not sign out. After a reboot, sign in again.'
Write-Host 'No broker orders are sent. Default state file: mt5-paper.db.'
