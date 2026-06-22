# Kee — desktop app launcher.
#
# What it does, in order:
#   1. Make sure Ollama is up (start the local daemon if dead).
#   2. Make sure Kee's API is responding on http://127.0.0.1:7330 — if not,
#      spawn the supervisor in the background (pythonw, hidden console).
#   3. Open the dashboard in app-mode (Edge --app=… or Chrome --app=…) so it
#      looks like a standalone window with no browser chrome.
#
# Designed to be the target of a desktop / Start Menu shortcut. Safe to
# double-click while Kee is already running — every step is idempotent.

$ErrorActionPreference = 'Continue'

$KeeRoot = 'D:\Kee'
$ApiUrl  = 'http://127.0.0.1:7330'
$AppPath = "$ApiUrl/app/"

function Wait-Port {
    param([string]$Url, [int]$TimeoutSec = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { return $true }
        } catch { Start-Sleep -Milliseconds 400 }
    }
    return $false
}

# ── 1. Ollama ───────────────────────────────────────────────────────────
if (-not (Wait-Port 'http://127.0.0.1:11434/api/tags' 1)) {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollama) {
        $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
        if (Test-Path $candidate) { $ollamaPath = $candidate }
    } else { $ollamaPath = $ollama.Path }
    if ($ollamaPath) {
        Start-Process -FilePath $ollamaPath -ArgumentList 'serve' -WindowStyle Hidden
        Wait-Port 'http://127.0.0.1:11434/api/tags' 8 | Out-Null
    }
}

# ── 2. Kee API ───────────────────────────────────────────────────────────
if (-not (Wait-Port "$ApiUrl/health" 1)) {
    $pyw = Join-Path $KeeRoot '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path $pyw)) {
        $pyw = Join-Path $KeeRoot '.venv\Scripts\python.exe'
    }
    Start-Process -FilePath $pyw `
        -ArgumentList '-m','kee.main','all' `
        -WorkingDirectory $KeeRoot `
        -WindowStyle Hidden
    Wait-Port "$ApiUrl/health" 30 | Out-Null
}

# ── 3. Open dashboard in app-mode ───────────────────────────────────────
$browsers = @(
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$browser = $browsers | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($browser) {
    $userDataDir = Join-Path $env:LOCALAPPDATA 'Kee\BrowserProfile'
    if (-not (Test-Path $userDataDir)) {
        New-Item -ItemType Directory -Force -Path $userDataDir | Out-Null
    }
    Start-Process -FilePath $browser -ArgumentList `
        "--app=$AppPath", `
        "--user-data-dir=$userDataDir", `
        "--no-default-browser-check", `
        "--no-first-run"
} else {
    Start-Process $AppPath
}
