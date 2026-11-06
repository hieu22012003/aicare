# Module Nhắc Thuốc & Theo Dõi Tuân Thủ Độc Lập (P0–P3)

Module được phát triển độc lập theo đặc tả tại [`plan/KE_HOACH_NHAC_THUOC_TUAN_THU.md`](../../plan/KE_HOACH_NHAC_THUOC_TUAN_THU.md) và [`plan/THIET_KE_CSDL_CHAM_SOC_NGUOI_CAO_TUOI.md`](../../plan/THIET_KE_CSDL_CHAM_SOC_NGUOI_CAO_TUOI.md).

---

## 1. Cấu Trúc Module

```text
features/medication/adherence/
├── domain/                  # Lõi nghiệp vụ & Hợp đồng trừu tượng
│   ├── models.py            # Dataclasses & Enums (DoseState, DoseEvent, Alert, v.v.)
│   └── contracts.py         # Abstract interfaces (IPatientIdentityAdapter, IMedicationRepository, v.v.)
├── database/                # CSDL quan hệ chuẩn hoá 3NF
│   ├── schema.sql           # DDL 16 bảng chuẩn hoá có ràng buộc FK, CHECK, UNIQUE, INDEX
│   ├── connection.py        # DatabaseManager với PRAGMA foreign_keys = ON & WAL mode
│   └── seed_data.py         # Nạp 2 bệnh nhân, 3 người chăm sóc, 1 bác sĩ, đơn thuốc mẫu
├── adapters/                # Các adapter thực thi hợp đồng
│   ├── identity_adapter.py  # PatientIdentityAdapter & Mock với cơ chế chống lẫn bệnh nhân
│   ├── patient_repository.py# SqlPatientRepository
│   ├── caregiver_repository.py # SqlCaregiverRepository lọc quan hệ hiệu lực & email xác thực
│   ├── medication_repository.py# SqlMedicationRepository (Prescriptions, Occurrences, Alerts)
│   └── notification_gateway.py # NotificationGateway & MockNotificationGateway
├── services/                # Dịch vụ ứng dụng & Bộ lập lịch
│   ├── scheduler.py         # AdherenceScheduler chạy nền độc lập camera
│   └── adherence_service.py # AdherenceService điều phối các thao tác Đã uống, Nhắc lại, SOS
├── ui/                      # Giao diện người dùng (PyQt5 & Headless Presenters)
│   ├── elderly_ui.py        # Giao diện Người cao tuổi: thẻ to, 3 nút hành động
│   ├── caregiver_ui.py      # Giao diện Người nhà: quản lý đơn, ảnh, xem trước 7 ngày, xử lý quá hạn
│   └── main_app.py          # Ứng dụng độc lập tích hợp bảng điều khiển mô phỏng
├── tests/                   # Bộ kiểm thử tự động toàn diện
│   ├── test_database_and_seed.py
│   ├── test_patient_isolation.py
│   ├── test_scheduler_and_overdue.py
│   ├── test_actions_and_idempotency.py
│   ├── test_caregiver_filtering.py
│   ├── test_revision_immutability.py
│   ├── test_end_to_end_acceptance.py
│   └── run_all_tests.py     # Script thực thi toàn bộ test suite
└── docs/
    └── INTEGRATION_GUIDE.md # Tài liệu hướng dẫn 3 điểm nối cho giai đoạn P4
```

---

## 2. Hướng Dẫn Chạy Kiểm Thử Tự Động

Thực thi toàn bộ bộ test suite:

```bash
python3 features/medication/adherence/tests/run_all_tests.py
```

Kết quả: Toàn bộ 20 bài kiểm thử đơn vị và tích hợp đạt **100% PASS** (kiểm chứng các tiêu chí nghiệm thu 1–6, 8–10, quan hệ người chăm sóc hết hạn, chống lẫn dữ liệu giữa các bệnh nhân và tính bất biến của lịch sử).

---

## 3. Khởi Chạy Ứng Dụng Mô Phỏng Độc Lập

```bash
python3 features/medication/adherence/ui/main_app.py
```

Ứng dụng cung cấp thanh điều khiển mô phỏng phía trên:
- **Đổi nhận diện Camera**: Chuyển đổi giữa Cụ Hoàng Anh (`patient_001`), Cụ Mai Hương (`patient_002`), hoặc "Mất tín hiệu camera" để kiểm tra tính năng chống lẫn dữ liệu và chế độ chọn thủ công.
- **Tua nhanh thời gian**: Mô phỏng thời gian trôi qua để kiểm tra các lần nhắc đến giờ (`REMINDING`) và chuyển sang quá hạn (`OVERDUE`).
- **Giao diện Người cao tuổi**: Thử nghiệm 3 nút thao tác `Đã uống`, `Nhắc lại sau`, `Cần hỗ trợ`.
- **Giao diện Người nhà**: Thử nghiệm thêm lịch thuốc với xem trước 7 ngày và xử lý các sự cố quá hạn/SOS.
