# ==============================================================================
# AI REHABILITATION & SAFETY ASSISTANT — DISK CLEANUP UTILITY
# ==============================================================================
# Kịch bản dọn dẹp các file rác, bộ nhớ đệm (cache), và môi trường ảo không dùng.
# Giúp giảm dung lượng thư mục dự án từ ~15 GB xuống còn ~2.8 GB một cách an toàn.
# ==============================================================================

param(
    [switch]$KeepReleaseZips,
    [switch]$KeepTestVideos,
    [switch]$Force
)

$ErrorActionPreference = 'SilentlyContinue'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir '..'))

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " 🧹 CÔNG CỤ DỌN DẸP BỘ NHỚ — AI REHABILITATION & SAFETY ASSISTANT" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

function Remove-SafeItem {
    param(
        [string]$Path,
        [string]$Description
    )
    if (Test-Path -LiteralPath $Path) {
        try {
            $item = Get-Item -LiteralPath $Path -Force
            $sizeMB = 0
            if ($item.PSIsContainer) {
                $sizeMB = [math]::Round(((Get-ChildItem -LiteralPath $Path -Recurse -Force | Measure-Object -Property Length -Sum).Sum / 1MB), 2)
                Remove-Item -LiteralPath $Path -Recurse -Force
            } else {
                $sizeMB = [math]::Round(($item.Length / 1MB), 2)
                Remove-Item -LiteralPath $Path -Force
            }
            Write-Host " [ĐÃ XÓA] $Description ($sizeMB MB)" -ForegroundColor Green
            return $sizeMB
        } catch {
            Write-Host " [BỎ QUA] Không thể xóa $Path: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    return 0
}

Set-Location -LiteralPath $ProjectRoot
$totalFreedMB = 0

Write-Host "`n1. Đang dọn dẹp các thư mục không cần thiết trên Windows..." -ForegroundColor Yellow

# 1. Thư mục .venv-linux (Môi trường ảo Linux sót lại, ~6.4 GB)
$totalFreedMB += Remove-SafeItem (Join-Path $ProjectRoot '.venv-linux') "Môi trường Linux thừa (.venv-linux)"

# 2. Thư mục .cache nội bộ (~4.9 GB)
$totalFreedMB += Remove-SafeItem (Join-Path $ProjectRoot '.cache') "Thư mục cache cài đặt (.cache)"

# 3. Thư mục tạm khác
$totalFreedMB += Remove-SafeItem (Join-Path $ProjectRoot 'temp_dist') "Thư mục đóng gói tạm (temp_dist)"
$totalFreedMB += Remove-SafeItem (Join-Path $ProjectRoot 'scratch') "Thư mục nháp (scratch)"

# 4. Bytecode cache (__pycache__)
Write-Host "`n2. Đang dọn dẹp cache Python bytecode (__pycache__)..." -ForegroundColor Yellow
$pycaches = Get-ChildItem -LiteralPath $ProjectRoot -Filter '__pycache__' -Directory -Recurse -Force
$pycacheFreed = 0
foreach ($dir in $pycaches) {
    if ($dir.FullName -notmatch '\\.venv\\') { # Giữ nguyên cấu trúc venv
        try {
            $s = (Get-ChildItem -LiteralPath $dir.FullName -Recurse -Force | Measure-Object -Property Length -Sum).Sum / 1MB
            Remove-Item -LiteralPath $dir.FullName -Recurse -Force
            $pycacheFreed += $s
        } catch {}
    }
}
$pycacheFreedRound = [math]::Round($pycacheFreed, 2)
$totalFreedMB += $pycacheFreedRound
Write-Host " [ĐÃ XÓA] Đã xóa $pycacheFreedRound MB cache bytecode." -ForegroundColor Green

# 5. Dọn dẹp bản zip cũ trong release/
if (-not $KeepReleaseZips) {
    Write-Host "`n3. Dọn dẹp các bản zip cũ trong release/ (Giữ lại bản mới nhất AICareDemo-v04.zip)..." -ForegroundColor Yellow
    $oldZips = @(
        (Join-Path $ProjectRoot 'release\AICareDemo-v3.0.0-windows-x64.zip'),
        (Join-Path $ProjectRoot 'release\AICareDemo-v4.0.0-windows-x64.zip')
    )
    foreach ($zip in $oldZips) {
        $totalFreedMB += Remove-SafeItem $zip "File nén trùng lặp ($(Split-Path -Leaf $zip))"
    }
}

# 6. Dọn dẹp pip cache
$venvPip = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPip) {
    Write-Host "`n4. Dọn dẹp Pip cache..." -ForegroundColor Yellow
    try {
        & $venvPip -m pip cache purge | Out-Null
        Write-Host " [ĐÃ XÓA] Đã dọn dẹp bộ nhớ đệm pip cache thành công." -ForegroundColor Green
    } catch {}
}

Write-Host "`n==================================================================" -ForegroundColor Cyan
$freedGB = [math]::Round($totalFreedMB / 1024, 2)
Write-Host " 🎉 HOÀN TẤT DỌN DẸP! ĐÃ GIẢI PHÓNG KHOẢNG $freedGB GB BỘ NHỚ." -ForegroundColor Green
Write-Host " Các file cốt lõi (.venv, models, code, database) được bảo toàn 100%." -ForegroundColor Green
Write-Host " Bạn vẫn có thể khởi động bình thường bằng 'deploy/windows/Start Demo.cmd'." -ForegroundColor Yellow
Write-Host "==================================================================" -ForegroundColor Cyan
