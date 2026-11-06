"""
User & Caregiver Profile Service for AI Rehabilitation & Safety Assistant.
Handles local profile persistence, caregiver contact registration, and email validation.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.config import SHARED_DATA_DIR, USERS_DIR

logger = logging.getLogger("UserService")

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class UserService:
    """Manages Elderly User profiles and their Caregiver emergency alert recipients."""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.users_dir: Path = (data_dir or USERS_DIR).resolve()
        self.users_dir.mkdir(parents=True, exist_ok=True)
        self.legacy_patients_file: Path = SHARED_DATA_DIR / "patients.json"
        self.legacy_patient_file: Path = SHARED_DATA_DIR / "patient.json"

    @staticmethod
    def validate_email(email: str) -> bool:
        """Validate if email matches standard format (e.g., contact-9fa9045b@example.com)."""
        if not email or not isinstance(email, str):
            return False
        clean = email.strip()
        return bool(EMAIL_REGEX.match(clean))

    def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve user profile by user_id."""
        if not user_id:
            return None
        uid = str(user_id).strip()

        # 1. Check data/users/{uid}.json
        user_file = self.users_dir / f"{uid}.json"
        if user_file.exists():
            try:
                with open(user_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error("Error reading user file %s: %s", user_file, e)

        # 2. Check legacy shared/data/patients.json
        if self.legacy_patients_file.exists():
            try:
                with open(self.legacy_patients_file, "r", encoding="utf-8") as f:
                    patients = json.load(f)
                if uid in patients:
                    return patients[uid]
            except Exception as e:
                logger.debug("Error checking legacy patients: %s", e)

        return None

    def get_caregiver_email(self, user_id: str) -> Optional[str]:
        """Resolve the emergency alert recipient email for a given user_id."""
        profile = self.get_user_profile(user_id)
        if not profile:
            return None

        caregiver = profile.get("caregiver")
        if isinstance(caregiver, dict):
            email = caregiver.get("email", "").strip()
            if self.validate_email(email):
                return email

        # Legacy fallback if directly stored
        legacy_email = profile.get("caregiver_email", "").strip()
        if self.validate_email(legacy_email):
            return legacy_email

        return None

    def save_user_profile(
        self,
        user_id: str,
        name: str,
        age: int = 70,
        gender: str = "Nam",
        notes: str = "",
        caregiver_name: str = "",
        caregiver_relationship: str = "Người thân",
        caregiver_email: str = "",
        image_count: int = 0,
    ) -> Dict[str, Any]:
        """Save user profile locally with validated caregiver information."""
        uid = str(user_id).strip()
        if not uid:
            raise ValueError("user_id cannot be empty")

        clean_email = caregiver_email.strip()
        if clean_email and not self.validate_email(clean_email):
            raise ValueError(f"Invalid caregiver email format: '{clean_email}'")

        existing = self.get_user_profile(uid) or {}

        user_data: Dict[str, Any] = {
            **existing,  # Preserve legacy and extension fields during profile edits.
            "user_id": uid,
            "patient_id": uid,
            "name": name.strip() or f"Người dùng {uid}",
            "age": int(age),
            "gender": gender,
            "notes": notes.strip(),
            "caregiver": {
                "name": caregiver_name.strip() or "Người chăm sóc",
                "relationship": caregiver_relationship.strip() or "Người thân",
                "email": clean_email,
            },
            "registered_at": existing.get("registered_at", datetime.now().isoformat()),
            "last_updated": datetime.now().isoformat(),
            "image_count": image_count or existing.get("image_count", 0),
        }

        from shared.services.credential_security import protect_profile_credentials
        user_data = protect_profile_credentials(user_data)

        # 1. Save to data/users/{uid}.json
        user_file = self.users_dir / f"{uid}.json"
        try:
            with open(user_file, "w", encoding="utf-8") as f:
                json.dump(user_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Failed saving user profile to %s: %s", user_file, e)

        # 2. Sync to legacy shared/data/patients.json for backwards compatibility
        try:
            patients = {}
            if self.legacy_patients_file.exists():
                with open(self.legacy_patients_file, "r", encoding="utf-8") as f:
                    patients = json.load(f)
            patients[uid] = user_data
            with open(self.legacy_patients_file, "w", encoding="utf-8") as f:
                json.dump(patients, f, ensure_ascii=False, indent=2)

            with open(self.legacy_patient_file, "w", encoding="utf-8") as f:
                json.dump(user_data, f, ensure_ascii=False, indent=2)

            logger.info("Saved user profile (contact details omitted)")
        except Exception as e:
            logger.error("Failed syncing legacy patient files: %s", e)

        return user_data

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Return list of all registered users."""
        users: Dict[str, Dict[str, Any]] = {}

        # 1. Read from data/users/
        for p in self.users_dir.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                uid = data.get("user_id") or data.get("patient_id")
                if uid:
                    users[str(uid)] = data
            except Exception:
                pass

        # 2. Merge from legacy shared/data/patients.json
        if self.legacy_patients_file.exists():
            try:
                with open(self.legacy_patients_file, "r", encoding="utf-8") as f:
                    patients = json.load(f)
                for uid, pdata in patients.items():
                    if uid not in users:
                        users[uid] = pdata
            except Exception:
                pass

        return list(users.values())

    def delete_user(self, user_id: str) -> bool:
        """Delete user profile from local storage."""
        uid = str(user_id).strip()
        user_file = self.users_dir / f"{uid}.json"
        if user_file.exists():
            try:
                user_file.unlink()
            except Exception as e:
                logger.error("Failed to delete %s: %s", user_file, e)

        if self.legacy_patients_file.exists():
            try:
                with open(self.legacy_patients_file, "r", encoding="utf-8") as f:
                    patients = json.load(f)
                if uid in patients:
                    del patients[uid]
                    with open(self.legacy_patients_file, "w", encoding="utf-8") as f:
                        json.dump(patients, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error("Failed removing user from legacy file: %s", e)

        return True
