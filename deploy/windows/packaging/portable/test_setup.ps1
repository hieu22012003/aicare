param([Parameter(Mandatory=$true)][string]$Python)

$ErrorActionPreference = 'Stop'

$script:RealPython = $Python

. (Join-Path $PSScriptRoot 'setup.ps1') -LibraryOnly

$fixture = Join-Path ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))) ('.cache\packaging\setup tests ' + [Guid]::NewGuid().ToString('N'))

New-Item -ItemType Directory -Path $fixture -Force | Out-Null

function Assert($condition, $message) { if (-not $condition) { throw $message } }



$fake = Join-Path $fixture 'broken-launcher.cmd'

Set-Content $fake "@echo off`r`necho [ERROR] No runtime installed that matches 3.12.`r`nexit /b 0" -Encoding ASCII

Assert (-not (Test-PythonRuntime $fake)) 'Launcher with exit 0 and no interpreter was accepted.'

Write-Host 'PASS: misleading launcher exit 0 rejected'



Set-Content $fake "@echo off`r`nexit /b 1" -Encoding ASCII

Assert (-not (Test-PythonRuntime $fake)) 'Failed launcher was accepted.'

Write-Host 'PASS: failed launcher rejected'

Assert (-not (Test-PythonRuntime (Join-Path $fixture 'missing.exe'))) 'Missing Python accepted.'

Assert ([bool](Test-PythonRuntime $Python)) 'Real Python rejected.'

Write-Host 'PASS: real interpreter verified; missing executable rejected'



$failedRoot = Join-Path $fixture 'failed creation'

New-Item -ItemType Directory -Path $failedRoot | Out-Null

Set-Content $fake "@echo off`r`nexit /b 0" -Encoding ASCII

$failed = $false

try { Initialize-Environment $failedRoot $fake | Out-Null } catch { $failed = $true }

Assert $failed 'Creation reported success without producing a valid environment.'

Write-Host 'PASS: missing venv after exit 0 stops setup'



$venvPython = Initialize-Environment $fixture $Python

Assert ([bool](Test-PythonRuntime $venvPython)) 'New environment is invalid.'

Assert ((Initialize-Environment $fixture $Python) -eq $venvPython) 'Repeated setup did not reuse environment.'

Write-Host 'PASS: real venv creation in path with spaces; repeated setup'



# Exercise the entire no-runtime branch without repeating a Windows installation.

$script:installCalled = $false

$script:commands = @()

function Find-PythonRuntime { param($Root) return $null }

function Install-CompatiblePython { $script:installCalled = $true; return $script:RealPython }

function Invoke-PythonChecked { param($Python, $Arguments) $script:commands += ,$Arguments }

New-Item -ItemType Directory -Path (Join-Path $fixture 'runtime') -Force | Out-Null

Set-Content (Join-Path $fixture '.env.example') 'EMAIL_ENABLED=false'

Invoke-Setup $fixture

Assert $script:installCalled 'Missing runtime did not trigger installation.'

Assert (Test-Path (Join-Path $fixture 'runtime\.setup-complete')) 'Successful setup did not write marker.'

Assert ($script:commands.Count -eq 3) 'Expected pip install, pose model preparation and application check.'

Write-Host 'PASS: absent runtime triggers installer and resumes setup'



$script:commands = @()

function Initialize-Environment { throw 'simulated venv creation failure' }

$failed = $false

try { Invoke-Setup $fixture } catch { $failed = $true }

Assert $failed 'Setup ignored environment creation failure.'

Assert ($script:commands.Count -eq 0) 'Pip/check ran after environment creation failure.'

Write-Host 'PASS: environment creation failure prevents pip and application checks'

Write-Host 'ALL SETUP REGRESSION CHECKS PASSED'

