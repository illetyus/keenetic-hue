"""Explicit offline unit scope; no live router, BLE adapter or root mutation."""
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
NAMES=('test_core','test_query_capacity','test_journal_capacity','test_admin_cli',
       'test_admin_health','test_build_release','test_release_install','test_setup_code',
       'test_backup_password_policy','test_storage_payload','test_archive_limits',
       'test_cloud_context','test_cloud_fixture','test_optional_cloud','test_tunnel_api')

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(NAMES))
    print(json.dumps({'testsRun':result.testsRun,'failures':len(result.failures),
                      'errors':len(result.errors),'skipped':len(result.skipped)}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
