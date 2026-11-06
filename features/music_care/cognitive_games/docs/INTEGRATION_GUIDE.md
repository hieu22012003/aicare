# Hướng Dẫn Tích Hợp Module Trò Chơi Trí Nhớ & Thư Giãn (Cognitive Games)

Tài liệu này hướng dẫn chi tiết về kiến trúc module độc lập và **3 Điểm Kết Nối Cốt Lõi** khi đưa module vào hệ thống chính của Dự án Trợ lý An toàn & Phục hồi chức năng.

---

## 1. Kiến Trúc Hướng Cổng & Bộ Điều Hợp (Hexagonal Architecture / Ports & Adapters)

Module được thiết kế hoàn toàn tách biệt, không phụ thuộc trực tiếp vào CSDL hay framework của repository chính:

```text
[ Hệ Thống Chính / Camera / Auth ]
               │
               ▼ (3 Điểm Kết Nối)
   ┌────────────────────────────────────────────────────────┐
   │       CÁC CỔNG GIAO TIẾP (DOMAIN CONTRACTS)           │
   │  ├─ IPatientIdentityAdapter (Nhận diện bệnh nhân)       │
   │  ├─ ICareTeamRepository     (Quyền người nhà & bác sĩ) │
   │  ├─ IGameRepository         (Lưu phiên & kết quả game) │
   │  └─ IMusicRepository        (Lưu phiên nghe nhạc)      │
   └────────────────────────────────────────────────────────┘
               │
               ▼
   ┌────────────────────────────────────────────────────────┐
   │            DỊCH VỤ NGHIỆP VỤ & QUY TẮC                 │
   │  ├─ CognitiveService   (Điều phối phiên, thời gian)   │
   │  ├─ AudioService       (Phát nhạc, hẹn giờ, binaural)  │
   │  └─ GameEngine         (Sinh đề seed cố định, chấm điểm)│
   └────────────────────────────────────────────────────────┘
               │
               ▼
   ┌────────────────────────────────────────────────────────┐
   │          GIAO DIỆN NGƯỜI DÙNG (UI LAYER)               │
   │  ├─ CardMatchingWidget   (Lật hình 3/4/6 cặp, nút lớn) │
   │  ├─ SequenceRecallWidget (Nhớ trình tự 2/3/4 hình)     │
   │  ├─ MusicPlayerWidget    (Nhạc êm dịu, sleep timer)    │
   │  └─ ProgressDashboard    (Tiến trình theo vai trò P4)  │
   └────────────────────────────────────────────────────────┘
```

---

## 2. Ba Điểm Kết Nối Khi Tích Hợp Với Hệ Thống Chính

### Điểm Nối 1: `patient_id` (Nhận diện khuôn mặt & Cơ chế dự phòng)
- **Vị trí hợp đồng**: `modules.cognitive_games.domain.contracts.IPatientIdentityAdapter`
- **Mục đích**: Nhận diện người cao tuổi đang ngồi trước thiết bị để tự động tải hồ sơ và bài tập phù hợp.
- **Cách tích hợp**:
  1. Trong môi trường kiểm thử/giả lập: sử dụng `MockPatientIdentityAdapter`.
  2. Trong hệ thống production: tạo lớp `LiveCameraIdentityAdapter(IPatientIdentityAdapter)` nhận tín hiệu từ module Camera/Face Recognition.
- **Ràng buộc an toàn (Tiêu chí 7)**:
  - Khi camera gặp lỗi (mất kết nối, phòng tối, camera bị che), hệ thống **bắt buộc phải có cơ chế dự phòng** qua mã PIN hoặc phiên đăng nhập đã xác thực; không được khóa hoặc chặn quyền chơi game/nghe nhạc của người già.
  - Khi camera phát hiện đổi người, adapter gọi `switch_patient(new_patient_id)`, lập tức đóng phiên game/nhạc của người cũ trước khi khởi tạo môi trường cho người mới.
  - **Không lưu ảnh khuôn mặt hoặc video trong CSDL trò chơi (Tiêu chí 10)**.

---

### Điểm Nối 2: Auth & Phân Quyền Chăm Sóc (`care_team_repo`)
- **Vị trí hợp đồng**: `modules.cognitive_games.domain.contracts.ICareTeamRepository`
- **Mục đích**: Bảo vệ quyền riêng tư và dữ liệu sức khỏe của người bệnh; kiểm tra quyền truy cập trước mọi truy vấn tiến trình (Tiêu chí 6).
- **Hợp đồng kiểm tra quyền**:
  - `is_clinician_authorized(clinician_id, patient_id) -> bool`: Kiểm tra bác sĩ có quan hệ trong bảng `care_team`, trạng thái `consent_status = 'GRANTED'`, `is_active = 1`, và mốc thời gian `valid_from <= now <= valid_to`. Khi quan hệ hết hạn (như `doc-002`), hệ thống chặn ngay lập tức (`PermissionError`).
  - `can_caregiver_view_progress(caregiver_id, patient_id) -> bool`: Kiểm tra người nhà trong bảng `patient_caregivers` có cờ `can_view_health = 1` và đang trong thời gian hiệu lực.
  - `get_patient_progress(actor_id, actor_role, patient_id)`:
    * **Người bệnh**: Chỉ xem của chính mình (`actor_id == patient_id`). Tuyệt đối không hiển thị xếp hạng hay so sánh điểm với người khác (tránh mặc cảm).
    * **Người nhà**: Xem tần suất tham gia, cảm nhận của người thân; không đưa ra cảnh báo suy giảm sa sút trí tuệ chỉ từ một phiên kém.
    * **Bác sĩ**: Xem biểu đồ phân nhóm chuẩn hóa theo từng trò, từng độ khó và phiên bản luật kèm kích thước mẫu $n$ (Tiêu chí 4). Không dùng điểm số làm chỉ số chẩn đoán y khoa.

---

### Điểm Nối 3: Nơi Lưu Trữ `game_sessions`, `game_results`, `listening_sessions`
- **Vị trí hợp đồng**: `modules.cognitive_games.domain.contracts.IGameRepository` & `IMusicRepository`
- **Các bảng dữ liệu cốt lõi**:
  - `game_sessions`:
    * Trường: `session_id`, `patient_id`, `game_type_id`, `pack_id`, `difficulty`, `seed`, `started_at`, `ended_at`, `status`, `active_seconds`, `help_count`, `rules_version`, `client_request_id`, `notes`.
    * **Bảo toàn phiên bỏ dở**: Khi người dùng thoát giữa chừng, lưu trạng thái `status = 'abandoned'`, ghi nhận thời gian `active_seconds`, tuyệt đối **không ghi kết quả 0 điểm** vào bảng kết quả (Tiêu chí 5).
    * **Idempotency**: Ràng buộc `client_request_id UNIQUE` đảm bảo gửi lại kết quả do mất mạng không tạo 2 phiên trùng lặp.
    * **Đo lường thời gian chủ động**: Thời gian tạm dừng (Pause) được trừ khỏi `active_seconds` để không làm sai lệch số liệu tập trung (Tiêu chí 2).
  - `game_results`:
    * Trường: `session_id` (PK/FK), `attempts`, `correct_count`, `total_tasks`, `accuracy_percent`, `user_feedback`, `enjoyment`, `metrics_json`.
    * Chỉ ghi nhận khi phiên đạt trạng thái `completed`.
  - `listening_sessions`:
    * Trường: `listen_id`, `patient_id`, `track_id`, `started_at`, `ended_at`, `played_seconds`, `is_binaural`, `headphone_confirmed`, `feedback`, `stopped_reason`, `client_request_id`.
- **Cách tích hợp hạ tầng**:
  - Trong giai đoạn độc lập: Dữ liệu được lưu trong SQLite nội bộ tại `features/music_care/cognitive_games/database/cognitive_games.db` với chế độ WAL và bật Foreign Keys.
  - Khi tích hợp hệ thống chính: Tạo class `PostgresGameRepository(IGameRepository)` kết nối với CSDL trung tâm của hệ thống mà không cần sửa bất kỳ dòng code nào trong `GameEngine` hay `UI`.

---

## 3. Ràng Buộc Y Tế & Nguyên Tắc Đạo Đức (Safety Guardrails)

1. **Thông điệp sản phẩm**: “Luyện tập và giải trí phù hợp với bạn.”
2. **Phi chẩn đoán**: Tuyệt đối không gọi điểm trò chơi là “Điểm IQ”, “Chỉ số trí tuệ”, hay “Mức độ sa sút trí tuệ”.
3. **Âm thanh thư giãn**: Mặc định luôn tắt; chỉ phát khi người dùng chủ động chọn nghe; không tự động phát lặp ngoài ý muốn (Tiêu chí 8).
4. **Thử nghiệm Binaural Beats 10Hz (Nhánh thử nghiệm tùy chọn)**:
   - Mặc định luôn TẮT (Tiêu chí 9).
   - Yêu cầu người dùng xác nhận đã đeo tai nghe stereo 2 bên tai và đồng ý thử nghiệm.
   - Luôn có nút dừng ngay lập tức; hướng dẫn dừng nghe nếu chóng mặt hoặc khó chịu.
   - Nghiêm cấm đưa ra tuyên bố chữa khỏi bệnh hay tăng trí nhớ.
