param([Parameter(Mandatory=$true)][string]$Package,
      [string]$Results = (Join-Path $env:LOCALAPPDATA ('AICareCleanTest\'+[guid]::NewGuid().ToString('N'))))
$ErrorActionPreference = 'Stop'
$Package = (Resolve-Path -LiteralPath $Package).Path
New-Item -ItemType Directory -Force -Path $Results | Out-Null
$Results = (Resolve-Path -LiteralPath $Results).Path
$env:TEMP = Join-Path $Results 'temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$env:PATH = (Join-Path $Package 'runtime')+';'+$env:SystemRoot+'\System32;'+$env:SystemRoot
$transcript = Join-Path $Results 'console.txt'
Start-Transcript -LiteralPath $transcript
try {
    Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,OSArchitecture |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Results 'os.json') -Encoding UTF8
    & (Join-Path $Package 'runtime\python.exe') -I -X utf8 (Join-Path $Package 'qa\acceptance.py') $Package --home (Join-Path $Results 'home') --report (Join-Path $Results 'QA_REPORT.json')
    if ($LASTEXITCODE -ne 0) { throw 'Portable acceptance failed; see console.txt' }
    Write-Host "PASS: $Results"
} finally { Stop-Transcript }
