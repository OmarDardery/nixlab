"""The compatibility deployer must distinguish activation from recovery failure."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, mock_open, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))
spec = importlib.util.spec_from_file_location('apps_onboarding', BASE / 'scripts/deploy-apps.py')
apps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apps)

OLD = '/nix/store/' + '1' * 32 + '-old'
NEW = '/nix/store/' + '2' * 32 + '-new'


class AppsRecoveryTests(unittest.TestCase):
    def test_target_and_rollback_failures_remain_distinguishable(self):
        for recovery_failed in (False, True):
            with self.subTest(recovery_failed=recovery_failed):
                transport = Mock()
                transport.inspect.return_value = {'profile': OLD, 'active': OLD}
                original = apps.DeploymentError('Guest command failed (exit status 4)')
                recovery = apps.DeploymentError('Guest command failed (exit status 127)') if recovery_failed else None
                transport.activate.side_effect = [original, recovery]
                with patch.object(apps, 'Transport', return_value=transport), \
                        patch.object(apps, 'open_lock', mock_open()), patch.object(apps.fcntl, 'flock'), \
                        self.assertRaises(apps.DeploymentError) as caught:
                    apps.main(NEW)
                self.assertEqual(transport.activate.call_args_list, [call(NEW), call(OLD)])
                self.assertIn('exit status 4', str(caught.exception))
                if recovery_failed:
                    self.assertIn('rollback also failed', str(caught.exception))
                    self.assertIn('exit status 127', str(caught.exception))
                else:
                    self.assertIs(caught.exception, original)


if __name__ == '__main__': unittest.main()
