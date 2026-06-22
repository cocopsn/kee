param(
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$model = "hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = "python"
}

if (-not $env:OLLAMA_MODELS) { $env:OLLAMA_MODELS = "D:\Ollama\models" }
if (-not $env:OLLAMA_KV_CACHE_TYPE) { $env:OLLAMA_KV_CACHE_TYPE = "q4_0" }
if (-not $env:OLLAMA_FLASH_ATTENTION) { $env:OLLAMA_FLASH_ATTENTION = "1" }
if (-not $env:OLLAMA_KEEP_ALIVE) { $env:OLLAMA_KEEP_ALIVE = "24h" }
$env:KEE_MODEL = $model
$env:KEE_LLM_PRIMARY = "ollama"

$ollamaApi = "http://127.0.0.1:11434"

function Test-Ollama {
    try {
        Invoke-RestMethod -Uri "$ollamaApi/api/tags" -TimeoutSec 5 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Resolve-OllamaExe {
    $cmd = Get-Command "ollama" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path -LiteralPath $candidate) { return $candidate }

    return $null
}

$ollamaExe = Resolve-OllamaExe
if (-not (Test-Ollama)) {
    if (-not $ollamaExe) {
        throw "Ollama is not responding and ollama.exe was not found."
    }

    Write-Host "Starting Ollama server..."
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null

    $ready = $false
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Ollama) {
            $ready = $true
            break
        }
    }

    if (-not $ready) {
        throw "Ollama did not become ready on $ollamaApi."
    }
}

Set-Location -LiteralPath $projectRoot
Write-Host ""
Write-Host "Kee Chat"
Write-Host "Project: $projectRoot"
Write-Host "Model:   $model"
Write-Host ""

if ($CheckOnly) {
    Write-Host "Shortcut preflight OK."
    exit 0
}

& $pythonExe -m kee.main terminal
$code = if ($LASTEXITCODE -ne $null) { $LASTEXITCODE } else { 0 }
if ($code -ne 0) {
    Write-Host ""
    Write-Host "Kee exited with code $code."
    Read-Host "Press Enter to close"
}
exit $code
