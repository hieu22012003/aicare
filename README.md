# AICare: hệ thống AI giám sát và hỗ trợ người cao tuổi tại nhà

Một codebase gồm hai phần chạy cùng nhau:

- **App tại nhà** (`apps/desktop`): PyQt5, camera, MediaPipe, loa và micro. Chạy trên máy tính đặt ở nhà người cao tuổi.
- **Server và website quản lý** (`apps/server`): cổng người nhà, bác sĩ, admin, REST API, SSE và lịch thuốc. Chạy trên Render.

Video không rời khỏi nhà. App tại nhà chỉ gửi toạ độ khung xương, bàn tay và khuôn mặt lên server.

## Cấu trúc thư mục

```
apps/
  desktop/            app tại nhà (main.py, dashboard, widget)
  server/             backend cho Render (python -m apps.server.main)
features/             mỗi thư mục là một chức năng
  face_monitoring/    1. giám sát khuôn mặt, nhận diện người đã đăng ký
  emotion/            2. nhận diện và tổng hợp cảm xúc
  posture_activity/   3. tư thế, hoạt động, nhắc ngồi/nằm lâu
  fall_risk/          4. phát hiện choáng, ngã
  medication/         5. lịch thuốc, nhắc giờ, giám sát uống thuốc qua camera
  voice_assistant/    6. trợ lý giọng nói
  object_finder/      7. tìm đồ vật
  music_care/         8. âm nhạc, trò chơi nhận thức
  exercise_rehab/     9. hướng dẫn tập luyện
  web_portal/         10. website quản lý (portal, API, SSE)
  health_vitals/      chỉ số sức khỏe (huyết áp, nhịp tim…)
shared/               dùng chung: config, database, camera, services, vision, ai_runtime
deploy/windows/       Setup / Start / Stop Demo.cmd, đóng gói Windows
scripts/  tests/  docs/  models/
render.yaml           cấu hình deploy Render (thư mục gốc repo)
```

## Giám sát uống thuốc (chức năng 5)

Ví dụ lịch uống thuốc lúc 10:00:

| Giờ | Việc xảy ra |
|---|---|
| 09:55 | Camera tự bật, kể cả khi đang tắt. Bắt đầu theo dõi bàn tay và miệng (MediaPipe Holistic). |
| 10:00 | Loa nói: "Bác ơi, đến giờ uống thuốc … rồi." |
| Khi uống | **Tay phải** đưa thuốc lên miệng, **ngón cái và ngón trỏ** cùng chạm gần miệng, **miệng há ra**. Liều được ghi là đã uống ("Camera ghi nhận"). |
| 10:10 chưa uống | Loa nói: "Đến giờ uống thuốc rồi, bác chưa uống…". Web hiện ngay thanh cảnh báo đỏ, kèm dòng trong nhật ký và lịch sử cảnh báo. |
| 10:30 | Tạo cảnh báo quá giờ và gửi email cho người nhà. |

Người nhà có thể đổi cách xưng hô (bác, anh, ông, bà…), số phút trước khi cảnh báo, tay cầm thuốc và quy tắc ngón tay ở **Trang chủ**, mục "Giám sát uống thuốc qua camera".

Mã nguồn: `features/medication/intake_monitoring/` (bộ nhận diện và mốc thời gian) và `shared/ai_runtime/` (bật camera, nói, gửi lên server).

## Các bổ sung khác

- **Tìm đồ vật (7):** model `models/object_detector/dfine_s_coco.onnx` (D-FINE-S, COCO 80 loại, Apache-2.0) được app tự nạp. Tìm được điện thoại, điều khiển, cốc, chai, sách, kéo… Kính mắt không có trong COCO.
- **Choáng, ngã (4):** mặc định gửi cảnh báo thật. Người nhà tắt được ở Trang chủ (chuyển về chế độ thử).
- **Khuôn mặt (1):** ghi giờ xuất hiện lần đầu, lần cuối và số lần xuất hiện mỗi ngày. Báo khi có người lạ, khi xuất hiện muộn hơn thường lệ 2 giờ, hoặc khi đến giờ quen mà chưa thấy.
  Nhận diện dùng SFace (OpenCV, `features/face_monitoring/standalone/models/face_recognition_sface_2021dec.onnx` + `face_detection_yunet_2023mar.onnx`), giữ danh tính khi tay che miệng hoặc quay đầu; chỉ báo "Người lạ" khi khuôn mặt khác hẳn liên tục ≥ 1,5 giây. Thiếu 2 file này thì app tự quay về LBPH. Chỉnh bằng biến môi trường `AICARE_SFACE_ACCEPT` (mặc định 0.42), `AICARE_SFACE_REJECT` (0.28), `AICARE_FACE_HOLD_SECONDS` (10).
- **Cảm xúc và âm nhạc (2, 8):** khi bản tổng hợp cảm xúc là "Buồn", app nói một lời gợi ý và tự mở nhạc (ưu tiên nhạc người nhà gửi).
- **Giọng nói (6):** trả lời "mấy giờ rồi", "hôm nay thứ mấy, ngày mấy".
- **Tập luyện (9):** thêm bài "Xoay khớp vai" và "Nghiêng người giãn cơ".
- **Website (10):** thêm tab "Báo cáo tuần/tháng": tỷ lệ uống thuốc, cảnh báo, ngã, ngày buồn, tập luyện, thời gian đi lại theo ngày. In hoặc lưu PDF được.
- **Đồng bộ nhà ↔ Render:** app tại nhà lấy đơn thuốc và nhạc từ web về, rồi gửi lên các nút "Đã uống", cảm xúc, buổi tập, chỉ số nhập ở nhà, trò chơi, lần xuất hiện khuôn mặt và đồ vật nhìn thấy (`shared/ai_runtime/replication.py`).

## Chạy thử

```bash
pip install -r requirements.txt              # app tại nhà (Windows: deploy/windows/Setup.cmd)
python apps/desktop/main.py

pip install -r requirements-backend.txt      # chỉ server và web
python -m apps.server.main                   # http://localhost:8080
python -m pytest tests -q
```

## Deploy lên Render

Render dùng thư mục gốc repo (Root Directory để trống) và đọc `render.yaml`:

- Build: `pip install -r requirements-backend.txt`
- Start: `python -m apps.server.main`
- Kiểm tra sức khỏe: `/api/health`

Các biến môi trường:

| Biến | Ý nghĩa |
|---|---|
| `AICARE_ADMIN_PASSWORD` | Mật khẩu tài khoản `admin@unifiedcare.local` (ít nhất 12 ký tự) |
| `AICARE_BOOTSTRAP_CAREGIVER` | `email\|mật khẩu\|Họ tên\|patient_001`: tạo sẵn tài khoản người nhà khi server khởi động |
| `AICARE_BOOTSTRAP_CLINICIAN` | `email\|mật khẩu\|Họ tên\|patient_001`: tạo sẵn tài khoản bác sĩ phụ trách bệnh nhân đó |
| `AICARE_DEVICE_KEYS` | `khoá:patient_001` cho app tại nhà (khoá dài ít nhất 16 ký tự) |
| `EMAIL_USERNAME`, `EMAIL_PASSWORD` | Gmail và App Password để gửi email cảnh báo |

Gói Free của Render **không có ổ đĩa lưu trữ**, nên dữ liệu SQLite mất khi deploy lại hoặc khi server ngủ (sau 15 phút không có truy cập). `AICARE_BOOTSTRAP_CAREGIVER` giúp tạo lại tài khoản người nhà tự động. Muốn giữ dữ liệu lâu dài thì chuyển sang gói Starter và gắn đĩa ở `/var/data`.

## Nối app tại nhà với web trên Render

Trong file `.env` của app tại nhà:

```
AICARE_CLOUD_URL=https://<tên-service>.onrender.com
AICARE_DEVICE_KEY=<khoá trùng với AICARE_DEVICE_KEYS trên server>
AICARE_CLOUD_PATIENT_ID=patient_001
```

Khi có hai biến đầu, app tại nhà gửi toạ độ lên server. Server giữ lịch thuốc, quyết định giờ nhắc và cảnh báo. App đọc to các câu nhắc và tự bật camera khi đến giờ thuốc.
