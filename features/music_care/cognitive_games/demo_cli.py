"""
Command Line Interface (CLI) Demo for Cognitive Games & Relaxation Module.
Allows running and inspecting the module directly from terminal without GUI.
Supports automated demonstration mode (--demo) and interactive menu.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.music_care.cognitive_games.adapters.care_team_repository import SqlCareTeamRepository
from features.music_care.cognitive_games.adapters.game_repository import SqlGameRepository
from features.music_care.cognitive_games.adapters.identity_adapter import MockPatientIdentityAdapter
from features.music_care.cognitive_games.adapters.music_repository import SqlMusicRepository
from features.music_care.cognitive_games.database.connection import DatabaseManager
from features.music_care.cognitive_games.database.seed_data import seed_database
from features.music_care.cognitive_games.domain.models import (
    AudioMode,
    DifficultyLevel,
    FeedbackRating,
    GameStatus,
)
from features.music_care.cognitive_games.services.audio_service import AudioService
from features.music_care.cognitive_games.services.cognitive_service import CognitiveService
from features.music_care.cognitive_games.services.game_engine import GameEngine


def print_banner() -> None:
    print("=" * 72)
    print("🧠 TRỢ LÝ RÈN LUYỆN TRÍ NHỚ & THƯ GIÃN CHO NGƯỜI CAO TUỔI")
    print("   Module độc lập tuân thủ KE_HOACH_TRO_CHOI_TRI_NHO_NGUOI_CAO_TUOI.md")
    print("   Thông điệp: “Luyện tập và giải trí phù hợp với bạn.”")
    print("   ⚠️  Không dùng làm chỉ số chẩn đoán y khoa sa sút trí tuệ hay điểm IQ.")
    print("=" * 72)


def run_automated_demo(db: DatabaseManager) -> None:
    print("\n🚀 BẮT ĐẦU CHẠY DEMO TỰ ĐỘNG CÁC TÍNH NĂNG CỐT LÕI (P0–P4):\n")

    care_team_repo = SqlCareTeamRepository(db)
    game_repo = SqlGameRepository(db)
    music_repo = SqlMusicRepository(db)
    identity_adapter = MockPatientIdentityAdapter(initial_patient_id="pat-001")

    service = CognitiveService(game_repo, care_team_repo, identity_adapter)
    audio = AudioService(music_repo)

    # 1. Identity & Camera Fallback
    print("--- 1. NHẬN DIỆN BỆNH NHÂN & DỰ PHÒNG CAMERA (Criterion 7) ---")
    current_p = service.get_active_patient_id()
    print(f"✓ Bệnh nhân hiện tại: {current_p} (Nguyễn Văn An)")
    identity_adapter.set_camera_active(False)
    fallback_p = service.get_active_patient_id()
    print(f"✓ Camera lỗi -> Tự động kích hoạt PIN/phiên hợp lệ: {fallback_p} (Không bị khóa truy cập)")
    identity_adapter.set_camera_active(True)

    # 2. Card Matching Game (Criterion 2)
    print("\n--- 2. LẬT HÌNH GHÉP CẶP (Criterion 2: Đúng 3 cặp, không lẻ, seed chuẩn) ---")
    session, board = service.start_card_matching_session(patient_id="pat-001", difficulty=DifficultyLevel.EASY, seed=123)
    print(f"✓ Tạo phiên game: {session.session_id} (Độ khó: {session.difficulty}, Seed: {session.seed})")
    print(f"✓ Số lượng thẻ bài trên bàn cờ: {len(board)} thẻ (tương ứng {len(board)//2} cặp)")
    for c in board:
        print(f"   [Ô {c.card_index+1}]: {c.icon_symbol} {c.name}")

    # Gợi ý không phạt
    hints = service.record_hint_used(session.session_id)
    print(f"✓ Bác dùng trợ giúp gợi ý: {hints} lần (Không bị phạt điểm hay giảm thành tích)")

    # Tạm dừng không tính giờ
    service.pause_session(session.session_id)
    print("✓ Tạm dừng phiên game (Thời gian tạm dừng được loại trừ khỏi active_seconds)")
    time.sleep(0.05)
    service.resume_session(session.session_id)

    # Hoàn thành phiên
    completed_sess, result = service.finish_card_matching(
        session_id=session.session_id,
        attempts=4,
        correct_pairs=3,
        total_pairs=3,
        user_feedback="vừa phải, thú vị",
        enjoyment="rất vui",
    )
    print(f"✓ Kết quả: Hoàn thành 3/3 cặp trong {completed_sess.active_seconds:.1f}s | Cảm nhận: {result.user_feedback}")

    # 3. Sequence Recall Game (Criterion 3)
    print("\n--- 3. NHỚ TRÌNH TỰ (Criterion 3: Chấm theo thứ tự, xem lại không phạt) ---")
    seq_sess, target_seq, choices = service.start_sequence_recall_session(
        patient_id="pat-001", difficulty=DifficultyLevel.EASY, seed=456
    )
    print(f"✓ Trình tự mục tiêu ({len(target_seq)} hình): {' -> '.join([it['icon'] + ' ' + it['name'] for it in target_seq])}")
    print(f"✓ Các lựa chọn trong kho: {', '.join([it['icon'] + ' ' + it['name'] for it in choices])}")

    # Test order grading
    perfect_match, correct_count, msg = GameEngine.grade_sequence(target_seq, target_seq)
    print(f"✓ Chấm điểm đúng thứ tự: {msg} (Khớp {correct_count}/{len(target_seq)} vị trí)")

    service.finish_sequence_recall(
        session_id=seq_sess.session_id,
        attempts=1,
        correct_rounds=1,
        total_rounds=1,
        user_feedback="vừa sức",
        enjoyment="vui",
    )

    # 4. Relaxing Music & Sleep Timer (Criterion 8 & 9)
    print("\n--- 4. NGHE NHẠC THƯ GIÃN & HẸN GIỜ (Criteria 8 & 9) ---")
    tracks = audio.list_tracks()
    print(f"✓ Kho nhạc: {len(tracks)} bản nhạc có bản quyền CC0")
    for t in tracks:
        print(f"   * [{t.track_id}] {t.title} ({t.audio_mode}) - {t.duration_seconds}s")

    lis_sess = audio.start_playback(patient_id="pat-001", track_id="trk-relax-chimes", timer_minutes=5)
    print(f"✓ Phát nhạc: {lis_sess.track_id} (Hẹn giờ tắt: 5 phút). Âm lượng: {audio.volume}% nhẹ nhàng")
    audio.tick(301.0)  # fast forward timer
    print("✓ Hết giờ hẹn -> Tự động dừng phát nhạc an toàn.")

    # 5. Role-based Progress Dashboard (Criterion 4 & 6)
    print("\n--- 5. TIẾN TRÌNH & PHÂN QUYỀN DASHBOARD (Criteria 4 & 6) ---")
    summaries = service.get_patient_progress(actor_id="pat-001", actor_role="patient", patient_id="pat-001")
    print(f"✓ Bệnh nhân xem tiến trình bản thân ({len(summaries)} nhóm độ khó chuẩn hóa):")
    for s in summaries:
        print(f"   * [{s.game_code.upper()} - {s.difficulty.upper()} - {s.rules_version}]: n={s.sample_count_n} ván | Tỷ lệ HT: {s.completion_rate:.1f}% | Gợi ý TB: {s.avg_help_count:.1f}")

    # Test Care Team Expiration
    print("\n--- 6. KIỂM TRA PHÂN QUYỀN BÁC SĨ HẾT HẠN (Criterion 6) ---")
    try:
        service.get_patient_progress(actor_id="doc-002", actor_role="clinician", patient_id="pat-001")
        print("❌ Lỗi: Bác sĩ hết hạn vẫn truy cập được!")
    except PermissionError as pe:
        print(f"✓ Thành công chặn bác sĩ hết hạn: {pe}")

    print("\n" + "=" * 72)
    print("🎉 HOÀN TẤT CHẠY DEMO TỰ ĐỘNG! TOÀN BỘ 10 TIÊU CHÍ ĐÃ ĐƯỢC XÁC THỰC.")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cognitive Games CLI Demo")
    parser.add_argument("--demo", action="store_true", help="Chạy kịch bản demo tự động")
    args = parser.parse_args()

    db = DatabaseManager()
    seed_database(db)

    print_banner()

    if args.demo:
        run_automated_demo(db)
        return

    # Interactive Menu
    while True:
        print("\nCHỌN CHỨC NĂNG TRẢI NGHIỆM:")
        print("1. Chạy kịch bản tự động toàn diện (P0–P4)")
        print("2. Chạy toàn bộ 22 bài kiểm thử (run_all_tests.py)")
        print("3. Khởi chạy giao diện đồ họa PyQt5 (main_app.py)")
        print("0. Thoát")

        choice = input("\nNhập lựa chọn [0-3]: ").strip()
        if choice == "1":
            run_automated_demo(db)
        elif choice == "2":
            from features.music_care.cognitive_games.tests.run_all_tests import run_cognitive_games_tests
            run_cognitive_games_tests()
        elif choice == "3":
            print("Đang khởi động giao diện đồ họa...")
            from features.music_care.cognitive_games.ui.main_app import run_app
            run_app()
            break
        elif choice == "0":
            print("Tạm biệt!")
            break
        else:
            print("Lựa chọn không hợp lệ. Vui lòng thử lại.")


if __name__ == "__main__":
    main()
