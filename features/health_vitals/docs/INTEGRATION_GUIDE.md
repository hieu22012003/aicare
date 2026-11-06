# Hướng Dẫn Tích Hợp Module Nhật Ký Sức Khỏe & Bác Sĩ Theo Dõi Từ Xa (P0-P2)

Tài liệu này xác định hợp đồng tích hợp độc lập giữa Module **Health Monitoring** (`features/health_vitals/`) và Hệ Thống Trung Tâm (`AI_Rehabilitation_Safety_Assistant`).

Module được thiết kế theo kiến trúc **Hexagonal (Ports & Adapters)**, cho phép hoạt động độc lập với CSDL SQLite cục bộ hoặc kết nối với hệ thống production thông qua 3 điểm tích hợp rõ ràng.

---

## 1. Ba Điểm Tích Hợp Cần Nối Với Hệ Thống Chính

### Điểm 1: Nguồn `patient_id` (Từ Camera / Session Authentication)
- **Hợp đồng hiện tại**: [`IIdentityAdapter`](file:///mnt/shared/Code/AI_Rehabilitation_Safety_Assistant/modules/health_monitoring/domain/contracts.py)
  - Triển khai độc lập: `MockPatientIdentityAdapter`
- **Cách tích hợp vào hệ thống chính**:
  1. Khi hệ thống nhận diện khuôn mặt (`features/face_monitoring/standalone/`) nhận diện thành công người cao tuổi, truyền `recognized_patient_id` vào Adapter:
     ```python
     identity_adapter.set_patient_override(recognized_patient_id)
     ```
  2. **Quy tắc an toàn (Bắt buộc)**:
     - Khi camera tắt hoặc nhận diện thất bại, hệ thống **không** cho phép người dùng gõ `patient_id` tùy ý trên client.
     - Hệ thống kích hoạt cơ chế dự phòng **Authenticated Session Fallback**: lấy ID từ tài khoản đã đăng nhập của người bệnh hoặc người nhà chăm sóc hợp lệ.
     - Khi đổi người trước camera (từ Bệnh nhân A sang Bệnh nhân B), Adapter lập tức kích hoạt sự kiện dọn sạch (flush) màn hình hiển thị để chống rò rỉ dữ liệu chéo giữa các bệnh nhân.

---

### Điểm 2: Xác Thực & Phân Quyền Bác Sĩ / Người Nhà (`care_team` & `patient_caregivers`)
- **Hợp đồng hiện tại**:
  - [`ICareTeamRepository`](file:///mnt/shared/Code/AI_Rehabilitation_Safety_Assistant/modules/health_monitoring/domain/contracts.py): Kiểm tra quyền bác sĩ theo dõi.
  - [`IPatientAccessRepository`](file:///mnt/shared/Code/AI_Rehabilitation_Safety_Assistant/modules/health_monitoring/domain/contracts.py): Kiểm tra quyền người bệnh và người chăm sóc.
- **Cách tích hợp vào hệ thống chính**:
  1. Thay thế `SqlCareTeamRepository` bằng Adapter kết nối bảng `care_team` của hệ thống chính.
  2. **Quy tắc an toàn (Bắt buộc)**:
     - Kiểm tra quyền ở phía Server trên **mỗi request** (`assert_clinician_access`):
       ```sql
       SELECT 1 FROM care_team
       WHERE clinician_id = ? AND patient_id = ?
         AND is_active = 1
         AND valid_from <= datetime('now')
         AND (valid_to IS NULL OR valid_to >= datetime('now'))
       ```
     - Nếu bác sĩ bị rút quyền hoặc quan hệ trong `care_team` hết hạn (`valid_to < now`), server từ chối ngay lập tức (`PermissionError`) và xóa cache quyền, ngăn chặn tuyệt đối việc đọc dữ liệu cũ hay dữ liệu đã cache.
     - Người nhà chỉ được xem khi `patient_caregivers.can_view_health = 1` và còn hiệu lực.

---

### Điểm 3: Nơi Lưu Trữ Số Đo (`measurement_groups` & `health_observations`)
- **Hợp đồng hiện tại**: [`IObservationRepository`](file:///mnt/shared/Code/AI_Rehabilitation_Safety_Assistant/modules/health_monitoring/domain/contracts.py)
  - Triển khai độc lập: `SqlObservationRepository`
- **Cách tích hợp vào hệ thống chính**:
  1. Khi hệ thống chính sử dụng PostgreSQL hoặc cơ sở dữ liệu chung:
     - Nối với bảng `measurement_groups` và `health_observations`.
  2. **Quy tắc toàn vẹn dữ liệu (Bắt buộc)**:
     - **Huyết áp**: Phải được lưu thành 1 nhóm đo (`measurement_groups`) chứa đúng 2 bản ghi con trong `health_observations` (`blood_pressure_systolic` và `blood_pressure_diastolic`). Ràng buộc `UNIQUE(group_id, observation_type_id)` đảm bảo không bị ghép lẫn tâm thu ngày này với tâm trương ngày khác.
     - **Tách biệt thời gian**: Lưu riêng `measured_at` (giờ đo thực tế trên máy) và `recorded_at` (thời điểm ghi nhận vào hệ thống).
     - **Idempotency**: Client gửi kèm `client_request_id` (UUID). Nếu mạng bị ngắt và gửi lại, server trả về nhóm đo đã tạo mà không nhân đôi bản ghi.
     - **Bảo toàn lịch sử khi sửa sai**: Không dùng `UPDATE` ghi đè giá trị gốc. Bản ghi sai được chuyển trạng thái `status = 'corrected'`, bản ghi mới được tạo với `corrects_observation_id = old_id` và `status = 'active'`, giúp giữ toàn vẹn dấu vết kiểm toán y khoa.
     - **Không lập ngưỡng cảnh báo tự động**: Không tự ý phán đoán ngưỡng nguy hiểm hay cam kết bác sĩ trực 24/7. Mọi ngưỡng (nếu có) phải do chính bác sĩ xác nhận bằng văn bản cấu hình phiên bản.

---

## 2. Bảng Tóm Tắt Ánh Xạ Schema

| Thành phần Module Độc Lập | Tên bảng đề xuất trên CSDL Chính | Mục đích & Ràng buộc |
|---|---|---|
| `users` | `users` / `accounts` | Tài khoản xác thực gốc |
| `patients` | `patients` / `elderly_profiles` | Hồ sơ người cao tuổi, múi giờ IANA |
| `clinicians` | `clinicians` / `doctors` | Bác sĩ đã xác minh CCHN |
| `care_team` | `care_team` | Quan hệ N-N Bác sĩ - Bệnh nhân (có thời hạn `valid_to`) |
| `patient_caregivers` | `patient_caregivers` | Quan hệ N-N Người chăm sóc - Bệnh nhân (`can_view_health`) |
| `observation_types` | `observation_types` | Danh mục loại chỉ số chuẩn hóa (chuẩn bị FHIR Observation) |
| `measurement_groups` | `measurement_groups` | Nhóm lần đo, nguồn, giờ đo, idempotency key |
| `health_observations` | `health_observations` | Giá trị đo chi tiết, liên kết sửa sai `corrects_observation_id` |
| `clinician_reviews` | `clinician_reviews` | Nhật ký xác nhận đã xem của Bác sĩ (chỉ tạo khi bấm nút) |
| `clinical_messages` | `clinical_messages` | Lời dặn / Yêu cầu đo lại của Bác sĩ (trạng thái `sent` != `read`) |
| `message_receipts` | `message_receipts` | Biên nhận đọc tin nhắn của người bệnh |
