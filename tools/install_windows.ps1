# One-time Windows setup for the desk (LenovoYoga). Run from the repo root in PowerShell:
#   .\tools\install_windows.ps1            venv + requirements + tests + GrokDesk task (DISABLED) + AC power
# Enable/start the task at cutover with:  Enable-ScheduledTask GrokDesk; Start-ScheduledTask GrokDesk
param([switch]$SkipTests, [switch]$SkipPower)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$env:PYTHONUTF8 = '1'

if (-not (Test-Path .venv\Scripts\python.exe)) { py -3.12 -m venv .venv }
& .venv\Scripts\python.exe -m pip install --disable-pip-version-check -q --upgrade pip
& .venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE) { throw "pip install failed" }
"python: " + (& .venv\Scripts\python.exe --version)

if (-not $SkipTests) {
    & .venv\Scripts\python.exe -X utf8 -m pytest -q -p no:cacheprovider tests --deselect tests/test_replay.py 2>&1 | Select-Object -Last 15
}

# --- Scheduled Task: supervisor.py at logon + every 5 min (IgnoreNew: no duplicates), never stopped on battery
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "$root\.venv\Scripts\pythonw.exe" `
    -Argument "-X utf8 `"$root\supervisor.py`"" -WorkingDirectory $root
$t1 = New-ScheduledTaskTrigger -AtLogOn -User $user
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5)
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -DontStopOnIdleEnd -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$pr = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName GrokDesk -Description "Grok desk (PAPER): supervisor.py runs main.py + publisher.py" `
    -Action $action -Trigger @($t1, $t2) -Settings $set -Principal $pr -Force | Out-Null
Disable-ScheduledTask -TaskName GrokDesk | Out-Null
$t = Get-ScheduledTask GrokDesk
"task GrokDesk: state=$($t.State) triggers=$($t.Triggers.Count) battery_ok=$(-not $t.Settings.DisallowStartIfOnBatteries) stop_on_battery=$($t.Settings.StopIfGoingOnBatteries) limit=$($t.Settings.ExecutionTimeLimit) restart=$($t.Settings.RestartCount)x$($t.Settings.RestartInterval)"

# --- Power (AC only; battery untouched)
if (-not $SkipPower) {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
    powercfg /setactive SCHEME_CURRENT
    foreach ($q in @('SUB_SLEEP STANDBYIDLE', 'SUB_SLEEP HIBERNATEIDLE', 'SUB_BUTTONS LIDACTION')) {
        $o = powercfg /q SCHEME_CURRENT $q.Split(' ')[0] $q.Split(' ')[1]
        $v = @($o | Select-String ':\s*0x[0-9a-fA-F]+\s*$' | ForEach-Object { $_.ToString().Split(':')[-1].Trim() })
        "power $q  AC=$($v[-2]) DC=$($v[-1])"
    }
}
