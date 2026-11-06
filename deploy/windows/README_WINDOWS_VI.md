# HƯỚNG DẪN SỬ DỤNG BẢN DEMO — AI CARE & REHABILITATION ASSISTANT
*Hệ thống Trợ lý AI Chăm sóc Sức khỏe, Phục hồi Chức năng và Giám sát An toàn cho Người cao tuổi*  
*Phiên bản Phát hành: v06 (v6.0.0) Portable Edition dành cho Windows 10/11 x64*

**Gói giải nén rồi chạy hiện hành: `6.0.0-nativefix.2`**, trong `releases/AI_FOR_EXAM_PT.zip`, `AI_FOR_EXAM_HP.zip` và `AI_FOR_EXAM_V3.zip`. Các gói này chứa Python, VC++ runtime và model; giải nén toàn bộ rồi chạy `AICare.exe`. Các bản portable trước nativefix và gói setup/source cũ không đáp ứng cùng cam kết đóng gói. Xem [điều tra DLL và kiểm thử portable](docs/PORTABLE_NATIVE_FIX.md), cùng `QA_REPORT.txt` trong gói. Kiểm thử trên Windows sạch không Python/VC++ chưa được thực hiện; không coi kết quả trên máy build là bảo đảm mọi máy.

---

## 🌟 1. TỔNG QUAN BẢN DEMO HỢP NHẤT

Hệ thống hoạt động trên mô hình tích hợp **4 trong 1** chạy cục bộ ngay trên máy tính của bạn:
1. **👵 Ứng dụng Người cao tuổi (Desktop App PyQt5):**
   - Đăng ký hồ sơ người dùng / bệnh nhân mới với công nghệ **Tracking Bounding Box (KCF + IoU Gating)** mượt mà, chống giật nhảy khung hình, bắt nét chính xác.
   - Tự động nhận diện khuôn mặt người cao tuổi đã đăng ký trong hệ thống.
   - Phân tích biểu cảm cảm xúc (Vui vẻ, Buồn bã, Bình thường).
   - Hướng dẫn và tự động đếm bài tập phục hồi chức năng bằng AI thị giác (Vươn vai, Đứng lên ngồi xuống, Đi bộ tại chỗ).
   - Bảng việc cần làm hôm nay: Nhắc uống thuốc một chạm ("Đã uống" tự khai báo), ghi nhận chỉ số huyết áp/nhịp tim.
   - Trò chơi rèn luyện trí nhớ (Lật hình ghép cặp, Nhớ trình tự) và nghe nhạc tĩnh tâm thư giãn.
   - Trợ lý giọng nói tiếng Việt nhận lệnh tự nhiên 100% offline.
2. **👨‍👩‍👧 Cổng Người nhà (Caregiver Portal):** Theo dõi tuân thủ uống thuốc, lịch sử tập luyện và chỉ số sức khỏe của người thân từ xa (đăng nhập bằng Email & Mật khẩu được tạo lúc đăng ký hồ sơ).
3. **👨‍⚕️ Cổng Bác sĩ (Clinician Portal):** Theo dõi biểu đồ sinh hiệu, đánh giá sức khỏe, kê đơn thuốc và điều chỉnh bài tập phục hồi chức năng.
4. **🛡️ Cổng Quản trị viên (Admin Portal):** Quản trị toàn diện hệ thống: duyệt, quản lý, khóa/mở khóa các hồ sơ người dùng và tài khoản người nhà được đăng ký.

---

## 🚀 2. HƯỚNG DẪN 3 BƯỚC KHỞI CHẠY NHANH

### Bước 1: Giải nén thư mục
Giải nén tệp tin `AICareDemo-v06-windows-x64.zip` vào bất kỳ thư mục nào trên máy tính (ví dụ: `C:\AICareDemo\` hoặc `D:\AICareDemo\`).

### Bước 2: Thiết lập một lần duy nhất (`deploy/windows/Setup.cmd`)
* Nhấp đúp chuột vào tệp **`deploy/windows/Setup.cmd`**.
* *Yêu cầu kết nối Internet trong lần đầu này:* Hệ thống sẽ tự động kiểm tra Windows 64-bit, chuẩn bị môi trường Python riêng biệt, cài đặt các thư viện cần thiết và tải các mô hình AI đã được xác minh mã băm SHA-256.
* Khi màn hình hiển thị `🎉 THIẾT LẬP THÀNH CÔNG!`, nhấn phím bất kỳ để đóng cửa sổ.

### Bước 3: Nhấp một nút để chạy toàn bộ (`deploy/windows/Start Demo.cmd`)
* Nhấp đúp chuột vào tệp **`deploy/windows/Start Demo.cmd`**.
* Hệ thống sẽ tự động:
  1. Khởi động Máy chủ Backend & SSE Gateway cục bộ.
  2. Tự động mở trình duyệt web tới **Cổng Quản trị viên & Đăng nhập**.
  3. Mở **Giao diện Người cao tuổi** trực tiếp trên màn hình máy tính.
* **Sau khi thiết lập, toàn bộ luồng cốt lõi hoạt động 100% OFFLINE**, không cần Internet.

---

## 🌐 3. ĐỊA CHỈ TRUY CẬP VÀ TÀI KHOẢN QUẢN TRỊ VIÊN

Trình duyệt web truy cập tại địa chỉ: **`http://127.0.0.1:8080/login`**

Toàn bộ các tài khoản thử nghiệm cũ cho người già, bệnh nhân, người nhà đã được dọn dẹp sạch sẽ trong bản v06. Hệ thống chỉ lưu trữ **duy nhất 01 tài khoản Quản trị viên** để quản lý các tài khoản đăng ký mới:

| Vai trò | Đường dẫn truy cập | Tài khoản đăng nhập | Mật khẩu | Chức năng chính |
|---|---|---|---|---|
| **🛡️ Quản trị viên Hệ thống** | `http://127.0.0.1:8080/admin` | `admin`<br>*(hoặc `admin@unifiedcare.local`)* | Đặt riêng bằng công cụ quản trị cục bộ | Quản trị danh sách người dùng, xem xét duyệt hồ sơ bệnh nhân và cấp quyền cho người nhà |
| **👵 Đăng ký Người dùng mới** | Trực tiếp trên Desktop App | Bấm nút **"➕ Đăng Ký Hồ Sơ Mới"** | Tự thiết lập | Chụp ảnh mặt căn chỉnh tự động với Tracking BBox; điền họ tên, tuổi, bệnh lý và thông tin người nhà |
| **👨‍👩‍👧 Cổng Người nhà** | `http://127.0.0.1:8080/caregiver` | Email người nhà đăng ký | Mật khẩu đăng ký | Theo dõi sức khỏe, lịch uống thuốc và quá trình phục hồi của người thân từ xa |

---

## 🛠️ 4. CÁC TẬP TIN ĐIỀU KHIỂN HỆ THỐNG

| Tệp tin | Chức năng | Hướng dẫn sử dụng |
|---|---|---|
| **`deploy/windows/Setup.cmd`** | Thiết lập môi trường tự động | Chạy 1 lần duy nhất sau khi giải nén gói phát hành. |
| **`deploy/windows/Start Demo.cmd`** | Khởi động toàn bộ hệ thống | Chạy hàng ngày để mở ứng dụng desktop và máy chủ web. |
| **`deploy/windows/Stop Demo.cmd`** | Dừng hệ thống an toàn | Nhấp đúp để giải phóng camera/micro và tắt sạch các tiến trình demo. |
| **`deploy/windows/Diagnostics.cmd`** | Kiểm tra chẩn đoán hệ thống | Kiểm tra thiết bị camera, micro, thư viện và xuất file báo cáo tại `runtime/logs/diagnostics_report.txt`. |
| **`deploy/windows/Reset Demo.cmd`** | Đặt lại dữ liệu mẫu | Đưa dữ liệu về trạng thái ban đầu sạch sẽ; tự động sao lưu dữ liệu cũ vào `runtime/backups/`. |

---

## 💻 5. YÊU CẦU HỆ THỐNG & GHI CHÚ KỸ THUẬT

* **Hệ điều hành:** Windows 10 / Windows 11 (64-bit).
* **Phần cứng:** CPU Intel/AMD thông thường, RAM tối thiểu 4GB (Khuyến nghị 8GB).
* **Card đồ họa (GPU):** Không bắt buộc; hệ thống chạy CPU-first.
* **Thiết bị ngoại vi:** Webcam (để nhận diện mặt và đếm bài tập), Micro (để ra lệnh giọng nói). Nếu không có webcam/micro, hệ thống vẫn hoạt động và hiển thị cảnh báo nhẹ mà không bị crash.
* **Quyền hạn:** Không yêu cầu quyền Administrator máy tính; cài đặt hoàn toàn trong thư mục người dùng.



## Bảo vệ tài khoản

Không còn tài khoản/mật khẩu đăng nhập nhanh. Trước lần đăng nhập quản trị đầu tiên, chạy trong terminal cục bộ:

```powershell
.venv\Scripts\python.exe scripts/set_account_password.py
```

Công cụ hỏi mật khẩu mà không hiển thị; không truyền mật khẩu trên dòng lệnh. Với tài khoản khác, thêm `--user-id`; chọn database bằng `--database` nếu cần. Mật khẩu mặc định cũ bị vô hiệu hóa; mật khẩu riêng cũ được nâng cấp sang PBKDF2-HMAC-SHA256 có salt. Việc đổi mật khẩu hoặc khóa tài khoản làm phiên cũ mất hiệu lực.
