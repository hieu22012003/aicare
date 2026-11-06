param(
    [switch]$SkipModelDownload,
    [switch]$CoreOnly
)

# ==============================================================================
# AI REHABILITATION & SAFETY ASSISTANT — WINDOWS SETUP V3 (POWERSHELL)
# ==============================================================================
# Nhiệm vụ:
# 1. Kiểm tra môi trường Windows 64-bit và dung lượng ổ cứng.
# 2. Tìm kiếm hoặc tự động cài đặt Python 3.12 64-bit riêng biệt cho người dùng.
# 3. Tạo môi trường ảo .venv nội bộ và cài đặt các thư viện CPU-only.
# 4. Tải các mô hình AI cần thiết (Vision + Local Speech) và kiểm tra mã băm SHA-256.
# 5. Khởi tạo cấu hình runtime\.env và cơ sở dữ liệu mẫu SQLite 3NF.
# 6. Kiểm tra toàn diện hệ thống trước khi sẵn sàng khởi chạy.
# ==============================================================================

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir '..'))

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding = [System.Text.Encoding]::UTF8
} catch {}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Write-Step {
    param([string]$Title)
    Write-Host "`n==================================================================" -ForegroundColor Cyan
    Write-Host " 🚀 $Title" -ForegroundColor Cyan
    Write-Host "==================================================================" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Msg)
    Write-Host " ✅ $Msg" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Msg)
    Write-Host " ⚠️ $Msg" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Msg)
    Write-Host " ❌ $Msg" -ForegroundColor Red
}

function Test-PythonInterpreter {
    param([string]$ExePath)
    if (-not $ExePath) { return $null }
    if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) { return $null }
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $ExePath
        $psi.Arguments = '-c "import sys,os; assert sys.version_info[:2] in ((3,11),(3,12)) and sys.maxsize > 2**32; print(''PYTHON_VALID='' + os.path.realpath(sys.executable))"'
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true

        $p = [System.Diagnostics.Process]::Start($psi)
        if (-not $p) { return $null }
        $stdout = $p.StandardOutput.ReadToEnd()
        [void]$p.WaitForExit(5000)
        if ($p.ExitCode -eq 0 -and $stdout) {
            foreach ($line in $stdout.Split("`n")) {
                $trimmed = $line.Trim()
                if ($trimmed.StartsWith('PYTHON_VALID=')) {
                    $resPath = $trimmed.Substring(13)
                    if (Test-Path -LiteralPath $resPath -PathType Leaf) {
                        return [string]$resPath
                    }
                }
            }
        }
    } catch { }
    return $null
}

function Find-UsablePython {
    param([string]$Root)
    # 1. Check existing .venv
    $venvPy = Join-Path $Root '.venv\Scripts\python.exe'
    $valid = Test-PythonInterpreter $venvPy
    if ($valid) { return $valid }

    # 2. Check user-installed Python
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )
    foreach ($c in $candidates) {
        $valid = Test-PythonInterpreter $c
        if ($valid) { return $valid }
    }

    # 3. Check system PATH
    foreach ($cmd in @('python', 'python3')) {
        $found = Get-Command $cmd -CommandType Application -ErrorAction SilentlyContinue
        if ($found -and $found.Source) {
            $valid = Test-PythonInterpreter $found.Source
            if ($valid) { return $valid }
        }
    }

    # 4. Check py launcher safely with ProcessStartInfo
    $pyLauncher = Get-Command 'py' -CommandType Application -ErrorAction SilentlyContinue
    if ($pyLauncher -and $pyLauncher.Source) {
        foreach ($ver in @('-3.12', '-3.11')) {
            try {
                $psi = New-Object System.Diagnostics.ProcessStartInfo
                $psi.FileName = $pyLauncher.Source
                $psi.Arguments = "$ver -c 'import sys,os; print(os.path.realpath(sys.executable))'"
                $psi.UseShellExecute = $false
                $psi.RedirectStandardOutput = $true
                $psi.RedirectStandardError = $true
                $psi.CreateNoWindow = $true

                $p = [System.Diagnostics.Process]::Start($psi)
                if ($p) {
                    $stdout = $p.StandardOutput.ReadToEnd().Trim()
                    [void]$p.WaitForExit(5000)
                    if ($p.ExitCode -eq 0 -and $stdout) {
                        $valid = Test-PythonInterpreter $stdout
                        if ($valid) { return [string]$valid }
                    }
                }
            } catch {}
        }
    }
    return $null
}

try {
    Set-Location -LiteralPath $ProjectRoot
    $runtimeLogs = Join-Path $ProjectRoot 'runtime\logs'
    New-Item -ItemType Directory -Force -Path $runtimeLogs | Out-Null
    Start-Transcript -Path (Join-Path $runtimeLogs 'setup.log') -Append | Out-Null

    Write-Step "[BƯỚC 1/6] KIỂM TRA MÔI TRƯỜNG WINDOWS"
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "Hệ điều hành Windows 64-bit là bắt buộc. Hệ điều hành hiện tại không được hỗ trợ."
    }
    Write-Success "Hệ điều hành: Windows 64-bit [HỢP LỆ]"

    # Kiểm tra dung lượng ổ đĩa
    $drive = (Get-Item $ProjectRoot).PSDrive
    if ($drive -and $drive.Free) {
        $freeGB = [math]::Round($drive.Free / 1GB, 2)
        Write-Host " • Dung lượng trống trên ổ $($drive.Name): $freeGB GB"
        if ($freeGB -lt 1.5) {
            Write-Warn "Dung lượng ổ đĩa còn lại dưới 1.5 GB. Khuyến nghị giải phóng thêm dung lượng để cài đặt thư viện AI."
        }
    }

    Write-Step "[BƯỚC 2/6] CHUẨN BỊ PYTHON 3.12 (64-BIT)"
    $pythonExe = Find-UsablePython $ProjectRoot
    if (-not $pythonExe -or -not (Test-Path -LiteralPath "$pythonExe" -PathType Leaf)) {
        Write-Host "Không tìm thấy Python 3.11 hoặc 3.12 64-bit phù hợp. Đang tự động tải và cài đặt Python 3.12.10 riêng biệt..."
        $installScript = Join-Path $ScriptDir 'install_python.ps1'
        if (-not (Test-Path -LiteralPath $installScript)) {
            $installScript = Join-Path $ProjectRoot 'deploy\windows\packaging\portable\install_python.ps1'
        }
        if (Test-Path -LiteralPath $installScript) {
            & $installScript
        } else {
            throw "Không tìm thấy script cài đặt Python: $installScript"
        }
        $pythonExe = Find-UsablePython $ProjectRoot
        if (-not $pythonExe -or -not (Test-Path -LiteralPath "$pythonExe" -PathType Leaf)) {
            throw "Quá trình cài đặt Python không tạo được interpreter tương thích."
        }
    }
    Write-Success "Python Interpreter đã xác minh: $pythonExe"

    Write-Step "[BƯỚC 3/6] KHỞI TẠO MÔI TRƯỜNG ẢO (.VENV) & CÀI ĐẶT THƯ VIỆN"
    $venvDir = Join-Path $ProjectRoot '.venv'
    $venvPython = Join-Path $venvDir 'Scripts\python.exe'
    
    if (-not (Test-PythonInterpreter $venvPython)) {
        Write-Host "Đang tạo môi trường ảo tại $venvDir ..."
        & $pythonExe -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "Tạo môi trường ảo .venv thất bại." }
    }
    Write-Success "Môi trường ảo .venv sẵn sàng."

    Write-Host "Đang cài đặt các thư viện cần thiết từ requirements-windows.txt (Cần kết nối mạng)..."
    try { & $venvPython -m pip install --upgrade pip --quiet 2>$null } catch {}
    $reqFile = Join-Path $ProjectRoot 'requirements-windows.txt'
    & $venvPython -m pip install -r $reqFile
    if ($LASTEXITCODE -ne 0) {
        throw "Cài đặt thư viện thất bại. Vui lòng kiểm tra lại kết nối Internet và thử lại."
    }
    Write-Success "Toàn bộ thư viện đã được cài đặt thành công."

    Write-Step "[BƯỚC 4/6] KHỞI TẠO CẤU HÌNH & TẢI MÔ HÌNH AI"
    & $venvPython (Join-Path $ProjectRoot 'shared\config_generator.py')
    if ($LASTEXITCODE -ne 0) { throw "Khởi tạo tệp cấu hình thất bại." }

    if (-not $SkipModelDownload) {
        Write-Host "Đang kiểm tra và tải các mô hình AI (Kiểm tra mã băm SHA-256)..."
        $downloadArgs = @((Join-Path $ProjectRoot 'scripts\download_models.py'))
        if ($CoreOnly) { $downloadArgs += '--core-only' }
        & $venvPython @downloadArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Một số mô hình chưa tải xong. Xem runtime\logs\model_download.log; chạy lại deploy/windows/Setup.cmd để hoàn tất."
        } else {
            Write-Success "Toàn bộ mô hình AI đã sẵn sàng và được xác minh nguyên vẹn."
        }
    }

    Write-Step "[BƯỚC 5/6] KHỞI TẠO CƠ SỞ DỮ LIỆU CHUẨN 3NF & TÀI KHOẢN QUẢN TRỊ VIÊN"
    $initDbCmd = "from database.connection import UnifiedDatabaseManager; from database.migrator import Migrator; from database.importer import DataImporter; db = UnifiedDatabaseManager(); Migrator(db).apply_all(); DataImporter(db).import_all(seed_sample_data=False); print('Database migrations and admin account initialized successfully.')"
    & $venvPython -c $initDbCmd
    if ($LASTEXITCODE -ne 0) {
        throw "Khởi tạo cơ sở dữ liệu thất bại."
    }
    Write-Success "Cơ sở dữ liệu 3NF và tài khoản Quản trị viên (admin / admin123) đã sẵn sàng."

    Write-Step "[BƯỚC 6/6] KIỂM TRA CHẨN ĐOÁN TOÀN DIỆN HỆ THỐNG"
    & $venvPython (Join-Path $ProjectRoot 'scripts/check_system.py')
    if ($LASTEXITCODE -ne 0) { throw 'Kiểm tra hệ thống thất bại; chưa đánh dấu thiết lập hoàn tất.' }
    
    $marker = Join-Path $ProjectRoot 'runtime\.setup-complete'
    Set-Content -LiteralPath $marker -Value 'setup-v3.0' -Encoding ASCII

    Write-Host "`n==================================================================" -ForegroundColor Green
    Write-Host " 🎉 THIẾT LẬP THÀNH CÔNG! BẢN DEMO ĐÃ SẴN SÀNG." -ForegroundColor Green
    Write-Host "==================================================================" -ForegroundColor Green
    Write-Host "👉 Bây giờ bạn có thể khởi động hệ thống bằng lệnh:" -ForegroundColor Yellow
    Write-Host "   deploy/windows/Start Demo.cmd`n" -ForegroundColor Yellow

} catch {
    Write-Err "QUÁ TRÌNH THIẾT LẬP GẶP LỖI:"
    Write-Err $_.Exception.Message
    Write-Host "Chi tiết nhật ký được ghi tại: runtime\logs\setup.log" -ForegroundColor Yellow
    exit 1
} finally {
    Stop-Transcript | Out-Null
}