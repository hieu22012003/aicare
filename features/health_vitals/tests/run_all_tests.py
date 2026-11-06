"""
Master test runner for the Health Monitoring Module (P0-P2).
Executes all unit and acceptance test suites and outputs a structured summary.
"""

from __future__ import annotations

import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parents[3]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import unittest

from features.health_vitals.tests.test_15_acceptance_criteria import Test15AcceptanceCriteria
from features.health_vitals.tests.test_database_and_seed import TestDatabaseAndSeed
from features.health_vitals.tests.test_permissions_and_care_team import TestPermissionsAndCareTeam


def run_all() -> bool:
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    suite.addTests(loader.loadTestsFromTestCase(TestDatabaseAndSeed))
    suite.addTests(loader.loadTestsFromTestCase(TestPermissionsAndCareTeam))
    suite.addTests(loader.loadTestsFromTestCase(Test15AcceptanceCriteria))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print("HEALTH MONITORING (P0-P2) TEST RUN SUMMARY:")
    print(f"Total Tests Run: {result.testsRun}")
    print(f"Errors: {len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Success: {result.wasSuccessful()}")
    print("=" * 60 + "\n")

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
