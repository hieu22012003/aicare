"""Run regression tests in an isolated runtime, with SMTP forbidden by default."""
import argparse
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['QT_QPA_PLATFORM']='offscreen'
import mediapipe
from shared import config


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--pattern',default='test_fall_pipeline_v2.py')
    parser.add_argument('--output',default='docs/test-reports/fall_detection/checks')
    args=parser.parse_args()
    out=Path(args.output); out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fall_checks_') as tmp:
        # Patch constants before importing test/application modules.
        sandbox=Path(tmp)
        for name,relative in dict(USERS_DIR='users',DATA_DIR='data',SESSIONS_DIR='sessions',FALL_EVENTS_DIR='fall_events',
                                  SHARED_DIR='shared',SHARED_DATA_DIR='shared/data',SHARED_RESULTS_DIR='shared/results',
                                  SHARED_ALERTS_DIR='shared/alerts',SHARED_RECORDINGS_DIR='shared/recordings',SHARED_REPORTS_DIR='shared/reports',LOGS_DIR='logs').items():
            path=sandbox/relative; path.mkdir(parents=True,exist_ok=True); setattr(config,name,path)
        # Keep bundled models read-only at the original resource paths.
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler); handler.close()
        logging.basicConfig(level=logging.ERROR,filename=str(out/'runtime.log'),encoding='utf-8')
        with patch('smtplib.SMTP',side_effect=AssertionError('External SMTP forbidden by check harness')), patch('smtplib.SMTP_SSL',side_effect=AssertionError('External SMTP_SSL forbidden')):
            suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=args.pattern)
            with (out/'unittest.txt').open('w',encoding='utf-8') as stream:
                result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
        summary=dict(pattern=args.pattern,tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                     skipped=len(result.skipped),successful=result.wasSuccessful(),isolated_runtime=True,
                     failure_names=[str(test) for test,_ in result.failures],error_names=[str(test) for test,_ in result.errors])
        (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        print(json.dumps(summary),flush=True)
        return 0 if result.wasSuccessful() else 1


if __name__=='__main__': sys.exit(main())
