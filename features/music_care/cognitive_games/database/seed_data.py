"""
Seed data generator for Cognitive Games & Relaxation Module.
Seeds 2 patients, 2 caregivers, 2 clinicians, game types, content packs, tracks,
and sample game & listening sessions with realistic metadata.
Ensures full idempotency (INSERT OR IGNORE / INSERT OR REPLACE) and clean separation
between game engine catalogs and demo user data.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from features.music_care.cognitive_games.database.connection import DatabaseManager

logger = logging.getLogger("CognitiveGamesSeed")


def seed_catalogs_only(db: DatabaseManager) -> None:
    """Populates game types, content packs, and music tracks idempotently."""
    manifest_path = Path(__file__).resolve().parents[1] / "assets" / "asset_manifest.json"
    if not manifest_path.exists():
        from features.music_care.cognitive_games.assets.generate_assets import build_all_assets
        build_all_assets()

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))

    with db.connect() as conn:
        cursor = conn.cursor()

        # 1. GAME TYPES
        game_types_data = [
            ("gt-card-match", "card_matching", "Lật Hình Ghép Cặp", "Trò chơi tìm cặp hình tương ứng nhằm rèn luyện trí nhớ không gian ngắn hạn.", "v1.0", 1),
            ("gt-seq-recall", "sequence_recall", "Nhớ Trình Tự", "Ghi nhớ và tái hiện đúng thứ tự các biểu tượng xuất hiện.", "v1.0", 1),
        ]
        cursor.executemany(
            "INSERT OR IGNORE INTO game_types (game_type_id, code, name, description, rules_version, is_active) VALUES (?, ?, ?, ?, ?, ?)",
            game_types_data,
        )

        # 2. CONTENT PACKS (Linked to game types)
        for cp in manifest_data.get("content_packs", []):
            gtype = "gt-card-match" if cp["pack_id"] != "pack-fruits" else "gt-seq-recall"
            cursor.execute(
                """
                INSERT OR REPLACE INTO content_packs (pack_id, game_type_id, title, theme, assets_manifest, license, locale, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cp["pack_id"],
                    gtype,
                    cp["title"],
                    cp["theme"],
                    json.dumps({"items": cp["items"]}, ensure_ascii=False),
                    cp["license"],
                    "vi-VN",
                    "1.0",
                ),
            )

        # 3. MUSIC TRACKS
        for trk in manifest_data.get("audio_tracks", []):
            cursor.execute(
                """
                INSERT OR IGNORE INTO music_tracks (track_id, title, artist_attribution, asset_uri, license, audio_mode, duration_seconds, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trk["track_id"],
                    trk["title"],
                    trk["artist_attribution"],
                    str(Path(__file__).resolve().parents[1] / "assets" / "audio" / Path(trk["asset_uri"]).name),
                    trk["license"],
                    trk["audio_mode"],
                    trk["duration_seconds"],
                    trk["description"],
                ),
            )


def seed_database(db: DatabaseManager, include_demo_users: Optional[bool] = None) -> None:
    """Populates the database with catalogs and optional realistic demo user data."""
    # Always ensure static catalogs (game types, content packs, tracks) exist
    seed_catalogs_only(db)

    # Determine whether to seed demo users
    if include_demo_users is None:
        is_memory = ":memory:" in str(getattr(db, "db_path", ""))
        is_test_mode = os.environ.get("AI_CARE_TEST_MODE") == "1"
        include_demo_users = is_memory or is_test_mode

    if not include_demo_users:
        logger.info("Cognitive Games database initialized with catalogs only (clean user data).")
        return

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with db.connect() as conn:
        cursor = conn.cursor()

        # Check if already has demo users
        cursor.execute("SELECT COUNT(*) as cnt FROM users")
        if cursor.fetchone()["cnt"] > 0:
            logger.info("Database already contains users. Skipping demo user seed.")
            return

        logger.info("Seeding Cognitive Games demo users and historical sessions...")

        # 1. USERS
        users_data = [
            ("usr-pat-001", "an.nguyen@example.com", "0901234567", "Cụ Nguyễn Văn An", 1, now_iso),
            ("usr-pat-002", "binh.tran@example.com", "0902345678", "Cụ Trần Thị Bình", 1, now_iso),
            ("usr-cg-001", "binh.nguyen.cg@example.com", "0911222333", "Nguyễn Văn Bình (Con trai)", 1, now_iso),
            ("usr-cg-002", "hoa.tran.cg@example.com", "0922333444", "Trần Thị Hoa (Con gái)", 1, now_iso),
            ("usr-doc-001", "contact-91e5c40b@example.com", "0988777666", "BS.CKII Lê Quang", 1, now_iso),
            ("usr-doc-002", "contact-4bcc03e4@example.com", "0977666555", "ThS.BS Phạm Mai", 1, now_iso),
        ]
        cursor.executemany(
            "INSERT OR IGNORE INTO users (user_id, email, phone, full_name, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            users_data,
        )

        # 2. PATIENTS
        patients_data = [
            ("pat-001", "usr-pat-001", "Cụ Nguyễn Văn An", "1948-03-12", "Nam", "Asia/Ho_Chi_Minh", "ACTIVE", "Thích nghe chuông gió và chủ đề cây cảnh thiên nhiên."),
            ("pat-002", "usr-pat-002", "Cụ Trần Thị Bình", "1952-08-25", "Nữ", "Asia/Ho_Chi_Minh", "ACTIVE", "Thích lật hình đồ dùng gia đình."),
        ]
        cursor.executemany(
            "INSERT OR IGNORE INTO patients (patient_id, user_id, full_name, date_of_birth, gender, timezone, status, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            patients_data,
        )

        # 3. CAREGIVERS
        caregivers_data = [
            ("cg-001", "usr-cg-001", 1, 1),
            ("cg-002", "usr-cg-002", 1, 1),
        ]
        cursor.executemany(
            "INSERT OR IGNORE INTO caregivers (caregiver_id, user_id, email_verified, phone_verified) VALUES (?, ?, ?, ?)",
            caregivers_data,
        )

        # 4. CLINICIANS
        clinicians_data = [
            ("doc-001", "usr-doc-001", "Bệnh viện Lão khoa Trung Ương", "Lão khoa & Phục hồi chức năng", "CCHN-001298-BYT", 1),
            ("doc-002", "usr-doc-002", "Bệnh viện Bạch Mai", "Thần kinh học", "CCHN-004567-BYT", 1),
        ]
        cursor.executemany(
            "INSERT OR IGNORE INTO clinicians (clinician_id, user_id, workplace, specialty, license_number, is_verified) VALUES (?, ?, ?, ?, ?, ?)",
            clinicians_data,
        )

        # 5. PATIENT CAREGIVERS
        patient_caregivers_data = [
            ("pcg-001", "pat-001", "cg-001", "Con trai", 1, "2026-01-01T00:00:00Z", "2027-12-31T23:59:59Z", 1),
            ("pcg-002", "pat-002", "cg-002", "Con gái", 1, "2026-01-01T00:00:00Z", "2027-12-31T23:59:59Z", 1),
        ]
        cursor.executemany(
            """
            INSERT OR IGNORE INTO patient_caregivers (
                patient_caregiver_id, patient_id, caregiver_id, relationship_type,
                can_view_health, valid_from, valid_to, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            patient_caregivers_data,
        )

        # 6. CARE TEAM (Dr. Quang active for A & B; Dr. Mai active for A, EXPIRED for B)
        care_team_data = [
            ("ct-001", "pat-001", "doc-001", "Bác sĩ phục hồi chức năng phụ trách", "2026-01-01T00:00:00Z", "2027-12-31T23:59:59Z", "GRANTED", 1),
            ("ct-002", "pat-002", "doc-001", "Bác sĩ phục hồi chức năng phụ trách", "2026-01-01T00:00:00Z", "2027-12-31T23:59:59Z", "GRANTED", 1),
            ("ct-003", "pat-001", "doc-002", "Bác sĩ hết hạn quyền theo dõi", "2025-01-01T00:00:00Z", "2025-12-31T23:59:59Z", "EXPIRED", 0),
            ("ct-004", "pat-002", "doc-002", "Bác sĩ hội chẩn trước đây (hết hạn)", "2025-01-01T00:00:00Z", "2025-12-31T23:59:59Z", "EXPIRED", 0),
        ]
        cursor.executemany(
            """
            INSERT OR IGNORE INTO care_team (
                care_team_id, patient_id, clinician_id, role_description,
                valid_from, valid_to, consent_status, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            care_team_data,
        )

        # 7. HISTORICAL SESSIONS FOR PATIENT A (pat-001)
        s1_start = (now - timedelta(days=3, hours=4)).isoformat()
        s1_end = (now - timedelta(days=3, hours=4, minutes=-3)).isoformat()
        cursor.execute(
            """
            INSERT OR IGNORE INTO game_sessions (
                session_id, patient_id, game_type_id, pack_id, difficulty,
                seed, started_at, ended_at, status, active_seconds, help_count,
                rules_version, client_request_id, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses-pat1-001", "pat-001", "gt-card-match", "pack-household", "easy",
                101, s1_start, s1_end, "completed", 125.0, 1,
                "v1.0", "req-pat1-001", "Hoàn thành vui vẻ với 1 lần gợi ý",
            ),
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO game_results (
                session_id, attempts, correct_count, total_tasks, accuracy_percent,
                user_feedback, enjoyment, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ses-pat1-001", 5, 3, 3, 100.0, "easy", "enjoyed", json.dumps({"flips": 10, "hints_used": 1})),
        )

        s2_start = (now - timedelta(days=2, hours=5)).isoformat()
        s2_end = (now - timedelta(days=2, hours=5, minutes=-4)).isoformat()
        cursor.execute(
            """
            INSERT OR IGNORE INTO game_sessions (
                session_id, patient_id, game_type_id, pack_id, difficulty,
                seed, started_at, ended_at, status, active_seconds, help_count,
                rules_version, client_request_id, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses-pat1-002", "pat-001", "gt-card-match", "pack-nature", "medium",
                102, s2_start, s2_end, "completed", 185.0, 2,
                "v1.0", "req-pat1-002", "Tập trung tốt",
            ),
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO game_results (
                session_id, attempts, correct_count, total_tasks, accuracy_percent,
                user_feedback, enjoyment, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ses-pat1-002", 7, 4, 4, 100.0, "fair", "enjoyed", json.dumps({"flips": 14, "hints_used": 2})),
        )

        s3_start = (now - timedelta(days=1, hours=3)).isoformat()
        s3_end = (now - timedelta(days=1, hours=3, minutes=-2)).isoformat()
        cursor.execute(
            """
            INSERT OR IGNORE INTO game_sessions (
                session_id, patient_id, game_type_id, pack_id, difficulty,
                seed, started_at, ended_at, status, active_seconds, help_count,
                rules_version, client_request_id, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses-pat1-003", "pat-001", "gt-seq-recall", "pack-fruits", "easy",
                103, s3_start, s3_end, "completed", 75.0, 0,
                "v1.0", "req-pat1-003", "Nhớ đúng ngay lần đầu",
            ),
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO game_results (
                session_id, attempts, correct_count, total_tasks, accuracy_percent,
                user_feedback, enjoyment, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ses-pat1-003", 2, 2, 2, 100.0, "easy", "enjoyed", json.dumps({"reviews_count": 1})),
        )

        s4_start = (now - timedelta(hours=6)).isoformat()
        s4_end = (now - timedelta(hours=6, minutes=-1)).isoformat()
        cursor.execute(
            """
            INSERT OR IGNORE INTO game_sessions (
                session_id, patient_id, game_type_id, pack_id, difficulty,
                seed, started_at, ended_at, status, active_seconds, help_count,
                rules_version, client_request_id, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses-pat1-004-ab", "pat-001", "gt-card-match", "pack-nature", "hard",
                104, s4_start, s4_end, "abandoned", 42.0, 1,
                "v1.0", "req-pat1-004-ab", "Người dùng bận nghỉ tay giữa chừng",
            ),
        )

        # 8. HISTORICAL SESSIONS FOR PATIENT B (pat-002)
        sb_start = (now - timedelta(days=1, hours=6)).isoformat()
        sb_end = (now - timedelta(days=1, hours=6, minutes=-3)).isoformat()
        cursor.execute(
            """
            INSERT OR IGNORE INTO game_sessions (
                session_id, patient_id, game_type_id, pack_id, difficulty,
                seed, started_at, ended_at, status, active_seconds, help_count,
                rules_version, client_request_id, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses-pat2-001", "pat-002", "gt-card-match", "pack-household", "easy",
                201, sb_start, sb_end, "completed", 140.0, 0,
                "v1.0", "req-pat2-001", "Cụ Bình chơi vui vẻ",
            ),
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO game_results (
                session_id, attempts, correct_count, total_tasks, accuracy_percent,
                user_feedback, enjoyment, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ses-pat2-001", 6, 3, 3, 100.0, "easy", "enjoyed", json.dumps({"flips": 12})),
        )

        # 9. HISTORICAL LISTENING SESSIONS FOR PATIENT A
        l1_start = (now - timedelta(days=2, hours=1)).isoformat()
        l1_end = (now - timedelta(days=2, hours=1, minutes=-10)).isoformat()
        cursor.execute(
            """
            INSERT OR IGNORE INTO listening_sessions (
                listen_id, patient_id, track_id, started_at, ended_at,
                played_seconds, is_binaural, headphone_confirmed, feedback, stopped_reason, client_request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "lis-pat1-001", "pat-001", "trk-relax-chimes", l1_start, l1_end,
                600.0, 0, 0, "pleasant", "timer_expired", "req-lis-001",
            ),
        )

        logger.info("Seed data completed successfully for Cognitive Games.")


seed_cognitive_games_database = seed_database
