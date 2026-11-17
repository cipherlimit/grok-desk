# One-time Windows setup for the desk (LenovoYoga). Run from the repo root in PowerShell:
#   .\tools\install_windows.ps1            venv + requirements + tests + tasks + AC power
#   .\tools\install_windows.ps1 -TasksOnly only (re)register GrokDeskHealth + GrokNightReport (+ GrokDesk if missing)
# GrokDesk is registered DISABLED the first time; an existing GrokDesk is never touched (it may be running).
# Enable/start it at cutover with:  Enable-ScheduledTask GrokDesk; Start-ScheduledTask GrokDesk
param([switch]$SkipTests, [switch]$SkipPower, [switch]$TasksOnly)
if ($TasksOnly) { $SkipTests = $true; $SkipPower = $true }
$ErrorActionPreference = 'Continue'   # native stderr (pip warnings) must not abort under PS 5.1
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$env:PYTHONUTF8 = '1'

if (-not $TasksOnly) {
    if (-not (Test-Path .venv\Scripts\python.exe)) { py -3.12 -m venv .venv }
    & .venv\Scripts\python.exe -m pip install --disable-pip-version-check -q --upgrade pip
    & .venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt
    if ($LASTEXITCODE) { throw "pip install failed" }
    "python: " + (& .venv\Scripts\python.exe --version)
}

if (-not $SkipTests) {
    & .venv\Scripts\python.exe -X utf8 -m pytest -q -p no:cacheprovider tests --deselect tests/test_replay.py 2>&1 | Select-Object -Last 15
}

# --- Scheduled Task: supervisor.py at logon + every 5 min (IgnoreNew: no duplicates), never stopped on battery
$user = "$env:USERDOMAIN\$env:USERNAME"
$pyw = "$root\.venv\Scripts\pythonw.exe"
$pr = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
function Show-Task($name) {
    $t = Get-ScheduledTask $name; $i = Get-ScheduledTaskInfo $name
    "task ${name}: state=$($t.State) triggers=$($t.Triggers.Count) actions=$($t.Actions.Count) next=$($i.NextRunTime) battery_ok=$(-not $t.Settings.DisallowStartIfOnBatteries) stop_on_battery=$($t.Settings.StopIfGoingOnBatteries)"
}
if (Get-ScheduledTask -TaskName GrokDesk -ErrorAction SilentlyContinue) {
    "GrokDesk already registered: left as is"; Show-Task GrokDesk
} else {
$action = New-ScheduledTaskAction -Execute "$root\.venv\Scripts\pythonw.exe" `
    -Argument "-X utf8 `"$root\supervisor.py`"" -WorkingDirectory $root
$t1 = New-ScheduledTaskTrigger -AtLogOn -User $user
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5)
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -DontStopOnIdleEnd -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName GrokDesk -Description "Grok desk (PAPER): supervisor.py runs main.py + publisher.py" `
    -Action $action -Trigger @($t1, $t2) -Settings $set -Principal $pr -Force | Out-Null
Disable-ScheduledTask -TaskName GrokDesk | Out-Null
Show-Task GrokDesk
}

# --- GrokDeskHealth: tools\desk_health.py hourly (Telegram alerts; restarts GrokDesk on stale heartbeats;
#     reconnect alert unchanged: >10 websocket reconnects in the last hour, checked once an hour)
$hs = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$ha = New-ScheduledTaskAction -Execute $pyw -Argument "-X utf8 `"$root\tools\desk_health.py`"" -WorkingDirectory $root
$ht = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours((Get-Date).Hour + 1) -RepetitionInterval (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName GrokDeskHealth -Description "Grok desk health (STEP 12), hourly" `
    -Action $ha -Trigger $ht -Settings $hs -Principal $pr -Force | Out-Null
Show-Task GrokDeskHealth

# --- GrokNightReport: 08:01 daily (night ends 08:00 local): build the report, then ship it to Pages.
#     Two actions run in order. The publisher keeps re-shipping a <26 h old report on every regular publish.
$na1 = New-ScheduledTaskAction -Execute $pyw -Argument "-X utf8 `"$root\tools\night_report.py`" --clear-skips" -WorkingDirectory $root
$na2 = New-ScheduledTaskAction -Execute $pyw -Argument "-X utf8 `"$root\publisher.py`" --once --with-report" -WorkingDirectory $root
$nt = New-ScheduledTaskTrigger -Daily -At 08:01
Register-ScheduledTask -TaskName GrokNightReport -Description "Grok desk night report -> Pages (box routine fetches it)" `
    -Action @($na1, $na2) -Trigger $nt -Settings $hs -Principal $pr -Force | Out-Null
Show-Task GrokNightReport

# --- Power (AC only; battery untouched)
if (-not $SkipPower) {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
    powercfg /setactive SCHEME_CURRENT
    foreach ($q in @('SUB_SLEEP STANDBYIDLE', 'SUB_SLEEP HIBERNATEIDLE', 'SUB_BUTTONS LIDACTION')) {
        $o = powercfg /qh SCHEME_CURRENT $q.Split(' ')[0] $q.Split(' ')[1]
        $v = @($o | Select-String ':\s*0x[0-9a-fA-F]+\s*$' | ForEach-Object { $_.ToString().Split(':')[-1].Trim() })
        "power $q  AC=$($v[-2]) DC=$($v[-1])"
    }
}
