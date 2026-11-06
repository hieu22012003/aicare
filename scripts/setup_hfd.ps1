# Explicit developer setup; application inference never downloads dependencies.

$ErrorActionPreference = 'Stop'

$taskRoot = Split-Path -Parent $PSScriptRoot

Set-Location -LiteralPath $taskRoot

& uv --cache-dir .cache/uv python install 3.9 --install-dir .cache/hfd-python

if ($LASTEXITCODE -ne 0) { throw 'Python install failed' }

if (-not (Test-Path -LiteralPath '.cache/hfd-py39/Scripts/python.exe')) {

    & uv --cache-dir .cache/uv venv .cache/hfd-py39 --python .cache/hfd-python/cpython-3.9-windows-x86_64-none/python.exe

    if ($LASTEXITCODE -ne 0) { throw 'Worker venv setup failed' }

}

& uv --cache-dir .cache/uv pip install --python .cache/hfd-py39/Scripts/python.exe -r requirements-fall-hfd-worker.txt --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match

if ($LASTEXITCODE -ne 0) { throw 'Worker dependencies failed' }

$taskModels = Join-Path $taskRoot 'features/fall_risk/hfd_model/models'

New-Item -ItemType Directory -Force -Path $taskModels | Out-Null

$taskCheckpoint = Join-Path $taskModels 'shufflenetv2k16.pkl'

if (-not (Test-Path -LiteralPath $taskCheckpoint)) {

    Invoke-WebRequest -Uri 'https://github.com/vita-epfl/openpifpaf-torchhub/releases/download/v0.12.6/shufflenetv2k16-210404-110105-cocokp-o10s-f90ed364.pkl' -OutFile $taskCheckpoint

}

$taskHash = (Get-FileHash -LiteralPath $taskCheckpoint -Algorithm SHA256).Hash.ToLowerInvariant()

if ($taskHash -ne 'f90ed364912c3295c93e9e8c9054652ea9fdcc669accc73db65258c87237c7f5') { throw 'OpenPifPaf checkpoint hash mismatch' }

foreach ($taskSize in @('513x289','257x193')) {

    $taskExport = Join-Path $taskModels "shufflenetv2k16-$taskSize.onnx"

    if (-not (Test-Path -LiteralPath $taskExport)) {

        & .cache/hfd-py39/Scripts/python.exe scripts/export_hfd_onnx.py --size $taskSize

        if ($LASTEXITCODE -ne 0) { throw 'ONNX export failed' }

    }

    & .cache/hfd-py39/Scripts/python.exe scripts/verify_hfd_onnx.py --size $taskSize --output ".cache/hfd-onnx-parity-$taskSize.json"

    if ($LASTEXITCODE -ne 0) { throw 'ONNX parity failed' }

}

Write-Output 'HFD worker ready. Select HumanFallDetection in video mode or set FALL_POSE_BACKEND=hfd.'

