"""
Tests for GameEngine logic:
- Deterministic board generation
- Exact pair counts (Criterion 2)
- No odd cards
- Sequence order-based grading (Criterion 3)
"""

import unittest
from collections import Counter

from features.music_care.cognitive_games.domain.models import DifficultyLevel
from features.music_care.cognitive_games.services.game_engine import GameEngine


class TestGameEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.items_pool = [
            {"id": "item-1", "name": "Bát cơm", "icon": "🍚"},
            {"id": "item-2", "name": "Ấm trà", "icon": "🫖"},
            {"id": "item-3", "name": "Đôi đũa", "icon": "🥢"},
            {"id": "item-4", "name": "Cái quạt", "icon": "🪭"},
            {"id": "item-5", "name": "Cây kéo", "icon": "✂️"},
            {"id": "item-6", "name": "Kính lão", "icon": "👓"},
            {"id": "item-7", "name": "Đồng hồ", "icon": "⏰"},
            {"id": "item-8", "name": "Quyển sách", "icon": "📖"},
        ]

    def test_card_matching_exact_pairs_easy(self) -> None:
        # Easy: 3 pairs = 6 cards
        board = GameEngine.generate_card_matching_board(
            items_pool=self.items_pool,
            difficulty=DifficultyLevel.EASY,
            seed=12345,
        )
        self.assertEqual(len(board), 6)
        counts = Counter(card.item_id for card in board)
        self.assertEqual(len(counts), 3)
        for item_id, count in counts.items():
            self.assertEqual(count, 2, f"Item {item_id} does not appear exactly twice")

    def test_card_matching_exact_pairs_medium(self) -> None:
        # Medium: 4 pairs = 8 cards
        board = GameEngine.generate_card_matching_board(
            items_pool=self.items_pool,
            difficulty=DifficultyLevel.MEDIUM,
            seed=54321,
        )
        self.assertEqual(len(board), 8)
        counts = Counter(card.item_id for card in board)
        self.assertEqual(len(counts), 4)
        for item_id, count in counts.items():
            self.assertEqual(count, 2)

    def test_card_matching_exact_pairs_hard(self) -> None:
        # Hard: 6 pairs = 12 cards
        board = GameEngine.generate_card_matching_board(
            items_pool=self.items_pool,
            difficulty=DifficultyLevel.HARD,
            seed=99999,
        )
        self.assertEqual(len(board), 12)
        counts = Counter(card.item_id for card in board)
        self.assertEqual(len(counts), 6)
        for item_id, count in counts.items():
            self.assertEqual(count, 2)

    def test_card_matching_exact_pairs_expert(self) -> None:
        # Expert: 8 pairs = 16 cards
        board = GameEngine.generate_card_matching_board(
            items_pool=self.items_pool,
            difficulty=DifficultyLevel.EXPERT,
            seed=77777,
        )
        self.assertEqual(len(board), 16)
        counts = Counter(card.item_id for card in board)
        self.assertEqual(len(counts), 8)
        for item_id, count in counts.items():
            self.assertEqual(count, 2)

    def test_seed_determinism(self) -> None:
        # Same seed must produce identical card orders
        board1 = GameEngine.generate_card_matching_board(self.items_pool, DifficultyLevel.EASY, seed=42)
        board2 = GameEngine.generate_card_matching_board(self.items_pool, DifficultyLevel.EASY, seed=42)
        order1 = [c.item_id for c in board1]
        order2 = [c.item_id for c in board2]
        self.assertEqual(order1, order2)

        # Different seed produces different arrangement
        board3 = GameEngine.generate_card_matching_board(self.items_pool, DifficultyLevel.EASY, seed=999)
        order3 = [c.item_id for c in board3]
        self.assertNotEqual(order1, order3)

    def test_sequence_length_per_difficulty(self) -> None:
        # Length 2 for easy, 3 for medium, 4 for hard, 5 for expert
        t_easy, _ = GameEngine.generate_sequence_round(self.items_pool, DifficultyLevel.EASY, seed=10)
        self.assertEqual(len(t_easy), 2)

        t_med, _ = GameEngine.generate_sequence_round(self.items_pool, DifficultyLevel.MEDIUM, seed=10)
        self.assertEqual(len(t_med), 3)

        t_hard, _ = GameEngine.generate_sequence_round(self.items_pool, DifficultyLevel.HARD, seed=10)
        self.assertEqual(len(t_hard), 4)

        t_expert, _ = GameEngine.generate_sequence_round(self.items_pool, DifficultyLevel.EXPERT, seed=10)
        self.assertEqual(len(t_expert), 5)

    def test_sequence_grading_strictly_by_order(self) -> None:
        target = [
            {"id": "item-1", "name": "Bát cơm"},
            {"id": "item-2", "name": "Ấm trà"},
            {"id": "item-3", "name": "Đôi đũa"},
        ]

        # Case 1: Exact order -> Perfect
        perfect_input = [
            {"id": "item-1", "name": "Bát cơm"},
            {"id": "item-2", "name": "Ấm trà"},
            {"id": "item-3", "name": "Đôi đũa"},
        ]
        is_perfect, correct_pos, _ = GameEngine.grade_sequence(target, perfect_input)
        self.assertTrue(is_perfect)
        self.assertEqual(correct_pos, 3)

        # Case 2: Same set, WRONG ORDER -> NOT perfect (Criterion 3)
        reversed_input = [
            {"id": "item-3", "name": "Đôi đũa"},
            {"id": "item-2", "name": "Ấm trà"},
            {"id": "item-1", "name": "Bát cơm"},
        ]
        is_perfect, correct_pos, _ = GameEngine.grade_sequence(target, reversed_input)
        self.assertFalse(is_perfect)
        self.assertEqual(correct_pos, 1)  # Only item-2 at index 1 is in correct position

        # Case 3: Completely wrong items -> 0 correct
        wrong_input = [
            {"id": "item-4", "name": "Cái quạt"},
            {"id": "item-5", "name": "Cây kéo"},
            {"id": "item-6", "name": "Kính lão"},
        ]
        is_perfect, correct_pos, _ = GameEngine.grade_sequence(target, wrong_input)
        self.assertFalse(is_perfect)
        self.assertEqual(correct_pos, 0)


if __name__ == "__main__":
    unittest.main()
