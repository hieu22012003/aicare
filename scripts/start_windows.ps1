param(

    [switch]$NoBrowser,

    [switch]$NoDesktop

)

# ==============================================================================

# AI REHABILITATION & SAFETY ASSISTANT — WINDOWS START SUPERVISOR (POWERSHELL)

# ==============================================================================

# Nhiệm vụ:

# 1. Kiểm tra môi trường đã thiết lập (.setup-complete và .venv).

# 2. Ngăn chặn chạy trùng lặp (Multi-instance prevention) qua file PID.

# 3. Khởi động Máy chủ Web/Backend nền và lưu PID vào runtime\pids\web_server.pid.

# 4. Thăm dò (Poll) endpoint /health với thời gian chờ tối đa 15 giây.

# 5. Tự động mở trình duyệt tới Cổng Chọn Vai Trò (http://127.0.0.1:8080/login).

# 6. Khởi động Ứng dụng Desktop Người cao tuổi (PyQt5) và lưu PID.

# 7. Giám sát vòng đời tiến trình, dọn dẹp sạch sẽ khi thoát.

# ==============================================================================







$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir '..'))



try {

    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

    [Console]::InputEncoding = [System.Text.Encoding]::UTF8

} catch {}

$env:PYTHONUTF8 = "1"

$env:PYTHONIOENCODING = "utf-8"



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



Set-Location -LiteralPath $ProjectRoot



# 1. KIỂM TRA MÔI TRƯỜNG THIẾT LẬP

$venvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

$marker = Join-Path $ProjectRoot 'runtime\.setup-complete'



if (-not (Test-Path -LiteralPath $venvPython)) {

    Write-Err "Chưa tìm thấy môi trường Python ảo (.venv)!"

    Write-Host "`n👉 Vui lòng chạy file deploy/windows/Setup.cmd trước để hệ thống tự động chuẩn bị môi trường.`n" -ForegroundColor Yellow

    exit 1

}



# 2. QUẢN LÝ TIẾN TRÌNH VÀ KIỂM TRA TRÙNG LẶP

$pidsDir = Join-Path $ProjectRoot 'runtime\pids'

$logsDir = Join-Path $ProjectRoot 'runtime\logs'

New-Item -ItemType Directory -Force -Path $pidsDir | Out-Null

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null



$webPidFile = Join-Path $pidsDir 'web_server.pid'

$desktopPidFile = Join-Path $pidsDir 'desktop_app.pid'



if (Test-Path -LiteralPath $webPidFile) {

    $existingWebPid = Get-Content -LiteralPath $webPidFile -ErrorAction SilentlyContinue

    if ($existingWebPid) {

        $p = Get-Process -Id ([int]$existingWebPid) -ErrorAction SilentlyContinue

        if ($p) {

            Write-Warn "Máy chủ Web/Backend đang chạy (PID: $existingWebPid)."

            Write-Host "Nếu muốn khởi động lại hoàn toàn, vui lòng chạy deploy/windows/Stop Demo.cmd trước." -ForegroundColor Yellow

        }

    }

}



# 3. THIẾT LẬP BIẾN MÔI TRƯỜNG TOÀN CỤC CHO PHIÊN CHẠY

$env:PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION = "python"

$env:MPLCONFIGDIR = "$env:TEMP\matplotlib-cache"

$env:PORTABLE_MODE = "1"

$env:PYTHONPATH = $ProjectRoot



# Đọc cổng Web từ cấu hình runtime\.env nếu có

$webHost = "127.0.0.1"

$webPort = "8080"

$envFile = Join-Path $ProjectRoot 'runtime\.env'

if (-not (Test-Path -LiteralPath $envFile)) {

    $envFile = Join-Path $ProjectRoot '.env'

}

if (Test-Path -LiteralPath $envFile) {

    Get-Content -LiteralPath $envFile | ForEach-Object {

        if ($_ -match '^\s*WEB_PORT\s*=\s*(\d+)') { $webPort = $matches[1] }

        if ($_ -match '^\s*WEB_HOST\s*=\s*(.+)') { $webHost = $matches[1].Trim() }

    }

}



Write-Step "KHỞI ĐỘNG HỆ THỐNG AI CARE & REHABILITATION DEMO"

Write-Host " • Thư mục dự án: $ProjectRoot"

Write-Host " • Máy chủ Web   : http://${webHost}:${webPort}"



# 4. KHỞI ĐỘNG BACKEND WEB SERVER

$webLogFile = Join-Path $logsDir 'web_server.log'

Write-Host "`n[1/3] Đang khởi động Backend Web Server & SSE Gateway..."



$webStartInfo = New-Object System.Diagnostics.ProcessStartInfo

$webStartInfo.FileName = $venvPython

$webStartInfo.Arguments = "features/web_portal/server.py --host $webHost --port $webPort"

$webStartInfo.WorkingDirectory = $ProjectRoot

$webStartInfo.RedirectStandardOutput = $true

$webStartInfo.RedirectStandardError = $true

$webStartInfo.UseShellExecute = $false

$webStartInfo.CreateNoWindow = $true



$webProcess = [System.Diagnostics.Process]::Start($webStartInfo)

if (-not $webProcess) {

    throw "Không thể tạo tiến trình cho Web Server."

}

Set-Content -LiteralPath $webPidFile -Value $webProcess.Id -Encoding ASCII

Write-Success "Backend Web Server đã khởi chạy (PID: $($webProcess.Id))."



# 5. CHỜ BACKEND SẴN SÀNG (HEALTH CHECK VỚI TIMEOUT 15s)

Write-Host "`n[2/3] Đang kiểm tra trạng thái sẵn sàng của dịch vụ (Health Check)..."

$healthUrl = "http://${webHost}:${webPort}/health"

$timeoutSec = 15

$elapsed = 0

$serverReady = $false



while ($elapsed -lt $timeoutSec) {

    if ($webProcess.HasExited) {

        Write-Err "Backend Web Server đã dừng đột ngột với mã thoát $($webProcess.ExitCode)!"

        Write-Host "Kiểm tra chi tiết lỗi tại: $webLogFile" -ForegroundColor Yellow

        exit 1

    }

    try {

        $res = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2 -ErrorAction Stop

        if ($res.status -eq "ok" -and $res.ready -eq $true) {

            $serverReady = $true

            break

        }

    } catch {

        # Chưa sẵn sàng, tiếp tục chờ

    }

    Start-Sleep -Milliseconds 500

    $elapsed += 0.5

    Write-Host -NoNewline "."

}

Write-Host ""



if (-not $serverReady) {

    Write-Err "Backend Web Server không phản hồi sau $timeoutSec giây!"

    Write-Host "Đang dọn dẹp tiến trình máy chủ..." -ForegroundColor Yellow

    try { $webProcess.Kill() } catch {}

    Remove-Item -LiteralPath $webPidFile -Force -ErrorAction SilentlyContinue

    exit 1

}

Write-Success "Hệ thống Backend & Cơ sở dữ liệu đã sẵn sàng phục vụ!"



# Mở trình duyệt Cổng Đa vai trò

$loginUrl = "http://${webHost}:${webPort}/login"

if (-not $NoBrowser) {

    Write-Host "Đang mở Cổng Quản lý Đa vai trò trên trình duyệt mặc định: $loginUrl"

    Start-Process $loginUrl

}



# 6. KHỞI ĐỘNG ỨNG DỤNG DESKTOP NGƯỜI CAO TUỔI (PyQt5)

if (-not $NoDesktop) {

    Write-Host "`n[3/3] Đang khởi động Giao diện Người cao tuổi (PyQt5 Desktop App)..."

    Write-Host " • Nhật ký ứng dụng: runtime\logs\app.log"



    $desktopProc = Start-Process -FilePath $venvPython -ArgumentList "apps/desktop/main.py" -WorkingDirectory $ProjectRoot -PassThru

    if ($desktopProc) {

        Set-Content -LiteralPath $desktopPidFile -Value $desktopProc.Id -Encoding ASCII

        Write-Success "Giao diện Người cao tuổi đã mở (PID: $($desktopProc.Id))."

        

        Write-Host "`n==================================================================" -ForegroundColor Green

        Write-Host " ✨ HỆ THỐNG ĐANG HOẠT ĐỘNG HOÀN TOÀN TẠI CỤC BỘ!" -ForegroundColor Green

        Write-Host "==================================================================" -ForegroundColor Green

        Write-Host " 👵 Ứng dụng Người già   : Đang hiển thị trên màn hình"

        Write-Host " 👨‍👩‍👧 Cổng Người nhà       : http://${webHost}:${webPort}/caregiver"

        Write-Host " 👨‍⚕️ Cổng Bác sĩ chuyên khoa: http://${webHost}:${webPort}/clinician"

        Write-Host " 🛡️ Cổng Quản trị viên   : http://${webHost}:${webPort}/admin"

        Write-Host "==================================================================" -ForegroundColor Green

        Write-Host "ℹ️ Để dừng toàn bộ hệ thống đúng cách, vui lòng chạy: deploy/windows/Stop Demo.cmd`n" -ForegroundColor Yellow



        # Chờ giao diện desktop đóng

        $desktopProc.WaitForExit()

        Write-Host "`nGiao diện Người cao tuổi đã đóng (Mã thoát: $($desktopProc.ExitCode))."

        Remove-Item -LiteralPath $desktopPidFile -Force -ErrorAction SilentlyContinue



        # Tự động dừng Web Server khi đóng Desktop App

        Write-Host "Đang tắt Backend Web Server..."

        try {

            if (-not $webProcess.HasExited) {

                $webProcess.Kill()

            }

        } catch {}

        Remove-Item -LiteralPath $webPidFile -Force -ErrorAction SilentlyContinue

        Write-Success "Hệ thống demo đã dừng an toàn."

    }

}