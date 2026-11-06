"""
Test Runner for Medication Adherence Module.
Executes all unit and integration tests and prints an itemized report.
"""

import sys
import unittest
from pathlib import Path

# Ensure project root is on sys.path
root = Path(__file__).resolve().parents[4]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


def run_tests() -> bool:
    test_dir = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(test_dir), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("\n" + "=" * 60)
    print(f"MEDICATION ADHERENCE TEST RUN SUMMARY:")
    print(f"Total Tests Run: {result.testsRun}")
    print(f"Errors: {len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Success: {result.wasSuccessful()}")
    print("=" * 60)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
