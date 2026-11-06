param([switch]$LibraryOnly)

$ErrorActionPreference = 'Stop'



function Test-PythonRuntime {

    param([string]$Executable, [string[]]$Prefix = @())

    # A zero exit code alone is insufficient: Windows aliases can report success

    # without executing Python. Require output produced by the interpreter.

    $probe = "import sys,os; assert sys.version_info[:2] in ((3,11),(3,12)) and sys.maxsize>2**32; print('AI_PYTHON_OK='+os.path.realpath(sys.executable))"

    try {

        $lines = @(& $Executable @Prefix -c $probe 2>$null)

        if ($LASTEXITCODE -ne 0) { return $null }

        foreach ($line in $lines) {

            if ("$line".StartsWith('AI_PYTHON_OK=')) {

                $path = "$line".Substring(13)

                if ([IO.Path]::IsPathRooted($path) -and (Test-Path -LiteralPath $path -PathType Leaf)) {

                    return $path

                }

            }

        }

    } catch { }

    return $null

}



function Find-PythonRuntime {

    param([string]$Root)

    $candidates = @(

        (Join-Path $Root '.venv\Scripts\python.exe'),

        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",

        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"

    )

    foreach ($base in @("$env:APPDATA\uv\python", "$env:LOCALAPPDATA\Python")) {

        if (Test-Path -LiteralPath $base) {

            $candidates += @(Get-ChildItem -LiteralPath $base -Directory |

                ForEach-Object { Join-Path $_.FullName 'python.exe' })

        }

    }

    foreach ($candidate in $candidates) {

        if (Test-Path -LiteralPath $candidate) {

            $found = Test-PythonRuntime $candidate

            if ($found) { return $found }

        }

    }

    foreach ($name in @('python', 'python3', 'py')) {

        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue

        if (-not $command) { continue }

        if ($name -eq 'py') {

            foreach ($version in @('-3.12', '-3.11')) {

                $found = Test-PythonRuntime $command.Source @($version)

                if ($found) { return $found }

            }

        } else {

            $found = Test-PythonRuntime $command.Source

            if ($found) { return $found }

        }

    }

    return $null

}



function Invoke-PythonChecked {

    param([string]$Python, [string[]]$Arguments)

    & $Python @Arguments | Out-Host

    if ($LASTEXITCODE -ne 0) {

        throw "Python command failed (exit $LASTEXITCODE): $($Arguments -join ' ')"

    }

}



function Initialize-Environment {

    param([string]$Root, [string]$Python)

    $venv = Join-Path $Root '.venv'

    $venvPython = Join-Path $venv 'Scripts\python.exe'

    if (-not (Test-PythonRuntime $venvPython)) {

        # Preserve a broken/copied environment for diagnosis; do not delete user data.

        if (Test-Path -LiteralPath $venv) {

            $backup = Join-Path $Root ('.venv.previous-' + [Guid]::NewGuid().ToString('N'))

            Move-Item -LiteralPath $venv -Destination $backup

        }

        Invoke-PythonChecked $Python @('-m', 'venv', $venv)

    }

    $verified = Test-PythonRuntime $venvPython

    if (-not $verified) { throw 'Virtual environment was not created correctly. Dependencies will not be installed.' }

    return $verified

}



function Install-CompatiblePython {

    & (Join-Path $PSScriptRoot 'install_python.ps1')

    return Test-PythonRuntime "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"

}



function Invoke-Setup {

    param([string]$Root)

    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows 64-bit is required.' }

    Write-Host 'AI Rehabilitation Assistant - SETUP v3 (2026-10-03)'

    Write-Host '[1/5] Checking for a working Python 3.11/3.12 64-bit runtime...'

    $python = Find-PythonRuntime $Root

    if (-not $python) {

        Write-Host 'No working compatible Python found. Installing Python 3.12.10 64-bit...'

        $python = Install-CompatiblePython

        if (-not $python) { throw 'Python installation did not produce a working runtime.' }

    }

    # Resolve again using the real executable, never use the launcher to create venv.

    $python = Test-PythonRuntime $python

    if (-not $python) { throw 'Selected Python executable failed verification.' }

    Write-Host "Python: $python"

    Write-Host '[2/5] Creating/checking the local environment...'

    $venvPython = Initialize-Environment $Root $python

    Write-Host '[3/5] Installing libraries (Internet required)...'

    Invoke-PythonChecked $venvPython @('-m', 'pip', 'install', '-r', (Join-Path $Root 'requirements-windows.txt'))

    Write-Host '[4/5] Preparing configuration...'

    $configPath = Join-Path $Root 'runtime\.env'

    if (-not (Test-Path -LiteralPath $configPath)) {

        Copy-Item -LiteralPath (Join-Path $Root '.env.example') -Destination $configPath

    }

    Write-Host '[5/5] Checking the application...'

    $env:PORTABLE_MODE = '1'

    # MediaPipe does not ship the lite pose model in every wheel. Fetch and

    # validate it during SETUP so the first START does not require a download.

    Write-Host 'Preparing the rehabilitation pose model...'

    Write-Host 'Verifying SQLite3 and initializing unified care database...'

    try {

        & $venvPython -c "import sqlite3" 2>$null

        if ($LASTEXITCODE -ne 0) {

            Write-Host 'Standard sqlite3 unavailable, installing apsw fallback...'

            & $venvPython -m pip install apsw

        }

    } catch { }

    Invoke-PythonChecked $venvPython @('-c', 'from database.connection import UnifiedDatabaseManager; from database.migrator import Migrator; Migrator(UnifiedDatabaseManager()).apply_all()')

    Invoke-PythonChecked $venvPython @((Join-Path $Root 'scripts/check_system.py'))

    Set-Content -LiteralPath (Join-Path $Root 'runtime\.setup-complete') -Value 'setup-v3' -Encoding ASCII

    Write-Host 'Setup complete. Run START.bat.'

}



if (-not $LibraryOnly) {

    $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))

    $transcript = $false

    $result = 1

    try {

        Set-Location -LiteralPath $root

        New-Item -ItemType Directory -Force -Path (Join-Path $root 'runtime\logs') | Out-Null

        Start-Transcript -Path (Join-Path $root 'runtime\logs\setup.log') -Append | Out-Null

        $transcript = $true

        $marker = Join-Path $root 'runtime\.setup-complete'

        if (Test-Path -LiteralPath $marker) { Remove-Item -LiteralPath $marker }

        Invoke-Setup $root

        $result = 0

    } catch {

        Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red

        Write-Host 'Setup stopped. Fix the reported error and run SETUP.bat again.'

    } finally {

        if ($transcript) { Stop-Transcript | Out-Null }

    }

    exit $result

}

