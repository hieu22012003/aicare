# Module Trò Chơi Trí Nhớ & Thư Giãn Dành Cho Người Cao Tuổi
*(Cognitive Memory Games & Relaxation Module for Elderly Users)*

Module độc lập tuân thủ nghiêm ngặt theo tài liệu yêu cầu: [`plan/KE_HOACH_TRO_CHOI_TRI_NHO_NGUOI_CAO_TUOI.md`](../../plan/KE_HOACH_TRO_CHOI_TRI_NHO_NGUOI_CAO_TUOI.md).

---

## 1. Cấu Trúc Module

```text
features/music_care/cognitive_games/
├── __init__.py
├── README.md                           # Giới thiệu module, lệnh chạy & license
├── demo_cli.py                         # CLI demo trực tiếp trong terminal (chạy được cả không có GUI)
├── adapters/                           # Bộ điều hợp (Adapters)
│   ├── __init__.py
│   ├── care_team_repository.py         # Kiểm tra phân quyền bác sĩ/người nhà (ICareTeamRepository)
│   ├── game_repository.py              # Lưu trữ phiên, kết quả, tiến trình (IGameRepository)
│   ├── identity_adapter.py             # Mock nhận diện bệnh nhân & dự phòng camera (IPatientIdentityAdapter)
│   └── music_repository.py             # Quản lý nhạc thư giãn & nhật ký nghe (IMusicRepository)
├── assets/                             # Tài nguyên âm thanh & hình ảnh (Bản quyền CC0)
│   ├── asset_manifest.json             # Manifest lưu trữ metadata và license từng asset
│   ├── audio/                          # 4 file âm thanh stereo 16-bit WAV tạo bằng thuật toán toán học
│   │   ├── trk-relax-chimes.wav        # Chuông gió thanh tịnh (Ordinary)
│   │   ├── trk-peaceful-stream.wav     # Suối reo rừng chiều (Ordinary)
│   │   ├── trk-meditation-harmonics.wav# Giai điệu bình an (Ordinary)
│   │   └── trk-binaural-alpha-10hz.wav # Âm thanh thử nghiệm 10Hz (Binaural Beats)
│   └── generate_assets.py              # Script tái tạo tài nguyên âm thanh & manifest
├── database/                           # Cơ sở dữ liệu SQLite độc lập
│   ├── __init__.py
│   ├── connection.py                   # Quản lý kết nối, WAL mode, foreign keys, in-memory mode
│   ├── schema.sql                      # Schema chuẩn 3NF (12 bảng)
│   └── seed_data.py                    # Seed dữ liệu 2 bệnh nhân, 2 người nhà, 2 bác sĩ, packs, tracks
├── docs/                               # Tài liệu tích hợp
│   └── INTEGRATION_GUIDE.md            # Hướng dẫn chi tiết 3 điểm kết nối với hệ thống chính
├── domain/                             # Thực thể và Hợp đồng (Domain Models & Interfaces)
│   ├── __init__.py
│   ├── contracts.py                    # Abstract Ports (IPatientIdentityAdapter, ICareTeamRepository...)
│   └── models.py                       # Dataclasses & Enums (GameSession, GameResult, MusicTrack...)
├── services/                           # Nghiệp vụ cốt lõi
│   ├── __init__.py
│   ├── audio_service.py                # Quản lý phát nhạc, âm lượng nhỏ, hẹn giờ, điều kiện binaural
│   ├── cognitive_service.py            # Điều phối phiên chơi, tính thời gian active, phân quyền
│   └── game_engine.py                  # Thuật toán sinh đề seed cố định, chấm điểm thứ tự chuẩn
├── tests/                              # Bộ kiểm thử tự động toàn diện
│   ├── __init__.py
│   ├── run_all_tests.py                # Test runner tổng hợp
│   ├── test_10_acceptance_criteria.py  # Kiểm thử chi tiết 10 tiêu chí nghiệm thu mục 8
│   ├── test_database_and_seed.py       # Kiểm thử schema, foreign keys & seed data
│   ├── test_game_engine.py             # Kiểm thử bàn cờ, số cặp, thứ tự chấm điểm
│   └── test_permissions_and_identity.py# Kiểm thử RBAC, cách ly dữ liệu & hết hạn bác sĩ
└── ui/                                 # Giao diện người dùng PyQt5 thiết kế cho người cao tuổi
    ├── __init__.py
    ├── card_matching_widget.py         # Màn chơi lật hình ghép cặp (3/4/6 cặp, nút lớn, gợi ý, pause)
    ├── main_app.py                     # Cửa sổ chính, thanh chuyển vai trò, camera toggle, 2 tab chính
    ├── music_player_widget.py          # Trình phát nhạc, hẹn giờ 5/10/15p, cảnh báo binaural
    ├── progress_dashboard_widget.py    # Dashboard tiến trình theo vai trò (Bệnh nhân/Người nhà/Bác sĩ)
    └── sequence_recall_widget.py       # Màn chơi nhớ trình tự (2/3/4 hình, xem lại không phạt)
```

---

## 2. Lệnh Chạy (Commands)

### A. Khởi chạy Giao diện Đồ Họa PyQt5 (GUI)
```bash
# Môi trường Wayland / X11:
./.venv-linux/bin/python features/music_care/cognitive_games/ui/main_app.py

# Hoặc chạy kiểm thử chế độ offscreen (nếu không có màn hình hiển thị):
QT_QPA_PLATFORM=offscreen ./.venv-linux/bin/python features/music_care/cognitive_games/ui/main_app.py
```

### B. Chạy Demo Tự Động Trong Terminal (CLI)
```bash
./.venv-linux/bin/python features/music_care/cognitive_games/demo_cli.py --demo
```

### C. Chạy Toàn Bộ Bộ Kiểm Thử (All Tests)
```bash
./.venv-linux/bin/python features/music_care/cognitive_games/tests/run_all_tests.py
```

---

## 3. Nguồn Gốc & Giấy Phép Bản Quyền Tài Nguyên (Asset Provenance & License)

Toàn bộ tài nguyên được tạo hợp pháp và ghi rõ trong `assets/asset_manifest.json`:
- **Bộ hình biểu tượng (Icons/Emojis)**: Sử dụng các biểu tượng Unicode tiêu chuẩn thân thuộc với người cao tuổi Việt Nam (bát cơm 🍚, ấm trà 🫖, chiếc quạt 🪭, hoa sen 🪷, dưa hấu 🍉, bắp ngô 🌽...). Giấy phép: **CC0-1.0 (Public Domain)**.
- **Tài nguyên âm thanh (Audio Files)**: 4 bản nhạc 16-bit stereo `.wav` được tổng hợp hoàn toàn bằng thuật toán toán học trong script `generate_assets.py` (tần số sóng hài tự nhiên 432 Hz, hợp âm trưởng êm dịu, và chênh lệch pha 10 Hz cho Binaural). Không sử dụng bất kỳ bài hát thương mại nào. Giấy phép: **CC0-1.0 (Public Domain)**.
- **Bảo mật sinh trắc học**: Module **tuyệt đối không lưu ảnh khuôn mặt hay video** trong bất kỳ bảng nào của CSDL (Tiêu chí 10).

---

## 4. Báo Cáo Kết Quả Kiểm Thử (22/22 Tests Passed - 100%)

Đã kiểm tra và vượt qua đầy đủ **10 Tiêu Chí Nghiệm Thu** tại Mục 8 của kế hoạch:
- **Tiêu chí 1**: Chữ, nút và hình ảnh lớn (≥14pt, nút ≥50px), có focus bàn phím; người già không bị mắc kẹt khi dừng chơi.
- **Tiêu chí 2**: Sinh đúng 3/4/6 cặp, không bao giờ xuất hiện ô lẻ; seed cố định giúp tái hiện chính xác; thời gian tạm dừng (pause) được loại trừ khỏi `active_seconds`.
- **Tiêu chí 3**: Nhớ trình tự chiều dài 2/3/4 hình được chấm nghiêm ngặt theo đúng vị trí thứ tự; tính năng "Xem lại" không bị tính là lỗi.
- **Tiêu chí 4**: Tiến trình phân nhóm chuẩn hóa theo từng trò, độ khó và phiên bản luật kèm số mẫu $n$ (`sample_count_n`); không so sánh gộp các phiên khác luật.
- **Tiêu chí 5**: Thoát giữa phiên lưu trạng thái `abandoned`, tuyệt đối không chấm 0 điểm; gửi lại kết quả có `client_request_id` trùng lặp đảm bảo tính lũy đẳng (idempotency).
- **Tiêu chí 6**: Cách ly dữ liệu nhiều bệnh nhân (`pat-001` không đọc được `pat-002`); người nhà và bác sĩ hết hạn ủy quyền (`doc-002`) bị chặn truy cập ngay lập tức.
- **Tiêu chí 7**: Khi camera gặp sự cố, hệ thống tự động kích hoạt phiên dự phòng (PIN/phiên hợp lệ); khi đổi người, phiên cũ và âm thanh được đóng sạch trước khi mở người mới.
- **Tiêu chí 8**: Âm nhạc mặc định luôn TẮT khi mở ứng dụng; có nút tạm dừng, dừng ngay, và tự tắt theo hẹn giờ 5/10/15 phút; khảo sát cảm nhận có thể bỏ qua.
- **Tiêu chí 9**: Tùy chọn Binaural Beats 10Hz mặc định TẮT; bắt buộc có hướng dẫn đeo tai nghe stereo và hộp thoại đồng ý thử nghiệm; có nút dừng tức thì; không đưa ra tuyên bố chữa sa sút trí tuệ hay tăng trí nhớ.
- **Tiêu chí 10**: Toàn bộ tài nguyên có chứng chỉ bản quyền CC0; CSDL game không lưu dữ liệu khuôn mặt.

---

## 5. Ba Điểm Kết Nối Cốt Lõi Khi Tích Hợp

1. **`patient_id` (Nhận diện & Dự phòng)**: Nối qua `IPatientIdentityAdapter`. Cho phép nhận diện tự động qua camera hoặc đăng nhập dự phòng bằng mã PIN khi camera lỗi.
2. **Auth & Quyền Chăm Sóc**: Nối qua `ICareTeamRepository`. Kiểm tra quyền của người nhà (`patient_caregivers`) và bác sĩ (`care_team`) dựa trên sự đồng ý còn hiệu lực trước mỗi request xem tiến trình.
3. **Nơi Lưu Trữ `game_sessions`**: Nối qua `IGameRepository` và `IMusicRepository`. Lưu trữ các bảng `game_sessions`, `game_results`, `listening_sessions` với đầy đủ tính toàn vẹn và bảo mật dữ liệu. Chi tiết xem tại [`docs/INTEGRATION_GUIDE.md`](docs/INTEGRATION_GUIDE.md).
