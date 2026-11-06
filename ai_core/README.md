# ai_core · Giám sát AI cho AICare

Bốn chức năng chạy nền trong app desktop, dùng camera sẵn có. Kết quả lưu vào DB chung và hiện ở tab **🤖 Giám sát AI** trên portal người nhà.

| Thư mục | Chức năng | Mặc định |
| --- | --- | --- |
| `posture_activity_monitoring/` | 3 · Tư thế, hoạt động, nhắc ngồi/nằm lâu | Bật |
| `fall_risk_detection/` | 4 · Ngã nhanh, ngã chậm, hỏi lại bằng giọng nói, dấu hiệu choáng | **Chế độ thử**: ghi và hiện trên portal, chưa gửi yêu cầu khẩn |
| `object_finder/` | 7 · Nhớ nơi để đồ, trả lời "kính của tôi đâu?" | Bật, cần model nhận diện đồ vật |
| `medication_intake_monitoring/` | Giám sát uống thuốc bằng camera | Bật |
| `integration/` | Nối vào AICareDemo: worker camera, DB, outbox/SSE, giọng nói, API portal | |

Module ngã cũ (`modules/fall`) và module nhắc thuốc cũ không bị thay. Chức năng 4 chạy song song để so sánh.

## Giám sát uống thuốc

Mốc tính từ giờ uống của từng liều trong `dose_occurrences`:

| Mốc | Việc |
| --- | --- |
| −5 phút | Mở giám sát: camera chuyển sang MediaPipe Holistic (khung xương + bàn tay + khuôn mặt) |
| Đúng giờ | Loa: "Bác ơi, đến giờ uống thuốc … rồi. Hộp thuốc ở …" |
| +10 phút chưa uống | Loa: "Đến giờ uống thuốc rồi, bác chưa uống…", portal hiện cảnh báo (`ai.medication.not_taken`) |
| +30 phút | Tạo cảnh báo OVERDUE sẵn có, gửi email người nhà qua OutboxRelay |
| +60 phút | Đóng giám sát. Scheduler cũ vẫn quyết định MISSED |

Nhận diện một lần uống: ngón tay đưa lên miệng ≥ 0,3 giây, miệng há ra, rồi uống nước (cốc ở miệng, hoặc đưa tay lên miệng lần 2 khi ngửa đầu) hoặc cầm viên thuốc ở chỗ để thuốc.
- **VERIFIED**: liều chuyển sang CONFIRMED với `action_actor = 'camera_verified'`. App hiện "(Camera ghi nhận)".
- **LIKELY**: chỉ thấy đưa tay lên miệng và há miệng. Loa hỏi lại, không tự ghi nhận đã uống.
- Bị loại: tay ở miệng quá 5 giây, đang cầm điện thoại, người khác uống.

Tay nào cũng được. Có thể chỉnh trong `PUT /api/patients/{id}/ai/settings/medication` (`intake.hand = right`).

## Cấu hình

| Biến môi trường | Ý nghĩa |
| --- | --- |
| `AI_CORE_ENABLED` | `0` để tắt toàn bộ. Mặc định bật, tắt khi `AI_CARE_TEST_MODE=1` |
| `AI_CORE_DETECTOR_MODEL` | Đường dẫn model ONNX D-FINE (Apache-2.0), cần `pip install onnxruntime`. Không có thì chức năng 7 không tự thấy đồ, và không có tín hiệu cốc/điện thoại cho chức năng uống thuốc |

Cấu hình theo bệnh nhân (bảng `ai_settings`), đọc và ghi qua `GET/PUT /api/patients/{id}/ai/settings/{posture|fall|objects|medication}`. Ghi cần quyền `edit_schedule`.
- Bật cảnh báo thật cho chức năng 4: `fall.config.alerts_enabled = true`. Khi đó sự kiện không phản hồi sẽ tạo yêu cầu khẩn giống "cứu tôi".
- Vùng giường/ghế: `posture.zones`. Khu vực có tên cho đồ vật: `objects.regions`. Chỗ để thuốc: `medication.pillbox_region_id`.

## API portal

| | Đường dẫn | |
| --- | --- | --- |
| GET | `/api/patients/{id}/ai` | Tổng quan 4 chức năng |
| GET | `/api/patients/{id}/ai/objects/where?q=` | Hỏi đồ vật ở đâu |
| GET, PUT | `/api/patients/{id}/ai/settings/{feature}` | Cấu hình |
| POST | `/api/patients/{id}/ai/fall/{event_id}/label` | Người nhà đánh giá sự kiện ngã (ngã thật, báo nhầm…) để chỉnh ngưỡng |

SSE: mọi sự kiện `ai.*` đi qua outbox sẵn có.

## Điểm nối vào code cũ

- `app/dashboard.py`: khởi động và dừng worker (`start_ai_core`, `stop_ai_core`).
- `services/local_voice/dispatcher.py`: câu "… đâu rồi?" và trả lời hỏi lại khi ngã được xử lý trước luật giọng nói cũ.
- `web/api_router.py`: mount `/api/patients/{id}/ai`.
- `web/templates/portal_caregiver.html`: tab Giám sát AI.
- `app/today_todo_widget.py`: nhãn "(Camera ghi nhận)".
- `database/migrations/014_ai_core.sql`: bảng mới, không sửa bảng cũ.

## Test

```bash
python -m pytest tests/ai_features -q
```

Dữ liệu tư thế và khung xương đều tổng hợp. Ngưỡng là giá trị khởi điểm, cần đo trên dữ liệu thật trước khi tắt chế độ thử của chức năng 4.

## Chạy làm backend trên đám mây (Render)

```bash
pip install -r requirements-backend.txt
python -m backend.main
```

Backend gồm portal admin, người nhà và bác sĩ, REST API, SSE, lịch thuốc (OutboxRelay) và 4 chức năng AI. Không cần PyQt, camera hay MediaPipe trên server. Thiết bị tại nhà tự chạy MediaPipe rồi gửi kết quả lên, nên video không rời khỏi nhà.

Deploy trên Render: Dashboard → **New → Blueprint** → chọn repo này. Render đọc `render.yaml` (web service Python 3.12, gói Starter, đĩa 1 GB ở `/var/data` để SQLite không mất khi deploy lại). Cần điền các biến:

| Biến | Ý nghĩa |
| --- | --- |
| `AICARE_ADMIN_PASSWORD` | Mật khẩu tài khoản `admin`, ít nhất 12 ký tự. Bỏ trống thì backend tự sinh và in một lần ra log |
| `AICARE_DEVICE_KEYS` | Khoá cho thiết bị: `khoa-dai-it-nhat-16-ky-tu:patient_id` (nhiều bệnh nhân ngăn bằng `\|`, `*` là tất cả) |
| `EMAIL_USERNAME`, `EMAIL_PASSWORD` | Gmail và App Password để gửi email cảnh báo người nhà |

API cho thiết bị (header `X-Device-Key`):

| | Đường dẫn | Gửi gì |
| --- | --- | --- |
| POST | `/api/device/patients/{id}/observations` | `{"observations": [{"ts", "landmarks": 33×[x,y,z,visibility], "recognized"}]}` |
| POST | `/api/device/patients/{id}/holistic` | Trong giờ uống thuốc: `{"frames": [{"ts", "face": 468×[x,y], "left_hand", "right_hand"}]}` (pixel) |
| POST | `/api/device/patients/{id}/detections` | `{"frames": [{"ts", "detections": [{"label", "score", "box"}]}]}` |
| POST | `/api/device/patients/{id}/voice` | `{"text": "kính của tôi đâu"}` → `{"reply": ...}` |
| GET | `/api/device/patients/{id}/commands?after=<id>` | Câu cần đọc loa, và `monitoring.holistic` = có đang trong giờ uống thuốc không |

Thiết bị hỏi `commands` vài giây một lần: đọc to các câu mới, và khi `monitoring.holistic = true` thì chuyển sang chạy Holistic, gửi lên `holistic`.
