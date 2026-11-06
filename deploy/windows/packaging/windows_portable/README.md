# Đóng gói AI Care portable cho Windows

## Bản vá log và phân quyền ngày 01/11/2026

Ba gói PT/HP/V3 phiên bản `6.0.0-logfix.1` được xuất riêng vào `releases/logfix-20261101`, giữ lại các bản nativefix trước đó. Báo cáo thay đổi và giới hạn: `docs/reports/LOGFIX_20261101.md`. Kết quả từng ZIP và SHA-256: `releases/logfix-20261101/RELEASE_VALIDATION.json`.

`python deploy/windows/packaging/windows_portable/build_logfix.py` biên dịch launcher và tạo ba bản từ `releases/portable-nativefix`, chạy QA rồi archive. Thư mục đích của từng bản phải chưa tồn tại. Sau khi ba bản hoàn tất, chạy `python deploy/windows/packaging/windows_portable/finalize_logfix.py` để tổng hợp báo cáo và kiểm tra log UTF-8/cổng thực tế. Các lệnh cần môi trường cho phép kết nối HTTP localhost và chạy launcher Windows.

Thông tin bên dưới mô tả nền runtime nativefix được tái sử dụng.

Các gói giữ hai giao diện PT/HP đã có, dùng Python 3.12.10 embeddable x64, wheel runtime ghim phiên bản, launcher C# và model AI Care cục bộ. STT chuẩn là Zipformer tiếng Việt 30M INT8 qua sherpa-onnx.

Máy build cần Python trong `.venv`, các model đã chuẩn bị ở dự án, .NET Framework compiler và mạng để tải runtime/wheel/prerequisite nếu chưa có cache.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/windows/packaging/windows_portable/build.ps1 -Version 6.0.0-nativefix.2 -Output releases/portable-nativefix-rebuild
```

Mỗi thư mục đầu ra phải chưa tồn tại. Builder từ chối ghi đè thư mục portable; chọn `-Output` khác cho lần build mới. Các ZIP setup cũ được giữ trong `releases/setup` làm nguồn hai giao diện. `-AssembleOnly` chỉ tái sử dụng runtime đã cài hoàn tất, không bỏ qua QA.

Các bước: tạo runtime riêng, cài wheel bằng `--target --only-binary=:all: --no-compile --no-deps` theo lock đã kiểm tra; xác minh Authenticode VC prerequisite; biên dịch launcher; assembly; QA bằng Python đi kèm; chỉ archive sau QA. `--no-deps` dùng vì file requirements đã chứa toàn bộ dependency closure ghim phiên bản, tránh cài lại các phiên bản khác trong lúc build.

Dữ liệu nằm ở `%LOCALAPPDATA%/AICareDemo/PT` hoặc `HP`; `AI_CARE_DATA_HOME` dùng cho thư mục QA riêng. Launcher EXE có `--self-test` để kiểm tra khởi động/health/token/shutdown mà không mở GUI hay camera. Entry point `--desktop-test` kiểm tra dựng dashboard không mở camera.

Không đưa dữ liệu runtime, .venv, bytecode hay cache máy build vào ZIP. Model Zipformer tiếng Việt INT8 được đóng gói đầy đủ. Torch CPU, PyAV và PyQt5 được giữ cho các chức năng hiện có.

CRC, SHA-256 và manifest hỗ trợ kiểm tra hỏng/thay đổi tệp; launcher chưa ký số. Kiểm tra trên máy build không thay cho Windows sạch, driver thiết bị hay thử nghiệm dài giờ. Xem QA_REPORT.txt trong từng gói.

STT chuẩn từ 24/10/2026: Zipformer tiếng Việt 30M INT8, sherpa-onnx 1.12.40, CPU. Không đóng gói faster-whisper, CTranslate2 hoặc trọng số Whisper. Hai ZIP phải qua QA với runtime nhúng trước khi thay thế bản phát hành.

## Sửa lỗi DLL trên máy khác

Bản hiện hành: `6.0.0-nativefix.2`. Có thêm bản sửa đường dẫn model/token Zipformer và resource graph MediaPipe khi thư mục giải nén chứa dấu. Graph MediaPipe phải khởi tạo và WAV phải giải mã thành công trước khi launcher báo ready. `PORTABLE_COMPAT.json` ghi rõ patch MediaPipe, giữ nguyên license upstream. Xem `docs/PORTABLE_NATIVE_FIX.md` để biết lỗi phát hiện ở lượt giải nén đầu tiên và kết quả kiểm thử cuối.

Các bản portable cũ thiếu `msvcp140.dll`/`concrt140.dll` cạnh Python và vô tình dùng bản có sẵn trong System32. Chỉ kèm `vc_redist.x64.exe` không đáp ứng yêu cầu giải nén rồi chạy. Builder hiện trích CRT AMD64 trực tiếp từ bundle Microsoft có chữ ký hợp lệ bằng `expand.exe`, kiểm tra chữ ký từng DLL, lưu nguồn/hash/version trong `runtime/vc-runtime.json` và đặt DLL cạnh `python.exe`. Không sao chép DLL từ System32, không cài VC++ toàn hệ thống và không cần admin trên máy đích.

`portable_native.py` kiểm tra hash và nạp CRT trước native extension trong cả supervisor và desktop. QA đặt PATH chỉ còn runtime/Windows, làm sai PYTHONHOME/PYTHONPATH, thử nhiều thứ tự import và kiểm tra DLL thực nạp. DLL CRT phải thuộc gói; DLL Defender do Windows chèn được ghi riêng. QA chạy launcher thật, model thật, tiếng Việt từ WAV đóng gói, Qt offscreen và HTTP/token/shutdown. Archive từ chối QA cũ hoặc đầu vào đã đổi và kiểm tra SHA-256/CRC từng thành viên ZIP.

Các ZIP setup/source cũ cần cài Python/thư viện nên không đáp ứng mục tiêu portable này. Các thư mục v1/v1b/zipformer/V3 trước nativefix là lịch sử, không dùng để phát hành. ZIP PT/HP/V3 hiện hành được thay từ `releases/portable-nativefix` sau QA. Không dùng lại báo cáo validation cũ để kết luận bản mới đã chạy trên Windows sạch.

Chạy lại kiểm thử trên máy Windows sạch, không Python, không VC++, ngắt mạng:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File AICare\qa\clean_machine_test.ps1 -Package .\AICare
```

Hoặc tạo cấu hình Windows Sandbox mạng tắt (máy host phải có tính năng Windows Sandbox):

```powershell
.venv\Scripts\python.exe deploy/windows/packaging/windows_portable/make_sandbox_test.py releases/portable-nativefix/V3/AICare docs/test-reports/windows-sandbox-v3
```

Sandbox map gói chỉ đọc, kết quả ghi vào thư mục reports riêng. Kiểm thử này không được đánh dấu đã chạy nếu host không có Windows Sandbox. Camera, micro, driver, CPU và Windows N cần kiểm thử riêng trên máy đích; không suy ra bảo đảm mọi máy từ kết quả trên máy build.
