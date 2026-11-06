param(
    [string]$TargetDirectory,
    [string]$InstallerPath
)

$ErrorActionPreference = 'Stop'

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding = [System.Text.Encoding]::UTF8
} catch {}

if (-not $TargetDirectory) {
    $TargetDirectory = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312'
}

$version = '3.12.10'
$url = "https://www.python.org/ftp/python/$version/python-$version-amd64.exe"
$target = Join-Path $TargetDirectory 'python.exe'
$downloaded = -not $InstallerPath
$installer = $InstallerPath

if ($downloaded) {
    $installer = Join-Path ([System.IO.Path]::GetTempPath()) ("ai-rehab-python-" + [Guid]::NewGuid().ToString('N') + '.exe')
}

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    if ($downloaded) {
        Write-Host "Downloading Python $version (64-bit) from python.org..."
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing -TimeoutSec 300
    }

    $signature = Get-AuthenticodeSignature -FilePath $installer
    if ($signature.Status -ne 'Valid' -or
        $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw "Python installer signature is invalid or not signed by Python Software Foundation."
    }

    Write-Host 'Installing Python for the current user...'
    $process = Start-Process -FilePath $installer -ArgumentList @(
        '/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_launcher=0',
        'Include_test=0', 'Include_pip=1', ('TargetDir="' + $TargetDirectory + '"')
    ) -Wait -PassThru -WindowStyle Hidden

    if (-not (Test-Path -LiteralPath $target)) {
        Write-Host 'Running repair mode in case of existing registry bundle...'
        $repairProc = Start-Process -FilePath $installer -ArgumentList @(
            '/quiet', '/repair', 'InstallAllUsers=0', 'PrependPath=0', 'Include_launcher=0',
            'Include_test=0', 'Include_pip=1', ('TargetDir="' + $TargetDirectory + '"')
        ) -Wait -PassThru -WindowStyle Hidden
    }

    if (-not (Test-Path -LiteralPath $target)) {
        foreach ($hive in @('HKCU:\Software\Python\PythonCore\3.12\InstallPath', 'HKLM:\Software\Python\PythonCore\3.12\InstallPath')) {
            $reg = Get-ItemProperty $hive -ErrorAction SilentlyContinue
            if ($reg) {
                if ($reg.ExecutablePath -and (Test-Path -LiteralPath $reg.ExecutablePath -PathType Leaf)) {
                    $target = $reg.ExecutablePath
                    break
                }
                if ($reg.'(default)' -and (Test-Path -LiteralPath (Join-Path $reg.'(default)' 'python.exe') -PathType Leaf)) {
                    $target = Join-Path $reg.'(default)' 'python.exe'
                    break
                }
            }
        }
    }

    if (-not (Test-Path -LiteralPath $target)) {
        throw "Installer finished but python.exe was not found at $target."
    }

    & $target -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32 else 1)'
    if ($LASTEXITCODE -ne 0) {
        throw 'Installed Python did not pass the compatibility check.'
    }
    Write-Host "Python ready: $target"
} catch {
    throw
} finally {
    if ($downloaded) {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
}
