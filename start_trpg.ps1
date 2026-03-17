$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendRoot = Join-Path $repoRoot "frontend"

function Test-CommandExists {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Fail-AndExit {
    param([string]$Message)
    Write-Host ""
    Write-Host "[ERROR] $Message" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "AI_TRPG_616 launcher" -ForegroundColor Cyan
Write-Host "Repo: $repoRoot"
Write-Host ""
Write-Host "[1/4] Checking Python..."

if (-not (Test-CommandExists "python")) {
    Fail-AndExit "python was not found in PATH. Please install Python first."
}

$pythonVersion = python --version 2>&1
Write-Host "      OK: $pythonVersion"
Write-Host ""
Write-Host "[2/4] Checking Node/npm..."

$npmExecutable = $null
if (Test-CommandExists "npm") {
    $npmExecutable = "npm"
} elseif (Test-CommandExists "npm.cmd") {
    $npmExecutable = "npm.cmd"
}

if (-not $npmExecutable) {
    Fail-AndExit "npm was not found in PATH. Please install Node.js first."
}

$npmVersion = & $npmExecutable --version 2>&1
Write-Host "      OK: npm $npmVersion"

$packageJsonPath = Join-Path $frontendRoot "package.json"
if (-not (Test-Path $packageJsonPath)) {
    Fail-AndExit "frontend/package.json was not found."
}

$nodeModulesPath = Join-Path $frontendRoot "node_modules"
if (-not (Test-Path $nodeModulesPath)) {
    Fail-AndExit "frontend/node_modules was not found. Run 'cd frontend' then 'npm install' first."
}

Write-Host ""
Write-Host "[3/4] Starting backend window..."
$backendCommand = "`$env:PYTHONPATH='$repoRoot\Code'; Set-Location '$repoRoot'; python -m trpg_runtime.http_server --port 8788"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCommand | Out-Null

Start-Sleep -Milliseconds 700

Write-Host "[4/4] Starting frontend window..."
$frontendCommand = "Set-Location '$frontendRoot'; $npmExecutable run dev -- --host 127.0.0.1 --port 5173"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCommand | Out-Null

Start-Sleep -Milliseconds 1200
Write-Host "[5/5] Opening browser..."
Start-Process "http://127.0.0.1:5173/" | Out-Null

Write-Host ""
Write-Host "Started successfully." -ForegroundColor Green
Write-Host "Frontend: http://127.0.0.1:5173"
Write-Host "Backend : http://127.0.0.1:8788"
Write-Host ""
Read-Host "Press Enter to close this launcher"
