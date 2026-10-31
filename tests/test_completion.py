"""Safety boundaries for disk installation, source publication, identities and recovery."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts'), str(BASE / 'iso')]
import accounts
import catalog
import install_disk
import operations
import recovery
import runtime as rt
import upgrades
from main import Auth
from locking import open_lock


class DiskTests(unittest.TestCase):
    def test_missing_installation_tools_fail_before_any_subprocess_or_erasure(self):
        with patch.object(install_disk.shutil,'which',return_value=None),patch.object(install_disk,'run') as run:
            with self.assertRaisesRegex(ValueError,'tools missing'):install_disk.preflight('/unused')
        run.assert_not_called()

    def test_data_disk_preparation_refuses_wrong_phrase_before_commands(self):
        with patch.dict(sys.modules, {'disk_tools': install_disk}):
            import disks as data_disks
        with patch.object(data_disks.os,'geteuid',return_value=0),patch.object(data_disks,'Path') as path, \
                patch.object(data_disks.sys.stdin,'isatty',return_value=True), \
                patch.object(install_disk,'disks',return_value=[self.disk]),patch.object(install_disk,'run') as run, \
                patch('builtins.input',side_effect=['1','archive','data','wrong']),patch('builtins.print'):
            path.return_value.exists.return_value=True
            with self.assertRaisesRegex(ValueError,'confirmation'):
                data_disks.prepare()
        run.assert_not_called()

    def setUp(self):
        self.disk = {'path': '/dev/test', 'type': 'disk', 'size': 64 * 1024**3, 'model': 'test', 'serial': 'one',
                     'wwn': 'abc', 'maj:min': '8:0', 'ro': False, 'mountpoints': [None], 'children': [], 'byId': '/dev/disk/by-id/test-serial'}

    def test_mounted_descendant_live_disk_read_only_and_non_disk_are_refused(self):
        self.assertTrue(install_disk.eligible(self.disk))
        for change in ({'ro': True}, {'busy': True}, {'type': 'loop'}, {'mountpoints': ['/']}, {'size': 1024},
                       {'children': [{'mountpoints': ['/iso']}]}):
            value = self.disk | change
            self.assertFalse(install_disk.eligible(value))
            with self.assertRaises(ValueError): install_disk.plan(value, 'nixlab', True)

    def test_hotplug_identity_or_new_mount_invalidates_review(self):
        proposal = install_disk.plan(self.disk, 'nixlab', True)
        for change in ({'serial': 'other'}, {'maj:min': '8:16'}, {'size': 128 * 1024**3}, {'mountpoints': ['/mnt']}):
            with self.assertRaises(ValueError): install_disk.verify_target(proposal, [self.disk | change])
        with self.assertRaises(ValueError): install_disk.verify_target(proposal, [])

    def test_invalid_confirmation_never_executes_commands(self):
        with patch.object(install_disk, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'confirmation'):
                install_disk.install(install_disk.plan(self.disk, 'nixlab', True), 'yes', '/module')
        run.assert_not_called()

    def test_installed_configuration_uses_selected_firmware_and_safe_host_name(self):
        efi = install_disk.configuration(install_disk.plan(self.disk, 'home-server', True))
        bios = install_disk.configuration(install_disk.plan(self.disk, 'home-server', False))
        self.assertIn('systemd-boot.enable = true', efi)
        self.assertNotIn('/dev/test', efi)
        self.assertIn('grub.device = "/dev/disk/by-id/test-serial"', bios)
        for name in ('x"; bad', '../host', 'bad-'):
            with self.assertRaises(ValueError): install_disk.plan(self.disk, name, True)


class PlatformUpgradeTests(unittest.TestCase):
    def test_real_git_transport_preserves_staged_machine_files_and_records_exact_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder); upstream=folder/'upstream';root=folder/'root';source=folder/'source'
            upstream.mkdir();root.mkdir()
            for repo in (upstream,root):subprocess.run(['git','init','-q',str(repo)],check=True)
            rt.atomic(upstream/'flake.nix','{}');rt.atomic(upstream/'flake.lock','{}');rt.atomic(upstream/'admin/main.py','# reviewed')
            rt.atomic(upstream/'machine/settings.json','DO NOT IMPORT')
            subprocess.run(['git','-C',str(upstream),'add','.'],check=True)
            subprocess.run(['git','-C',str(upstream),'-c','user.name=Test','-c','user.email=test@example.invalid',
                            '-c','commit.gpgsign=false','commit','-qm','test revision'],check=True)
            revision=subprocess.check_output(['git','-C',str(upstream),'rev-parse','HEAD'],text=True).strip()
            rt.atomic(root/'flake.nix','old');subprocess.run(['git','-C',str(root),'add','.'],check=True)
            rt.write_json(root/'machine/format.json',{'version':1});rt.write_json(root/'machine/settings.json',{'mine':True})
            rt.snapshot(source,root);rt.atomic(source/'machine/modules/staged.nix','{}')
            with tempfile.TemporaryFile(mode='w+') as log,patch.object(upgrades,'UPSTREAM',str(upstream)):
                changes=upgrades.prepare(source,revision,log)
            self.assertEqual(rt.read_json(source/'machine/settings.json'),{'mine':True})
            self.assertEqual((source/'machine/modules/staged.nix').read_text(),'{}')
            self.assertEqual(upgrades.metadata(source)['revision'],revision)
            self.assertTrue(any(c['path']=='admin/main.py' for c in changes))
            self.assertIn('admin/main.py',{n for n,_ in rt.source_files(source)})

    def test_publication_and_rollback_preserve_machine_and_remove_obsolete_platform_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, old = [Path(folder) / n for n in ('root', 'candidate', 'old')]
            root.mkdir(); subprocess.run(['git', 'init', '-q', str(root)], check=True)
            rt.atomic(root / 'flake.nix', 'old')
            rt.atomic(root / 'obsolete.nix', '{}')
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            rt.write_json(root / 'machine/format.json', {'version': 1})
            rt.write_json(root / 'machine/settings.json', {'local': 'retain'})
            rt.atomic(root / 'machine/modules/mine.nix', 'local-code')
            rt.snapshot(old, root); rt.snapshot(candidate, root)
            rt.atomic(candidate / 'flake.nix', 'new')
            (candidate / 'obsolete.nix').unlink()
            rt.atomic(candidate / 'new-feature.nix', '{}')
            names = rt.read_json(candidate / '.nixlab-files.json')
            names = [n for n in names if n != 'obsolete.nix'] + ['new-feature.nix']
            rt.write_json(candidate / '.nixlab-files.json', names)
            upgrades.publish(candidate, root)
            self.assertEqual((root / 'flake.nix').read_text(), 'new')
            self.assertFalse((root / 'obsolete.nix').exists())
            self.assertEqual((root / 'machine/modules/mine.nix').read_text(), 'local-code')
            upgrades.publish(old, root)
            self.assertEqual((root / 'flake.nix').read_text(), 'old')
            self.assertTrue((root / 'obsolete.nix').exists())
            self.assertFalse((root / 'new-feature.nix').exists())
            self.assertEqual(rt.read_json(root / 'machine/settings.json'), {'local': 'retain'})

    def test_failed_publication_can_remove_partially_published_additions(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, old = [Path(folder) / n for n in ('root', 'candidate', 'old')]
            root.mkdir(); subprocess.run(['git', 'init', '-q', str(root)], check=True)
            rt.atomic(root / 'flake.nix', 'old'); subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            rt.snapshot(old, root); rt.snapshot(candidate, root)
            rt.atomic(candidate / 'a-new.nix', '{}'); rt.atomic(candidate / 'z-new.nix', '{}')
            rt.write_json(candidate / '.nixlab-files.json', ['flake.nix', 'a-new.nix', 'z-new.nix'])
            atomic = rt.atomic
            def fail(path, *args, **kwargs):
                if path == root / 'z-new.nix': raise OSError('power interrupted')
                return atomic(path, *args, **kwargs)
            with patch.object(rt, 'atomic', side_effect=fail), self.assertRaises(OSError): upgrades.publish(candidate, root)
            self.assertTrue((root / 'a-new.nix').exists())
            upgrades.publish(old, root)
            self.assertFalse((root / 'a-new.nix').exists())

    def test_invalid_revision_never_fetches(self):
        with patch.object(upgrades.subprocess, 'run') as run:
            with self.assertRaises(ValueError): upgrades.prepare(Path('/unused'), '--upload-pack=bad', io.StringIO())
        run.assert_not_called()

    def test_personal_paths_are_never_platform_files(self):
        for path in ('machine/settings.json', 'local/settings.json', 'site-modules/x.nix', 'secrets/key', '../bad', '/etc/passwd', '.git/config', 'hardware-configuration.nix'):
            self.assertFalse(upgrades.platform_path(path))
        self.assertTrue(upgrades.platform_path('platform-modules/features/hello.nix'))


class LockTests(unittest.TestCase):
    def test_opening_a_lock_never_truncates_existing_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'lock';path.write_text('retain')
            with open_lock(path): pass
            self.assertEqual(path.read_text(),'retain')
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_symlink_and_hard_link_cannot_redirect_privileged_lock_open(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);target=folder/'protected';target.write_text('retain')
            link=folder/'lock';link.symlink_to(target)
            with self.assertRaises(OSError): open_lock(link)
            link.unlink();os.link(target,link)
            with self.assertRaises(ValueError): open_lock(link)
            self.assertEqual(target.read_text(),'retain')


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.patch = patch.object(rt, 'STATE', self.state); self.patch.start(); self.addCleanup(self.patch.stop)
        self.env = patch.dict(os.environ, {'NIXLAB_TEST': '1'}); self.env.start(); self.addCleanup(self.env.stop)
        rt.write_json(self.state / 'auth.json', rt.password_record('administrator-test-password'))

    def test_role_matrix_rejects_code_restore_and_cross_project_operator_actions(self):
        for role in ('viewer', 'operator'):
            for path, body in [('/api/review', {}), ('/api/recovery-export', {}), ('/api/users', {}),
                               ('/api/operation', {'action': 'restore'}), ('/api/operation', {'action': 'start', 'project': 'other'})]:
                with self.assertRaises(accounts.AuthorizationError): accounts.authorize({'role': role}, path, body)
        accounts.authorize({'role': 'operator'}, '/api/operation', {'action': 'start', 'project': 'lab'})
        accounts.authorize({'role': 'viewer'}, '/api/password', {})

    def test_deleted_or_downgraded_account_revokes_existing_sessions(self):
        accounts.update('helper', 'operator', 'operator-test-password')
        auth = Auth(); token, _ = auth.login('operator-test-password', '127.0.0.1', 'helper')
        self.assertEqual(auth.get('nixlab_session=' + token)['role'], 'operator')
        accounts.update('helper', 'viewer')
        self.assertIsNone(auth.get('nixlab_session=' + token))
        token, _ = auth.login('operator-test-password', '127.0.0.1', 'helper')
        accounts.update('helper', delete=True)
        self.assertIsNone(auth.get('nixlab_session=' + token))

    def test_recovery_administrator_cannot_be_deleted_and_passwords_are_not_listed(self):
        with self.assertRaises(ValueError): accounts.update('admin', delete=True)
        accounts.update('observer', 'viewer', 'observer-test-password')
        self.assertNotIn('hash', json.dumps(accounts.list_users()))
        self.assertNotIn('observer-test-password', (self.state / 'users.json').read_text())


class RecoveryKitTests(unittest.TestCase):
    def test_configuration_kit_excludes_runtime_secrets_and_stages_without_activation(self):
        with tempfile.TemporaryDirectory() as folder:
            root, state, output = [Path(folder) / n for n in ('repo', 'state', 'kit')]
            root.mkdir(); state.mkdir(); subprocess.run(['git', 'init', '-q', str(root)], check=True)
            rt.atomic(root / 'flake.nix', '{}'); subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            rt.atomic(state / 'ca.key', 'PRIVATE-KEY')
            with patch.object(rt, 'ROOT', root), patch.object(rt, 'STATE', state): result = recovery.export(output)
            manifest, entries = recovery.inspect(Path(result['configuration']))
            self.assertEqual(manifest['kind'], 'configuration')
            self.assertEqual(set(entries), {'config/flake.nix'})
            destination = Path(folder) / 'staged'
            recovery.stage(Path(result['configuration']), destination)
            self.assertEqual((destination / 'config/flake.nix').read_text(), '{}')
            self.assertTrue((state / 'ca.key').exists())
            with self.assertRaises(ValueError): recovery.stage(Path(result['configuration']), destination)

    def test_unsafe_archive_paths_and_checksum_mismatch_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'bad.tar.gz'
            for name in ('../escape', '/etc/passwd', 'config/../../escape'):
                with tarfile.open(archive, 'w:gz') as stream:
                    item = tarfile.TarInfo(name); item.size = 1; stream.addfile(item, io.BytesIO(b'x'))
                with self.assertRaises(ValueError): recovery.inspect(archive)
            with tarfile.open(archive, 'w:gz') as stream:
                data = json.dumps({'format': 1, 'kind': 'configuration', 'files': {'config/missing': 'bad'}}).encode()
                item = tarfile.TarInfo('manifest.json'); item.size = len(data); stream.addfile(item, io.BytesIO(data))
            with self.assertRaisesRegex(ValueError, 'checksum'): recovery.inspect(archive)

    def test_secret_export_requires_valid_recipient_before_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'kit'
            with self.assertRaises(ValueError): recovery.export(output, None, True)
            self.assertFalse(output.exists())


class GuestOperationTests(unittest.TestCase):
    def test_restart_recovery_waits_for_live_mutations(self):
        import backup
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);state=folder/'state';ops=operations.Operations(folder/'root',state)
            pending=folder/'pending.json';rt.write_json(pending,[{'project':'lab','name':'guest'}])
            with patch.object(rt,'STATE',state),patch.object(operations,'INCUS_LOCK',folder/'incus.lock'), \
                    patch.object(backup,'PENDING',pending),patch.object(backup,'recover') as recover:
                with rt.locked():ops.recover_interrupted()
                recover.assert_not_called()
                ops.recover_interrupted()
                recover.assert_called_once()

    def test_interrupted_export_cleanup_preserves_completed_archive(self):
        import backup
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);state=folder/'state';ops=operations.Operations(folder/'root',state);job_id='d'*32
            directory=ops.directory/job_id;directory.mkdir()
            rt.write_json(directory/'job.json',{'id':job_id,'action':'export','status':'failed'})
            archive=directory/'guest.tar.gz';archive.write_bytes(b'complete')
            partial=archive.with_suffix('.partial.tar.gz');partial.write_bytes(b'partial')
            with patch.object(rt,'STATE',state),patch.object(operations,'INCUS_LOCK',folder/'incus.lock'),patch.object(backup,'PENDING',folder/'no-pending'):
                ops.recover_interrupted()
            self.assertFalse(partial.exists())
            self.assertEqual(archive.read_bytes(),b'complete')

    def test_off_host_restore_does_not_require_original_instance_to_exist(self):
        with tempfile.TemporaryDirectory() as folder:
            ops=operations.Operations(Path(folder)/'root',Path(folder)/'state');archive_id='a'*32
            directory=ops.directory/archive_id;directory.mkdir()
            archive=directory/'guest.tar.gz';archive.write_bytes(b'export-content')
            rt.write_json(directory/'job.json',{'id':archive_id,'sha256':operations.file_hash(archive)})
            job={'action':'restore','project':'lab','name':'lost-original','target':'restored','archive':archive_id}
            def lookup(project, guest):
                self.assertEqual(guest, 'restored', 'Original must not be queried')
                return {'status': 'Stopped', 'config': {}}
            with patch.object(operations,'instance',side_effect=lookup), \
                    patch.object(operations,'guests',return_value=[]),patch.object(operations,'incus') as incus:
                result=ops.perform(job)
            self.assertEqual(result['restoredInstance'],'restored')
            self.assertIn('import',incus.call_args.args)

    def test_failed_export_keeps_restart_intent_and_attempts_recovery(self):
        import backup
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);ops=operations.Operations(folder/'root',folder/'state');job_id='b'*32
            (ops.directory/job_id).mkdir()
            def fail(*args,**kwargs):
                if args[0]=='export':raise ValueError('export failed')
                self.assertNotIn('--force',args)
                return ''
            with patch.object(backup,'PENDING',folder/'pending.json'),patch.object(backup,'recover') as recover, \
                    patch.object(backup,'save_pending') as pending,patch.object(operations,'instance',return_value={'status':'Running'}), \
                    patch.object(operations,'incus',side_effect=fail):
                with self.assertRaisesRegex(ValueError,'export failed'):ops.perform({'id':job_id,'action':'export','project':'lab','name':'guest'})
                pending.assert_called_once_with([{'name':'guest','project':'lab'}])
                self.assertEqual(recover.call_count,2)

    def test_missing_archive_mount_never_creates_fallback_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);ops=operations.Operations(folder/'root',folder/'state');target=folder/'missing/exports/on-demand'
            job={'id':'c'*32,'archiveRoot':str(target),'archiveMount':{'path':str(folder/'missing'),'uuid':'expected'}}
            with patch.object(operations.subprocess,'run',return_value=subprocess.CompletedProcess([],1,stdout='')):
                with self.assertRaisesRegex(ValueError,'filesystem is missing'):ops.archive_path(job,create=True)
            self.assertFalse(target.exists())

    def test_shared_writable_volume_blocks_restore_release(self):
        with tempfile.TemporaryDirectory() as folder:
            ops=operations.Operations(Path(folder)/'root',Path(folder)/'state')
            devices={'data':{'type':'disk','pool':'guests','source':'database-data','path':'/data'}}
            with patch.object(operations,'instance',return_value={'expanded_devices':devices}), \
                    patch.object(operations,'incus',return_value=json.dumps([{'name':'original','expanded_devices':devices}])):
                with self.assertRaisesRegex(ValueError,'shared with another'):
                    ops.perform({'action':'release-restore','project':'lab','name':'copy'})

    def test_remote_prefixes_paths_and_shell_arguments_are_rejected(self):
        for value in ('remote:guest', 'x/y', '../guest', '--force', 'x; echo', ''):
            with self.assertRaises(ValueError): operations.name(value)

    def test_restored_guests_cannot_start_before_release(self):
        with tempfile.TemporaryDirectory() as folder:
            ops = operations.Operations(Path(folder) / 'root', Path(folder) / 'state')
            current = {'config': {'user.nixlab.restore-review': 'required'}}
            with patch.object(operations, 'instance', return_value=current), patch.object(operations, 'incus') as incus:
                with self.assertRaisesRegex(ValueError, 'Review restored'):
                    ops.perform({'action': 'start', 'project': 'lab', 'name': 'restored'})
            incus.assert_not_called()

    def test_copy_refuses_existing_destination_and_sets_quarantine_at_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            ops = operations.Operations(Path(folder) / 'root', Path(folder) / 'state')
            job = {'action': 'snapshot-copy', 'project': 'lab', 'name': 'source', 'target': 'new', 'snapshot': 's1'}
            with patch.object(operations, 'instance', return_value={}), patch.object(operations, 'guests', return_value=[{'name': 'new'}]), patch.object(operations, 'incus') as incus:
                with self.assertRaisesRegex(ValueError, 'already exists'): ops.perform(job)
                incus.assert_not_called()
            with patch.object(operations, 'instance', return_value={'status': 'Stopped', 'config': {}}), patch.object(operations, 'guests', return_value=[]), patch.object(operations, 'incus') as incus:
                result = ops.perform(job)
                self.assertTrue(result['requiresReview'])
                args = incus.call_args.args
                self.assertIn('boot.autostart=false', args)
                self.assertIn('user.nixlab.restore-review=required', args)
                self.assertNotIn('--refresh', args)

    def test_restore_release_refuses_duplicate_static_address(self):
        with tempfile.TemporaryDirectory() as folder:
            ops = operations.Operations(Path(folder) / 'root', Path(folder) / 'state')
            device = {'eth0': {'ipv4.address': '10.77.0.12'}}
            current = {'expanded_devices': device}
            with patch.object(operations, 'instance', return_value=current), patch.object(operations, 'incus', return_value=json.dumps([{'name': 'original', 'expanded_devices': device}])) as incus:
                with self.assertRaisesRegex(ValueError, 'conflicts'):
                    ops.perform({'action': 'release-restore', 'project': 'lab', 'name': 'copy'})
                self.assertEqual(incus.call_count, 1)

    def test_unknown_actions_are_refused_before_any_worker_is_started(self):
        with tempfile.TemporaryDirectory() as folder:
            ops = operations.Operations(Path(folder) / 'root', Path(folder) / 'state')
            with patch.object(operations.subprocess, 'run') as run:
                with self.assertRaises(ValueError): ops.submit({'action': 'exec', 'name': 'guest'})
            run.assert_not_called()

    def test_packaged_catalog_is_static_and_names_are_unique(self):
        items = catalog.recipes()
        self.assertGreaterEqual(len(items), 4)
        self.assertEqual(len(items), len({i['id'] for i in items}))
        self.assertTrue(any(i['id'] == 'laptop' for i in items))
