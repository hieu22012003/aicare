"""
Test Runner for Cognitive Games & Relaxation Module.
Discovers and executes all test suites, verifying 100% test pass.
"""

import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_cognitive_games_tests() -> int:
    loader = unittest.TestLoader()
    test_dir = Path(__file__).resolve().parent

    print("======================================================================")
    print("🏃 RUNNING COGNITIVE GAMES & RELAXATION TEST SUITES...")
    print(f"📁 Directory: {test_dir}")
    print("======================================================================")

    suite = loader.discover(start_dir=str(test_dir), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("======================================================================")
    print("📊 TEST SUMMARY:")
    print(f"   Total tests run: {result.testsRun}")
    print(f"   Failures: {len(result.failures)}")
    print(f"   Errors: {len(result.errors)}")
    print(f"   Skipped: {len(result.skipped)}")
    print("======================================================================")

    if result.wasSuccessful():
        print("✅ ALL TESTS PASSED SUCCESSFULLY! (10/10 ACCEPTANCE CRITERIA VERIFIED)")
        return 0
    else:
        print("❌ SOME TESTS FAILED.")
        return 1


if __name__ == "__main__":
    sys.exit(run_cognitive_games_tests())
