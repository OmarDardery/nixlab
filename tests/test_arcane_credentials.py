"""Credential consumption, established-state protection and interrupted recovery."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/p) for p in ('scripts','admin')]
import arcane_credentials as guest
import arcane
import runtime as rt
from credential_fixtures import assert_public_clean
from test_direct_install import initial

PASSWORD='Synthetic-Arcane-credential1!'

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.data=root/'arcane';self.data.mkdir()
        self.run=root/'run';self.run.mkdir()
        for key,value in [('DATA',self.data),('RUNTIME',self.run),('LEGACY',root/'legacy-password')]:
            patcher=patch.object(guest,key,value);patcher.start();self.addCleanup(patcher.stop)

    def database(self,users=('admin',)):
        with sqlite3.connect(self.data/'arcane.db') as db:
            db.execute('CREATE TABLE users(username TEXT)')
            db.executemany('INSERT INTO users VALUES (?)',[(u,) for u in users])

    def test_fresh_initializing_existing_corrupt_and_lost_state(self):
        self.assertEqual(guest.state(),'fresh')
        guest.atomic(self.data/'.nixlab-bootstrap.json',json.dumps({'state':'initializing'}))
        self.assertEqual(guest.state(),'initializing')
        self.database()
        self.assertEqual(guest.state(),'initialized')
        guest.atomic(self.data/'.nixlab-bootstrap.json',json.dumps({'state':'initialized'}))
        (self.data/'arcane.db').unlink()
        self.assertEqual(guest.state(),'blocked')
        (self.data/'arcane.db').write_bytes(b'corrupt')
        self.assertEqual(guest.state(),'blocked')

    def test_old_key_with_lost_database_cannot_be_mistaken_for_first_boot(self):
        guest.atomic(self.data/'encryption-key','a'*64)
        self.assertEqual(guest.state(),'blocked')
        guest.atomic(self.data/'.nixlab-bootstrap.json',json.dumps({'format':1,'state':'fresh'}))
        self.assertEqual(guest.state(),'fresh')
        guest.atomic(self.data/'.nixlab-bootstrap.json',json.dumps({'state':'initialized'}))
        self.assertEqual(guest.state(),'blocked')

    def test_crash_during_owned_schema_migration_can_retry_but_unknown_database_cannot(self):
        with sqlite3.connect(self.data/'arcane.db') as db:
            db.execute('CREATE TABLE migration_progress(version INTEGER)')
        self.assertEqual(guest.state(),'blocked')
        guest.atomic(self.data/'.nixlab-bootstrap.json',json.dumps({'state':'initializing'}))
        self.assertEqual(guest.state(),'initializing')
        guest.atomic(self.data/'.nixlab-bootstrap.json',json.dumps({'state':'initialized'}))
        self.assertEqual(guest.state(),'blocked')

    def test_empty_established_database_never_reopens_enrollment(self):
        self.database(())
        guest.atomic(self.data/'.nixlab-bootstrap.json',json.dumps({'state':'initialized'}))
        self.assertEqual(guest.state(),'blocked')

    def test_confirm_consumes_plaintext_on_success_and_authentication_failure(self):
        for failure in (False,True):
            guest.atomic(self.run/'password',PASSWORD)
            with patch.object(guest,'verify',side_effect=ValueError('unavailable') if failure else None):
                if failure:
                    with self.assertRaises(ValueError): guest.confirm()
                else: guest.confirm()
            self.assertFalse((self.run/'password').exists())
            marker=json.loads((self.data/'.nixlab-bootstrap.json').read_text())
            self.assertNotIn(PASSWORD,json.dumps(marker))

    def test_invalid_legacy_input_is_retired_even_when_validation_fails(self):
        guest.atomic(guest.LEGACY,'weak-private-input')
        with self.assertRaises(ValueError): guest.prepare()
        self.assertFalse(guest.LEGACY.exists())
        self.assertFalse((self.run/'password').exists())

    def test_expired_tmpfs_input_is_removed(self):
        guest.atomic(self.run/'password',PASSWORD)
        os.utime(self.run/'password',(0,0));guest.expire()
        self.assertFalse((self.run/'password').exists())

    def test_initialized_prepare_does_not_require_or_reapply_password(self):
        self.database()
        guest.prepare()
        self.assertEqual((self.run/'environment').read_text(),'')

    def test_existing_renamed_identity_is_preserved_and_generated_input_retired(self):
        self.database(('owner',))
        guest.atomic(self.data/'initial-admin-password',PASSWORD)
        guest.prepare()
        self.assertEqual(guest.state(),'initialized')
        self.assertFalse((self.data/'initial-admin-password').exists())
        self.assertEqual((self.run/'environment').read_text(),'')

    def test_missing_input_never_starts_default_password_bootstrap(self):
        with self.assertRaises(ValueError): guest.prepare()
        self.assertFalse((self.run/'environment').exists())

    def test_failed_reset_restores_database_checkpoint(self):
        self.database()
        def failed_reset(value):
            with sqlite3.connect(self.data/'arcane.db') as db:
                db.execute("UPDATE users SET username='changed'")
            raise ValueError('child failed')
        with patch.object(guest,'command') as command,patch.object(guest,'api',return_value=True), \
                patch.object(guest,'terminal_reset',side_effect=failed_reset):
            with self.assertRaises(ValueError): guest.reset(PASSWORD)
        with sqlite3.connect(self.data/'arcane.db') as db:
            self.assertEqual(db.execute('SELECT username FROM users').fetchone()[0],'admin')
        self.assertFalse((self.data.parent/'arcane-credential-checkpoint/pending').exists())
        self.assertFalse((self.run/'reset-active').exists())
        self.assertEqual(command.call_args.args,('systemctl','start','docker-arcane.service'))

    def test_boot_recovers_checkpoint_after_crash_without_plaintext(self):
        self.database()
        checkpoint=self.data.parent/'arcane-credential-checkpoint';checkpoint.mkdir()
        with sqlite3.connect(self.data/'arcane.db') as db,sqlite3.connect(checkpoint/'arcane.db') as target:
            db.backup(target)
            db.execute("UPDATE users SET username='changed'")
        guest.atomic(checkpoint/'pending','pending')
        guest.recover_pending(stop=False)
        with sqlite3.connect(self.data/'arcane.db') as db:
            self.assertEqual(db.execute('SELECT username FROM users').fetchone()[0],'admin')
        self.assertFalse((checkpoint/'pending').exists())

    def test_existing_state_cannot_be_enrolled_even_with_valid_input(self):
        self.database()
        with patch.object(sys,'argv',['credentials','enroll']),patch.object(sys,'stdin',io.StringIO(PASSWORD)):
            with self.assertRaises(ValueError): guest.main()
        self.assertFalse((self.run/'password').exists())
        self.assertEqual(guest.state(),'initialized')

    def test_opt_in_requires_current_password_and_never_silently_reuses(self):
        with patch('accounts.matches',return_value=False):
            with self.assertRaises(ValueError): arcane.review_secrets({'reuseArcanePassword':True,'arcaneCurrent':PASSWORD},'owner')
        with patch('accounts.matches',return_value=True):
            body={'reuseArcanePassword':True,'arcaneCurrent':PASSWORD}
            self.assertEqual(arcane.review_secrets(body,'owner'),{'arcanePassword':PASSWORD})
            self.assertNotIn('arcaneCurrent',body)
            self.assertEqual(arcane.review_secrets({'arcaneCurrent':PASSWORD},'owner'),{})
            with self.assertRaises(ValueError):
                arcane.review_secrets({'reuseArcanePassword':True,'arcaneCurrent':PASSWORD,'secrets':{'arcanePassword':PASSWORD}},'owner')

    def test_host_discards_legacy_input_for_existing_database(self):
        path=self.run/'host-password';path.write_text(PASSWORD)
        with patch.dict(rt.SECRET_PATHS,arcanePassword=path), \
                patch.object(arcane,'credential_status',return_value={'state':'initialized'}), \
                patch.object(arcane,'guest') as command:
            self.assertEqual(arcane.onboard(initial()),{'ok':True,'existing':True})
        self.assertFalse(path.exists())
        self.assertEqual(command.call_args.args,('systemctl','restart','docker-arcane.service'))

    def test_password_control_characters_are_rejected(self):
        for value in (PASSWORD+'\0',PASSWORD+'\n',PASSWORD+'\x1b'):
            with self.assertRaises(ValueError): guest.policy(value)

class GatewayTests(unittest.TestCase):
    def test_retired_arcane_reset_endpoint_is_unavailable_for_every_role(self):
        import main
        from unittest.mock import Mock
        with patch.object(main,'BoundedHttpsServer') as server, \
             patch.object(main.ssl,'SSLContext'), \
             patch.object(main,'reply') as reply, \
             patch.object(main,'broker_request') as broker:
            main.web()
            handler=object.__new__(server.call_args.args[1])
            handler.access=Mock(return_value='localhost:9440')
            handler.headers={'Origin':'https://localhost:9440'}
            handler.path='/api/arcane/reset'
            for role in ('viewer', 'operator', 'admin'):
                handler.session=Mock(return_value={'role':role,'username':'owner'})
                broker.reset_mock()
                with patch.object(main,'json_body',return_value={'current':PASSWORD,'password':PASSWORD}):
                    handler.do_POST()
                broker.assert_not_called()
                self.assertIn(reply.call_args.args[1], (403, 404))
                assert_public_clean(self,reply.call_args.args[2])


class PublicationTests(unittest.TestCase):
    def test_sops_keeps_durable_ownership_but_initial_arcane_input_is_transient(self):
        settings=initial();settings['secrets']['enable']=True
        self.assertEqual(rt.encode_secrets({'arcanePassword':PASSWORD},settings),{'arcanePassword':PASSWORD})
        with self.assertRaises(ValueError): rt.encode_secrets({'resticPassword':PASSWORD},settings)

    def test_empty_or_missing_reset_input_never_invokes_privileged_child(self):
        with patch.object(arcane.subprocess,'run') as run:
            for value in (None,'',123,'weak'):
                with self.assertRaises(ValueError): arcane.supplied_password(initial(),value,'reset')
            run.assert_not_called()

    def test_recovery_archive_excludes_transient_bootstrap_but_keeps_durable_secrets(self):
        import recovery
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);bootstrap=root/'bootstrap';durable=root/'restic'
            rt.atomic(bootstrap,PASSWORD);rt.atomic(durable,'durable-private-input')
            with patch.object(rt,'STATE',root),patch.dict(rt.SECRET_PATHS,arcanePassword=bootstrap,resticPassword=durable), \
                    patch.object(recovery,'settings',return_value=initial()):
                files=recovery.secret_files()
            self.assertNotIn(bootstrap,files.values())
            self.assertIn(durable,files.values())

    def test_old_transaction_rollback_cannot_republish_consumed_bootstrap(self):
        import worker
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'retired-plaintext'
            rt.write_json(root/'recovery/manifest.json',{'files':[{'name':'secret-arcanePassword','path':str(target),'existed':True,'mode':0o600}]})
            real_path=Path
            with patch.object(worker,'Path',side_effect=lambda value: root/'empty-network' if str(value)=='/run/NetworkManager/system-connections' else real_path(value)):
                worker.restore_files(root)
            self.assertFalse(target.exists())
