"""Reproducible Windows regression run with isolated data and SMTP blocked."""
import os
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
os.chdir(root)
os.environ['AI_CARE_TEST_MODE'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

with tempfile.TemporaryDirectory(prefix='ai-care-verify-') as data:
    os.environ['AI_CARE_TEST_DATA_DIR'] = data
    # Native ML libraries must initialize before Qt on Windows.
    from shared import config
    import unittest
    import smtplib
    from unittest.mock import patch
    with patch.object(smtplib,'SMTP',side_effect=AssertionError('SMTP network blocked by test runner')):
        suite = unittest.defaultTestLoader.discover('tests')
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    import logging
    logging.shutdown()
sys.exit(0 if result.wasSuccessful() else 1)
