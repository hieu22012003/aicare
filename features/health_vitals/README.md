# Module Nhật Ký Sức Khỏe & Bác Sĩ Theo Dõi Từ Xa (P0–P2)

Module độc lập chuyên biệt thực hiện chức năng **Nhật ký sức khỏe cho người cao tuổi/người thân** và **Dashboard theo dõi từ xa cho bác sĩ**, bám sát thiết kế tại:
- `plan/KE_HOACH_NHAT_KY_SUC_KHOE_THEO_DOI_TU_XA .md`
- `plan/THIET_KE_CSDL_CHAM_SOC_NGUOI_CAO_TUOI.md`

Module hoàn toàn độc lập, không phụ thuộc và không sửa đổi codebase chính của dự án.

---

## 1. Cấu Trúc Thư Mục

```
features/health_vitals/
├── README.md                           # Giới thiệu & hướng dẫn nhanh
├── docs/
│   └── INTEGRATION_GUIDE.md            # 3 điểm tích hợp với hệ thống chính
├── database/
│   ├── schema.sql                      # CSDL 3NF (15 bảng, foreign keys, CHECK, UNIQUE)
│   ├── connection.py                   # Quản lý kết nối SQLite thread-safe, WAL mode
│   └── seed_data.py                    # Dữ liệu mô phỏng 2 BN, 2 BS, 2 người nhà, 30 ngày số đo
├── domain/
│   ├── models.py                       # Dataclasses, Enums, AggregatedPoint
│   └── contracts.py                    # Abstract Interfaces (Repository & Service ports)
├── adapters/
│   ├── identity_adapter.py             # Adapter nhận diện khuôn mặt & phiên dự phòng
│   ├── patient_access_repository.py    # Kiểm tra quyền người bệnh & người nhà
│   ├── care_team_repository.py         # Kiểm tra quyền bác sĩ care_team (hết hạn / đang hiệu lực)
│   └── observation_repository.py       # Truy vấn số đo, nhóm huyết áp, bảo toàn sửa sai, review
├── services/
│   └── monitoring_service.py           # Phân quyền server, kiểm tra lỗi gõ, gom nhóm ngày/tuần
├── ui/
│   ├── patient_entry_ui.py             # Form nhập số đo người bệnh/người nhà, font lớn
│   ├── clinician_dashboard_ui.py       # Dashboard bác sĩ 2 tầng (Danh sách & Chi tiết, Biểu đồ)
│   └── main_app.py                     # Ứng dụng Desktop chạy độc lập đa vai trò (PyQt5)
├── demo_cli.py                         # Trình chạy kịch bản & biểu đồ tương tác trên Terminal
└── tests/
    ├── run_all_tests.py                # Runner chạy toàn bộ test suites (23/23 PASS)
    ├── test_15_acceptance_criteria.py  # 15 kịch bản nghiệm thu bắt buộc (Mục 7)
    ├── test_database_and_seed.py       # Kiểm thử tính toàn vẹn CSDL, FK, CHECK
    └── test_permissions_and_care_team.py# Kiểm thử ma trận phân quyền
```

---

## 2. Hướng Dẫn Chạy & Kiểm Thử

### Cách 1: Chạy CLI Tương Tác Trực Tiếp Trong Terminal (Khuyên dùng)
Chạy menu tương tác với biểu đồ ASCII, bộ lọc thời gian, chuyển đổi độ phân giải và thử nghiệm phân quyền:
```bash
./.venv-linux/bin/python features/health_vitals/demo_cli.py
```

### Cách 2: Chạy Toàn Bộ Bộ Test Tự Động (23 Tests - 100% PASS)
Kiểm thử tự động toàn bộ 15 kịch bản nghiệm thu bắt buộc của Mục 7 và các kiểm thử ràng buộc CSDL:
```bash
./.venv-linux/bin/python features/health_vitals/tests/run_all_tests.py
```

### Cách 3: Chạy Giao Diện Đồ Họa Độc Lập (PyQt5)
Khởi chạy ứng dụng đồ họa trên máy tính cá nhân (tự động hỗ trợ Wayland / X11):
```bash
QT_QPA_PLATFORM=wayland ./.venv-linux/bin/python features/health_vitals/ui/main_app.py
```
*(Hoặc chạy không cần cờ nếu đã cài đủ thư viện XCB hệ thống)*:
```bash
./.venv-linux/bin/python features/health_vitals/ui/main_app.py
```

---

## 3. Đặc Điểm Nổi Bật Đạt Chuẩn Nghiệm Thu

1. **Huyết áp là một nhóm hai thành phần**: Lưu chung trong `measurement_groups` với 2 bản ghi con tâm thu và tâm trương (`ot-sys` và `ot-dia`), đơn vị `mmHg`, không bao giờ ghép sai ngày.
2. **Tách biệt hai mốc thời gian**: Lưu riêng `measured_at` (thời điểm đo thực tế) và `recorded_at` (thời điểm nhập vào hệ thống).
3. **Idempotency Key**: Chống trùng lặp dữ liệu khi kết nối mạng chập chờn gửi lại request (`client_request_id`).
4. **Bảo toàn dữ liệu khi sửa sai**: Giữ nguyên bản ghi gốc với trạng thái `corrected`, lưu bản ghi mới với `corrects_observation_id`, loại trừ giá trị sai khỏi biểu đồ mặc định.
5. **Bác sĩ mất quyền bị chặn tuyệt đối**: Bác sĩ hết hạn trong `care_team` không thể đọc dữ liệu qua API lẫn bộ nhớ cache.
6. **Biểu đồ chân thực**:
   - Thiếu ngày **không** tạo điểm 0.
   - Khoảng trống >= 48h / 5 ngày **không** vẽ đường nối liên tục gây hiểu lầm là có theo dõi liên tục.
   - Gom nhóm Ngày/Tuần có hiển thị số mẫu `n`, giá trị `mean` và `median`, cho phép mở xem lại dữ liệu gốc.
   - Hai đơn vị đường huyết (`mg/dL` và `mmol/L`) được tách riêng, không vẽ chung trên một đường nối.
7. **Ranh giới y tế an toàn**: Không tự động đặt ngưỡng cảnh báo y tế mặc định, không tuyên bố có bác sĩ trực 24/7. Trạng thái phản hồi phân biệt rõ ràng giữa "Đã gửi" và "Đã đọc".
