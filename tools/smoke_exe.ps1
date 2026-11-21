# Smoke-test the built exe: launch it with a temp APPDATA (never the user's real config.json)
# and check it is still alive after a few seconds. Keep this file ASCII: Windows PowerShell 5.1
# reads BOM-less files as the ANSI code page, and multibyte text can swallow line breaks.
# Usage: powershell -File tools\smoke_exe.ps1 [-Exe dist\BPAuditor.exe] [-Seconds 8]
param(
    [string]$Exe = (Join-Path $PSScriptRoot '..\dist\BPAuditor.exe'),
    [int]$Seconds = 8
)
$ErrorActionPreference = 'Stop'
$exePath = (Resolve-Path $Exe).Path
if (Get-Process BPAuditor -ErrorAction SilentlyContinue) {
    # Single instance: a second copy hands off to the running one and exits, so the smoke would fail.
    Write-Output 'FAIL: BPAuditor is already running; quit it from the tray and rerun.'
    exit 1
}
$tmp = Join-Path $env:TEMP ('bpa-smoke-' + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
$psi = New-Object System.Diagnostics.ProcessStartInfo $exePath
$psi.UseShellExecute = $false
$psi.EnvironmentVariables['APPDATA'] = $tmp
$p = [System.Diagnostics.Process]::Start($psi)
try {
    Start-Sleep -Seconds $Seconds
    $alive = -not $p.HasExited
    $code = if ($alive) { $null } else { $p.ExitCode }
} finally {
    # The onefile bootloader starts a child process; kill the whole tree.
    if (-not $p.HasExited) { taskkill /T /F /PID $p.Id | Out-Null }
    Start-Sleep -Milliseconds 500
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
if ($alive) { Write-Output "OK: still running after $Seconds s"; exit 0 }
Write-Output "FAIL: exited within $Seconds s, exit code $code"
exit 1
