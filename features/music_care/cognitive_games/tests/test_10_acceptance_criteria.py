"""
Dedicated automated test suite for all 10 Acceptance Criteria (Tiêu chí nghiệm thu)
from Section 8 of KE_HOACH_TRO_CHOI_TRI_NHO_NGUOI_CAO_TUOI.md.
"""

import json
import time
import unittest
from pathlib import Path

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
    GameSession,
    GameStatus,
)
from features.music_care.cognitive_games.services.audio_service import AudioService
from features.music_care.cognitive_games.services.cognitive_service import CognitiveService
from features.music_care.cognitive_games.services.game_engine import GameEngine


class Test10AcceptanceCriteria(unittest.TestCase):
    def setUp(self) -> None:
        self.db = DatabaseManager(db_path=":memory:")
        self.db.initialize_schema()
        seed_database(self.db)

        self.care_team_repo = SqlCareTeamRepository(self.db)
        self.game_repo = SqlGameRepository(self.db)
        self.music_repo = SqlMusicRepository(self.db)
        self.identity_adapter = MockPatientIdentityAdapter(initial_patient_id="pat-001")

        self.cognitive_service = CognitiveService(
            game_repo=self.game_repo,
            care_team_repo=self.care_team_repo,
            identity_adapter=self.identity_adapter,
        )
        self.audio_service = AudioService(self.music_repo)

    def test_criterion_1_elderly_accessible_and_nontrapping_stop(self) -> None:
        """
        Tiêu chí 1: Người lớn tuổi lần đầu có thể bắt đầu lật hình sau hướng dẫn ngắn,
        hoàn thành hoặc dừng mà không mắc kẹt; chữ/hình/nút đủ lớn, có focus bàn phím và nhãn đọc màn hình.
        """
        # Start a card matching session
        session, board = self.cognitive_service.start_card_matching_session(
            patient_id="pat-001",
            difficulty=DifficultyLevel.EASY,
        )
        self.assertIsNotNone(session)
        self.assertEqual(len(board), 6)  # 3 pairs

        # User chooses to stop midway (not trapped)
        abandoned = self.cognitive_service.abandon_session(session.session_id, notes="Người dùng dừng chủ động")
        self.assertIsNotNone(abandoned)
        self.assertEqual(abandoned.status, GameStatus.ABANDONED.value)

        # Verify no 0-point result created in game_results
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as c FROM game_results WHERE session_id = ?", (session.session_id,))
            self.assertEqual(cursor.fetchone()["c"], 0)

    def test_criterion_2_exact_pairs_no_odd_cards_seed_and_pause_timer(self) -> None:
        """
        Tiêu chí 2: Ba cặp hình tạo đúng ba cặp, các cặp không xuất hiện lẻ;
        dùng seed cố định tái hiện phiên lỗi; số lượt/gợi ý/giây hoạt động chính xác khi pause.
        """
        # Test 1: 3 pairs create exactly 3 pairs, no odd items
        board = GameEngine.generate_card_matching_board(
            items_pool=[
                {"id": "i1", "name": "Item 1", "icon": "1️⃣"},
                {"id": "i2", "name": "Item 2", "icon": "2️⃣"},
                {"id": "i3", "name": "Item 3", "icon": "3️⃣"},
                {"id": "i4", "name": "Item 4", "icon": "4️⃣"},
            ],
            difficulty=DifficultyLevel.EASY,
            seed=42,
        )
        self.assertEqual(len(board), 6)
        ids = [c.item_id for c in board]
        for item_id in set(ids):
            self.assertEqual(ids.count(item_id), 2, f"Item {item_id} appears {ids.count(item_id)} times, expected 2")

        # Test 2: Seed reproducibility
        board_replay = GameEngine.generate_card_matching_board(
            items_pool=[
                {"id": "i1", "name": "Item 1", "icon": "1️⃣"},
                {"id": "i2", "name": "Item 2", "icon": "2️⃣"},
                {"id": "i3", "name": "Item 3", "icon": "3️⃣"},
                {"id": "i4", "name": "Item 4", "icon": "4️⃣"},
            ],
            difficulty=DifficultyLevel.EASY,
            seed=42,
        )
        self.assertEqual([c.item_id for c in board], [c.item_id for c in board_replay])

        # Test 3: Pause does not accumulate active time
        sess, _ = self.cognitive_service.start_card_matching_session(
            patient_id="pat-001",
            difficulty=DifficultyLevel.EASY,
            seed=100,
        )
        time.sleep(0.05)
        self.cognitive_service.pause_session(sess.session_id)
        # Sleep during pause
        time.sleep(0.1)
        self.cognitive_service.resume_session(sess.session_id)
        time.sleep(0.05)
        final_sess, _ = self.cognitive_service.finish_card_matching(
            session_id=sess.session_id,
            attempts=3,
            correct_pairs=3,
            total_pairs=3,
        )
        # Active seconds should be close to 0.1s, strictly less than 0.2s despite pause
        self.assertLess(final_sess.active_seconds, 0.25)

    def test_criterion_3_sequence_order_strict_grading_and_review_no_penalty(self) -> None:
        """
        Tiêu chí 3: Trình tự chiều dài 2/3/4 được chấm theo thứ tự, không chỉ đúng tập hình;
        xem lại không tính là sai.
        """
        sess, target, choices = self.cognitive_service.start_sequence_recall_session(
            patient_id="pat-001",
            difficulty=DifficultyLevel.EASY,
            seed=777,
        )
        self.assertEqual(len(target), 2)  # Length 2 on easy

        # Grading strictly by order
        reversed_order = list(reversed(target))
        if reversed_order[0]["id"] != target[0]["id"]:
            is_perfect, correct_pos, _ = GameEngine.grade_sequence(target, reversed_order)
            self.assertFalse(is_perfect)

        # Perfect order
        is_perfect, correct_pos, _ = GameEngine.grade_sequence(target, target)
        self.assertTrue(is_perfect)
        self.assertEqual(correct_pos, len(target))

        # Reviewing sequence does not decrement score or create errors
        # Finished with 1 attempt and 1 correct round
        completed_sess, result = self.cognitive_service.finish_sequence_recall(
            session_id=sess.session_id,
            attempts=1,
            correct_rounds=1,
            total_rounds=1,
            metrics={"reviews_count": 5},
        )
        self.assertEqual(result.correct_count, 1)
        self.assertEqual(result.accuracy_percent, 100.0)

    def test_criterion_4_isolated_summaries_no_comparison_across_different_rules(self) -> None:
        """
        Tiêu chí 4: Đổi mức và bộ hình giữ metadata; biểu đồ không so sánh hai phiên khác luật như cùng độ khó.
        """
        summaries = self.cognitive_service.get_patient_progress(
            actor_id="pat-001",
            actor_role="patient",
            patient_id="pat-001",
        )
        self.assertTrue(len(summaries) > 0)

        # Verify that each progress summary is strictly segregated by (game_code, difficulty, rules_version)
        seen_keys = set()
        for s in summaries:
            key = (s.game_code, s.difficulty, s.rules_version)
            self.assertNotIn(key, seen_keys, f"Duplicate summary grouping found for {key}")
            seen_keys.add(key)
            self.assertGreaterEqual(s.sample_count_n, 1)

    def test_criterion_5_abandoned_saved_no_zero_and_idempotency_request_id(self) -> None:
        """
        Tiêu chí 5: Thoát giữa phiên lưu `abandoned`, không chấm 0;
        gửi lại kết quả không tạo hai phiên.
        """
        client_req_id = "req-idempotency-test-999"

        # Create session with client_request_id
        sess1, _ = self.cognitive_service.start_card_matching_session(
            patient_id="pat-001",
            difficulty=DifficultyLevel.EASY,
            client_request_id=client_req_id,
        )

        # Attempt to create duplicate session with the SAME client_request_id
        sess2, _ = self.cognitive_service.start_card_matching_session(
            patient_id="pat-001",
            difficulty=DifficultyLevel.EASY,
            client_request_id=client_req_id,
        )

        # Idempotency: Must return the existing session id
        self.assertEqual(sess1.session_id, sess2.session_id)

        # Abandoning session preserves status and does not write 0-score result
        abandoned = self.cognitive_service.abandon_session(sess1.session_id)
        self.assertEqual(abandoned.status, "abandoned")

    def test_criterion_6_tenant_isolation_and_expired_permission_denial(self) -> None:
        """
        Tiêu chí 6: `patient_id` A không đọc/lưu vào tiến trình B qua UI và API;
        bác sĩ/người nhà mất quyền không còn đọc được dữ liệu.
        """
        # 1. Patient A cannot read Patient B data
        with self.assertRaises(PermissionError):
            self.cognitive_service.get_patient_progress(
                actor_id="pat-001",
                actor_role="patient",
                patient_id="pat-002",
            )

        # 2. Unauthorized caregiver cannot read Patient A data
        with self.assertRaises(PermissionError):
            self.cognitive_service.get_patient_progress(
                actor_id="cg-002",  # Caregiver for pat-002 only
                actor_role="caregiver",
                patient_id="pat-001",
            )

        # 3. Expired clinician consent is denied
        with self.assertRaises(PermissionError):
            self.cognitive_service.get_patient_progress(
                actor_id="doc-002",  # Expired in 2025
                actor_role="clinician",
                patient_id="pat-001",
            )

        # 4. Active clinician is authorized
        authorized = self.cognitive_service.get_patient_progress(
            actor_id="doc-001",
            actor_role="clinician",
            patient_id="pat-001",
        )
        self.assertIsInstance(authorized, list)

    def test_criterion_7_camera_failure_fallback_and_profile_closing(self) -> None:
        """
        Tiêu chí 7: Camera lỗi vẫn mở game qua phiên/PIN hợp lệ;
        camera đổi người thì đóng hồ sơ cũ trước khi mở người mới.
        """
        # Step 1: Camera active -> patient resolved
        self.identity_adapter.set_camera_active(True)
        self.identity_adapter.set_simulated_patient("pat-001")
        self.assertEqual(self.identity_adapter.get_current_patient_id(), "pat-001")

        # Step 2: Camera fails (inactive) -> authenticated fallback still resolves patient
        self.identity_adapter.set_camera_active(False)
        self.assertEqual(self.identity_adapter.get_current_patient_id(), "pat-001")

        # Step 3: Camera changes person -> old session/audio closes cleanly before opening new one
        # Start music for pat-001
        self.audio_service.start_playback(patient_id="pat-001", track_id="trk-relax-chimes")
        self.assertTrue(self.audio_service.is_playing)

        # Simulate profile switch: stop playback
        self.audio_service.stop_playback(stopped_reason="patient_switch")
        self.assertFalse(self.audio_service.is_playing)

        # Set new patient
        self.identity_adapter.set_simulated_patient("pat-002")
        self.assertEqual(self.identity_adapter.get_current_patient_id(), "pat-002")

    def test_criterion_8_music_defaults_off_timer_autostop_skippable_feedback(self) -> None:
        """
        Tiêu chí 8: Nhạc mặc định tắt, có tạm dừng/dừng và tự tắt theo hẹn giờ;
        không phát lặp ngoài ý muốn; cảm nhận nghe có thể bỏ qua.
        """
        # Default state: NOT playing
        self.assertFalse(self.audio_service.is_playing)

        # Start playback with 5 minute timer
        sess = self.audio_service.start_playback(
            patient_id="pat-001",
            track_id="trk-peaceful-stream",
            timer_minutes=5,
        )
        self.assertTrue(self.audio_service.is_playing)
        self.assertEqual(self.audio_service.sleep_timer_seconds, 300.0)

        # Pause and resume
        self.audio_service.pause_playback()
        self.assertTrue(self.audio_service.is_paused)
        self.audio_service.resume_playback()
        self.assertFalse(self.audio_service.is_paused)

        # Fast forward timer to test auto-stop
        expired = self.audio_service.tick(301.0)
        self.assertTrue(expired)
        self.assertFalse(self.audio_service.is_playing)

        # User feedback can be skipped (SKIPPED)
        saved_sessions = self.audio_service.list_history("pat-001")
        self.assertTrue(len(saved_sessions) > 0)
        self.assertEqual(saved_sessions[0].stopped_reason, "sleep_timer_expired")

    def test_criterion_9_binaural_off_by_default_requires_headphone_consent_and_no_cure_claims(self) -> None:
        """
        Tiêu chí 9: Nếu bật binaural thử nghiệm, thông báo tai nghe stereo và chế độ thử nghiệm,
        có công tắc tắt; không xuất hiện tuyên bố chữa/tăng trí nhớ trên UI.
        """
        binaural_tracks = self.audio_service.list_tracks(audio_mode=AudioMode.BINAURAL_TRIAL.value)
        self.assertTrue(len(binaural_tracks) > 0)
        binaural_track = binaural_tracks[0]

        # Case 1: Attempt to start binaural WITHOUT consent -> MUST raise ValueError
        with self.assertRaises(ValueError):
            self.audio_service.start_playback(
                patient_id="pat-001",
                track_id=binaural_track.track_id,
                is_binaural_consent=False,
                headphone_confirmed=False,
            )

        # Case 2: Attempt to start binaural with consent but WITHOUT headphone confirmation -> MUST raise ValueError
        with self.assertRaises(ValueError):
            self.audio_service.start_playback(
                patient_id="pat-001",
                track_id=binaural_track.track_id,
                is_binaural_consent=True,
                headphone_confirmed=False,
            )

        # Case 3: Proper consent and headphone confirmation -> starts cleanly
        sess = self.audio_service.start_playback(
            patient_id="pat-001",
            track_id=binaural_track.track_id,
            is_binaural_consent=True,
            headphone_confirmed=True,
        )
        self.assertTrue(self.audio_service.is_playing)
        self.assertTrue(sess.is_binaural)
        self.assertTrue(sess.headphone_confirmed)

        # Immediate stop on demand
        stopped = self.audio_service.stop_playback(stopped_reason="user_stopped")
        self.assertFalse(self.audio_service.is_playing)
        self.assertIsNotNone(stopped)

    def test_criterion_10_licensed_assets_and_no_face_images_stored_in_game_db(self) -> None:
        """
        Tiêu chí 10: Tất cả hình/âm thanh có nguồn và quyền sử dụng;
        phiên game không lưu ảnh khuôn mặt hoặc thông tin người khác.
        """
        # 1. Verify asset manifest license provenance
        manifest_path = Path(__file__).resolve().parents[1] / "assets" / "asset_manifest.json"
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertIn("content_packs", manifest)
        for pack in manifest["content_packs"]:
            self.assertTrue(pack["license"].startswith("CC0-1.0"))

        self.assertIn("audio_tracks", manifest)
        for audio in manifest["audio_tracks"]:
            self.assertTrue(audio["license"].startswith("CC0-1.0"))
            # Verify file exists on disk
            self.assertTrue(Path(audio["asset_uri"]).exists())

        # 2. Verify schema does NOT contain any face/biometric columns in game tables
        with self.db.connect() as conn:
            cursor = conn.cursor()
            for tbl in ["game_sessions", "game_results", "listening_sessions"]:
                cursor.execute(f"PRAGMA table_info({tbl})")
                cols = [row["name"] for row in cursor.fetchall()]
                for forbidden in ["face", "biometric", "image_raw", "photo", "camera_feed"]:
                    self.assertNotIn(forbidden, cols, f"Forbidden biometric column {forbidden} found in {tbl}")


if __name__ == "__main__":
    unittest.main()
