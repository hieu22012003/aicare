"""
Deterministic Game Engine for Card Matching and Sequence Recall.
Ensures reproducible boards, exact pair counts (Criterion 2), and strict sequence ordering (Criterion 3).
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple

from features.music_care.cognitive_games.domain.models import CardItem, DifficultyLevel


class GameEngine:
    """Core deterministic rules engine for elderly memory activities."""

    @staticmethod
    def get_card_pair_count(difficulty: str | DifficultyLevel) -> int:
        diff_str = str(difficulty.value if isinstance(difficulty, DifficultyLevel) else difficulty).lower()
        if "8" in diff_str or "expert" in diff_str or "challenge" in diff_str:
            return 8  # 8 pairs -> 16 cards
        elif "6" in diff_str or "hard" in diff_str:
            return 6  # 6 pairs -> 12 cards
        elif "4" in diff_str or "medium" in diff_str:
            return 4  # 4 pairs -> 8 cards
        elif "easy" in diff_str or "3" in diff_str:
            return 3  # 3 pairs -> 6 cards
        return 4

    @staticmethod
    def get_sequence_length(difficulty: str | DifficultyLevel) -> int:
        diff_str = str(difficulty.value if isinstance(difficulty, DifficultyLevel) else difficulty).lower()
        if "5" in diff_str or "expert" in diff_str or "challenge" in diff_str:
            return 5  # Length 5
        elif "4" in diff_str or "hard" in diff_str:
            return 4  # Length 4
        elif "3" in diff_str or "medium" in diff_str:
            return 3  # Length 3
        elif "easy" in diff_str or "2" in diff_str:
            return 2  # Length 2
        return 3

    @classmethod
    def generate_card_matching_board(
        cls,
        items_pool: List[Dict[str, str]],
        difficulty: str | DifficultyLevel,
        seed: int,
        theme: str = "household",
    ) -> List[CardItem]:
        """
        Generates card board with exact pairs (Criterion 2).
        Guarantees:
        - Exactly pair_count items selected from items_pool.
        - Each item duplicated EXACTLY twice (total cards = pair_count * 2).
        - No odd items or lone cards.
        - Deterministic shuffle based on seed.
        """
        pair_count = cls.get_card_pair_count(difficulty)
        if len(items_pool) < pair_count:
            raise ValueError(f"Items pool size ({len(items_pool)}) is smaller than required pair count ({pair_count})")

        rng = random.Random(seed)
        selected_items = rng.sample(items_pool, pair_count)

        # Duplicate each item to create pairs
        cards_raw = []
        for item in selected_items:
            cards_raw.append(item)
            cards_raw.append(item)

        rng.shuffle(cards_raw)

        board: List[CardItem] = []
        for idx, item in enumerate(cards_raw):
            board.append(
                CardItem(
                    card_index=idx,
                    item_id=item["id"],
                    name=item["name"],
                    icon_symbol=item["icon"],
                    theme=theme,
                    is_flipped=False,
                    is_matched=False,
                )
            )
        return board

    @classmethod
    def generate_sequence_round(
        cls,
        items_pool: List[Dict[str, str]],
        difficulty: str | DifficultyLevel,
        seed: int,
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """
        Generates sequence round (Criterion 3):
        - Target sequence in exact presentation order.
        - Pool of choices including target items and distractors (6 to 8 items).
        """
        seq_len = cls.get_sequence_length(difficulty)
        if len(items_pool) < seq_len:
            raise ValueError(f"Items pool size ({len(items_pool)}) is smaller than sequence length ({seq_len})")

        rng = random.Random(seed)
        # Select target sequence (distinct items in order)
        target_seq = rng.sample(items_pool, seq_len)

        # Pool of choices (target items + distractors, up to 8 choices)
        choices_count = min(len(items_pool), max(seq_len + 3, 6))
        remaining = [it for it in items_pool if it not in target_seq]
        needed_distractors = choices_count - seq_len
        distractors = rng.sample(remaining, min(len(remaining), needed_distractors))

        all_choices = target_seq + distractors
        rng.shuffle(all_choices)

        return target_seq, all_choices

    @staticmethod
    def grade_sequence(
        target_seq: List[Dict[str, str]],
        user_input_seq: List[Dict[str, str]],
    ) -> Tuple[bool, int, str]:
        """
        Grades sequence strictly by order (Criterion 3).
        Returns: (is_perfect_match, correct_positions_count, feedback_msg)
        """
        if len(user_input_seq) != len(target_seq):
            return False, 0, f"Số lượng hình chưa đủ ({len(user_input_seq)}/{len(target_seq)} hình)."

        correct_positions = 0
        for i in range(len(target_seq)):
            if target_seq[i]["id"] == user_input_seq[i]["id"]:
                correct_positions += 1

        is_perfect = (correct_positions == len(target_seq))
        if is_perfect:
            msg = "Xuất sắc! Bạn đã nhớ đúng hoàn toàn thứ tự các hình."
        else:
            msg = f"Đúng {correct_positions}/{len(target_seq)} vị trí. Cùng thử lại để rèn luyện nhé!"

        return is_perfect, correct_positions, msg
