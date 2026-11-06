param(
    [string]$Version = '6.0.0-nativefix.2',
    [string]$Output = 'releases/portable-nativefix',
    [ValidateSet('PT','HP','both')][string]$Edition = 'both',
    [switch]$AssembleOnly
)
$ErrorActionPreference='Stop'
$Root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
Set-Location -LiteralPath $Root
$Python=Join-Path $Root '.venv\Scripts\python.exe'
$Cache=Join-Path $Root 'runtime\portable-build-cache'
New-Item -ItemType Directory -Path $Cache -Force | Out-Null
$embed=Join-Path $Cache 'python-3.12.10-embed-amd64.zip'
if (-not (Test-Path -LiteralPath $embed)) {
    Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $embed -UseBasicParsing
}
$runtime=Join-Path $Cache 'runtime'
if (-not (Test-Path -LiteralPath (Join-Path $runtime 'python.exe'))) { Expand-Archive -LiteralPath $embed -DestinationPath $runtime }
Set-Content -LiteralPath (Join-Path $runtime 'python312._pth') -Value "python312.zip`n.`nLib/site-packages`n../app`nimport site`n" -Encoding ASCII
# Remove the retired STT runtime before installing the current locked wheels.
$sitePackages=Join-Path $runtime 'Lib\site-packages'
if (Test-Path -LiteralPath $sitePackages) {
    $resolvedSite=[IO.Path]::GetFullPath($sitePackages)
    if (-not $resolvedSite.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid runtime cleanup path' }
    Get-ChildItem -LiteralPath $resolvedSite -Directory | Where-Object { $_.Name -match '^(faster_whisper|ctranslate2)(-|$)' } | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }
}
if (-not $AssembleOnly) {
    & $Python -m pip install --upgrade --target (Join-Path $runtime 'Lib\site-packages') --only-binary=:all: --no-compile --no-deps -r deploy/windows/packaging/windows_portable/requirements-portable.txt --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Portable wheel installation failed' }
}
$installer=Join-Path $Cache 'vc_redist.x64.exe'
if (-not (Test-Path -LiteralPath $installer)) { Invoke-WebRequest 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $installer -UseBasicParsing }
$signature=Get-AuthenticodeSignature -FilePath $installer
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') { throw 'VC installer signature invalid' }
& $Python deploy/windows/packaging/windows_portable/extract_vc_runtime.py $installer (Join-Path $Cache 'vc-runtime')
if ($LASTEXITCODE -ne 0) { throw 'App-local VC runtime extraction failed' }
$sources = @{ python = @{ url='https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'; sha256=(Get-FileHash -LiteralPath $embed -Algorithm SHA256).Hash.ToLower() }; vc_redist = @{ url='https://aka.ms/vs/17/release/vc_redist.x64.exe'; sha256=(Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLower(); authenticode='Valid Microsoft Corporation' } }
$sources | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $Cache 'sources.json') -Encoding UTF8
$csc=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $csc /nologo /target:winexe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll ("/out:"+(Join-Path $Cache 'AICare.exe')) deploy/windows/packaging/windows_portable/Launcher.cs
if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed' }
& $Python deploy/windows/packaging/windows_portable/assemble.py --edition $Edition --output $Output --version $Version
if ($LASTEXITCODE -ne 0) { throw 'Portable assembly failed' }
$editions = if ($Edition -eq 'both') { @('PT','HP') } else { @($Edition) }
foreach ($variant in $editions) {
    $package=Join-Path (Join-Path (Join-Path $Root $Output) $variant) 'AICare'
    & $Python deploy/windows/packaging/windows_portable/qa.py $package
    if ($LASTEXITCODE -ne 0) { throw "Portable QA failed: $variant" }
    & $Python deploy/windows/packaging/windows_portable/archive.py $package
    if ($LASTEXITCODE -ne 0) { throw "Archive failed: $variant" }
}
