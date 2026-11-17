# Windows counterpart of run.sh: (re)start the desk through the GrokDesk Scheduled Task,
# or, with -Foreground, run the supervisor in this console (Ctrl+C stops everything).
param([switch]$Foreground)
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path MOVED_TO) { Write-Output "MOVED_TO present; not starting"; exit 0 }
if ($Foreground) {
    & "$PSScriptRoot\.venv\Scripts\python.exe" -X utf8 supervisor.py
    exit $LASTEXITCODE
}
$t = Get-ScheduledTask -TaskName GrokDesk -ErrorAction SilentlyContinue
if (-not $t) { Write-Error "Scheduled Task GrokDesk not registered (see tools\install_windows.ps1)"; exit 1 }
if ($t.State -eq 'Running') { Stop-ScheduledTask -TaskName GrokDesk; Start-Sleep 3 }
Start-ScheduledTask -TaskName GrokDesk
Start-Sleep 8
foreach ($n in 'supervisor','main','publisher') {
    $f = "logs\$n.pid"
    if (Test-Path $f) { $p = (Get-Content $f).Trim(); "$n.py pid $p alive=$([bool](Get-Process -Id $p -ErrorAction SilentlyContinue))" }
}
