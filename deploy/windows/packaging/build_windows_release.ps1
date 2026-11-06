# PowerShell Build Script — RELEASE Build (Windowed, Console Disabled)

# Target: Windows 10/11 x64

# Output: dist/AI_Rehabilitation_Assistant/



$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$ProjectRoot = (Get-Item $ScriptDir).Parent.FullName



Write-Host "==========================================================" -ForegroundColor Cyan

Write-Host "  BUILDING RELEASE ONEDIR: AI_Rehabilitation_Assistant" -ForegroundColor Cyan

Write-Host "  Project Root: $ProjectRoot" -ForegroundColor Gray

Write-Host "==========================================================" -ForegroundColor Cyan



# 1. Locate Virtual Environment

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {

    Write-Error "Virtual environment not found at $VenvPython. Run: uv venv --python 3.12 .venv"

}



# 2. Clean previous build artifacts

Write-Host "[1/4] Cleaning previous release artifacts..." -ForegroundColor Yellow

$ReleaseDist = Join-Path $ProjectRoot "dist\AI_Rehabilitation_Assistant"

if (Test-Path $ReleaseDist) {

    Remove-Item -Recurse -Force $ReleaseDist

}

$BuildDir = Join-Path $ProjectRoot "build\AI_Rehabilitation_Assistant"

if (Test-Path $BuildDir) {

    Remove-Item -Recurse -Force $BuildDir

}



# 3. Set environment variables for build

$env:PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION = "python"

$env:AI_CARE_BUILD_DEBUG = "0"



# 4. Invoke PyInstaller

Write-Host "[2/4] Invoking PyInstaller in RELEASE mode (Windowed, Console=False)..." -ForegroundColor Yellow

$SpecPath = Join-Path $ScriptDir "AI_Rehabilitation_Assistant.spec"

& $VenvPython -m PyInstaller --clean --noconfirm $SpecPath



if ($LASTEXITCODE -ne 0) {

    Write-Error "PyInstaller release build failed with exit code $LASTEXITCODE"

}



# 5. Verify output binary

$ExePath = Join-Path $ReleaseDist "AI_Rehabilitation_Assistant.exe"

if (Test-Path $ExePath) {

    Write-Host "[3/4] Release build succeeded!" -ForegroundColor Green

    Write-Host "Output Directory: $ReleaseDist" -ForegroundColor Green

    Write-Host "Executable:       $ExePath" -ForegroundColor Green

    Write-Host "==========================================================" -ForegroundColor Cyan

} else {

    Write-Error "Expected executable not found at $ExePath"

}

