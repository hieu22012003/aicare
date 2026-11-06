# PowerShell Build Script — DEBUG Build (Console Enabled)

# Target: Windows 10/11 x64

# Output: dist/AI_Rehabilitation_Assistant_DEBUG/



$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$ProjectRoot = (Get-Item $ScriptDir).Parent.FullName



Write-Host "==========================================================" -ForegroundColor Cyan

Write-Host "  BUILDING DEBUG ONEDIR: AI_Rehabilitation_Assistant_DEBUG" -ForegroundColor Cyan

Write-Host "  Project Root: $ProjectRoot" -ForegroundColor Gray

Write-Host "==========================================================" -ForegroundColor Cyan



# 1. Locate Virtual Environment

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {

    Write-Error "Virtual environment not found at $VenvPython. Run: uv venv --python 3.12 .venv"

}



# 2. Clean previous build artifacts

Write-Host "[1/4] Cleaning previous debug artifacts..." -ForegroundColor Yellow

$DebugDist = Join-Path $ProjectRoot "dist\AI_Rehabilitation_Assistant_DEBUG"

if (Test-Path $DebugDist) {

    Remove-Item -Recurse -Force $DebugDist

}

$BuildDir = Join-Path $ProjectRoot "build\AI_Rehabilitation_Assistant_DEBUG"

if (Test-Path $BuildDir) {

    Remove-Item -Recurse -Force $BuildDir

}



# 3. Set environment variables for build

$env:PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION = "python"

$env:AI_CARE_BUILD_DEBUG = "1"



# 4. Invoke PyInstaller

Write-Host "[2/4] Invoking PyInstaller in DEBUG mode (Console=True)..." -ForegroundColor Yellow

$SpecPath = Join-Path $ScriptDir "AI_Rehabilitation_Assistant.spec"

& $VenvPython -m PyInstaller --clean --noconfirm $SpecPath



if ($LASTEXITCODE -ne 0) {

    Write-Error "PyInstaller debug build failed with exit code $LASTEXITCODE"

}



# 5. Verify output binary

$ExePath = Join-Path $DebugDist "AI_Rehabilitation_Assistant_DEBUG.exe"

if (Test-Path $ExePath) {

    Write-Host "[3/4] Build succeeded!" -ForegroundColor Green

    Write-Host "Output Directory: $DebugDist" -ForegroundColor Green

    Write-Host "Executable:       $ExePath" -ForegroundColor Green

    Write-Host "==========================================================" -ForegroundColor Cyan

} else {

    Write-Error "Expected executable not found at $ExePath"

}

