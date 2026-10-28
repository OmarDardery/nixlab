"""Schema, secret boundaries, snapshot integrity and real HTTPS authentication tests."""
import base64
import contextlib
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
import runtime as rt
import model
from controller import Manager
from main import Auth

DEFAULTS = json.loads((BASE / 'tests/fixtures/defaults.json').read_text())
KEY = 'ssh-ed25519 ' + base64.b64encode(struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + b'\x01' * 32).decode() + ' test-only'

def settings():
    value = copy.deepcopy(DEFAULTS)
    value.update(cpuVendor='intel', system='x86_64-linux', hostName='fixture', adminKeys=[KEY])
    value['setup']['enable'] = True
    value['management'].update(interface='eth0', address='192.0.2.10', prefixLength=24,
                               trustedCIDRs=['192.0.2.0/24'], gateway='192.0.2.1', dns=['192.0.2.1'])
    value['guests'].update(cidr='10.77.0.0/24', gatewayCIDR='10.77.0.1/24', appsAddress='10.77.0.10', dhcpRange='10.77.0.100-10.77.0.199')
    value['maintenance']['timeZone'] = 'UTC'
    value['apps']['enable'] = False
    return value

HOST = {'architecture':'x86_64','cpuVendor':'intel','kvm':True,
        'interfaces':[{'name':'eth0','wireless':False,'addresses':['192.0.2.10/24']}],
        'routes':[{'dst':'192.0.2.0/24','dev':'eth0'}],
        'disks':[{'uuid':'aaaa-bbbb','fstype':'ext4','mountpoints':[],'path':'/dev/test','type':'part','size':1000000}]}

class ModelTests(unittest.TestCase):
    def test_lightweight_defaults_and_monitoring_limits(self):
        self.assertFalse(DEFAULTS['apps']['enable'])
        self.assertFalse(DEFAULTS['monitoring']['history'])
        current = settings()
        proposed = copy.deepcopy(current)
        proposed['monitoring'].update(history=True, retentionDays=7, scrapeSeconds=120)
        self.assertEqual(model.validate_settings(proposed, current, HOST, False)['monitoring']['retentionDays'], 7)
        for key, invalid in (('retentionDays', 0), ('retentionDays', 91), ('scrapeSeconds', 14), ('scrapeSeconds', 301)):
            proposed = copy.deepcopy(current)
            proposed['monitoring'][key] = invalid
            with self.assertRaisesRegex(ValueError, 'Monitoring'):
                model.validate_settings(proposed, current, HOST, False)

    def test_unknown_nix_code_field_rejected(self):
        current=settings(); proposed=copy.deepcopy(current); proposed['rawNix']='builtins.readFile /etc/shadow'
        with self.assertRaisesRegex(ValueError,'Unknown setting'):
            model.validate_settings(proposed,current,HOST)

    def test_existing_filesystem_becomes_pool_with_stable_source(self):
        current=settings(); proposed=copy.deepcopy(current)
        proposed['mounts']=[{'name':'guests','uuid':'aaaa-bbbb','path':'/srv/nixlab/guests','fsType':'ext4','role':'guests','required':True,'readOnly':False}]
        out=model.validate_settings(proposed,current,HOST,require_devices=False)
        self.assertEqual(out['storage']['source'],'/srv/nixlab/guests/incus')
        self.assertTrue(out['ready'])

    def test_wrong_filesystem_and_system_disk_rejected(self):
        current=settings(); proposed=copy.deepcopy(current)
        proposed['mounts']=[{'name':'data','uuid':'aaaa-bbbb','path':'/srv/nixlab/data','fsType':'ext4','role':'data','required':False,'readOnly':False}]
        host=copy.deepcopy(HOST);host['disks'][0]['mountpoints']=['/']
        with self.assertRaisesRegex(ValueError,'already mounted'):
            model.validate_settings(proposed,current,host)
        host['disks'][0]['mountpoints']=[];host['disks'][0]['fstype']='xfs'
        with self.assertRaisesRegex(ValueError,'differs'):
            model.validate_settings(proposed,current,host)

    def test_mount_path_cannot_shadow_host_files(self):
        current=settings();proposed=copy.deepcopy(current)
        proposed['mounts']=[{'name':'data','uuid':'aaaa-bbbb','path':'/etc','fsType':'ext4','role':'data','required':False,'readOnly':False}]
        with self.assertRaisesRegex(ValueError,'mount only'):
            model.validate_settings(proposed,current,HOST,False)

    def test_wifi_uplink_cannot_be_disabled(self):
        current=settings();host=copy.deepcopy(HOST);host['interfaces'][0]['wireless']=True
        with self.assertRaisesRegex(ValueError,'Wi-Fi cannot be disabled'):
            model.validate_settings(copy.deepcopy(current),current,host)

    def test_setup_cannot_remove_its_recovery_path(self):
        current=settings();proposed=copy.deepcopy(current);proposed['setup']['enable']=False
        with self.assertRaisesRegex(ValueError,'recovery'):
            model.validate_settings(proposed,current,HOST,False)

class SecretTests(unittest.TestCase):
    def test_backup_credentials_cannot_execute_shell_substitutions(self):
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'executed';value='$(touch '+str(marker)+')'
            encoded=rt.encode_secrets({'resticEnvironment':json.dumps({'AWS_SECRET_ACCESS_KEY':value})},settings())
            env=Path(folder)/'credentials';env.write_text(encoded['resticEnvironment'])
            result=subprocess.check_output(['sh','-c','. "$1"; printf "%s" "$AWS_SECRET_ACCESS_KEY"','sh',str(env)],text=True)
            self.assertEqual(result,value);self.assertFalse(marker.exists())

    def test_command_environment_and_mismatched_tunnel_refused(self):
        with self.assertRaises(ValueError):
            rt.encode_secrets({'resticEnvironment':'{"LD_PRELOAD":"/evil"}'},settings())
        with self.assertRaises(ValueError):
            rt.encode_secrets({'tunnelCredentials':json.dumps({'AccountTag':'x','TunnelSecret':'abc','TunnelID':'wrong'})},settings())

    def test_password_hash_roundtrip_and_no_plaintext(self):
        record=rt.password_record('a-long-test-password')
        self.assertTrue(rt.password_matches('a-long-test-password',record))
        self.assertFalse(rt.password_matches('wrong',record))
        self.assertNotIn('a-long-test-password',json.dumps(record))

class SnapshotTests(unittest.TestCase):
    def test_untracked_modules_are_hashed_but_credentials_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            rt.atomic(root/'site-modules/disks/archive.nix', '{}')
            first = rt.digest_source(root)
            rt.atomic(root/'site-modules/disks/archive.nix', '{ changed = true; }')
            self.assertNotEqual(first, rt.digest_source(root))
            rt.atomic(root/'site-modules/password.txt', 'DO-NOT-COPY')
            with self.assertRaisesRegex(ValueError, 'Only Nix'):
                rt.snapshot(root/'candidate', root)

    def test_symlinked_module_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'root'; root.mkdir()
            outside = Path(folder)/'outside'; outside.mkdir()
            (root/'site-modules').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                rt.module_files(root)

    def test_untracked_credentials_excluded_and_snapshot_integrity_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'repo';root.mkdir();subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'flake.nix').write_text('{}');subprocess.run(['git','-C',str(root),'add','flake.nix'],check=True)
            (root/'secret.txt').write_text('DO-NOT-COPY')
            rt.write_json(root/'local/settings.json',settings())
            destination=Path(folder)/'snapshot';rt.snapshot(destination,root)
            self.assertFalse((destination/'secret.txt').exists())
            digest=rt.digest_source(destination)
            (destination/'flake.nix').write_text('{ changed = true; }')
            self.assertNotEqual(digest,rt.digest_source(destination))

    def test_hardware_import_excludes_non_nix_secrets(self):
        spec=importlib.util.spec_from_file_location('nixlab_install',BASE/'installer/install.py')
        install=importlib.util.module_from_spec(spec);spec.loader.exec_module(install)
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'old';source.mkdir();target=Path(folder)/'new'
            (source/'configuration.nix').write_text('{}');(source/'hardware-configuration.nix').write_text('{}')
            (source/'private-key').write_text('DO-NOT-COPY')
            install.copy_nix_modules(source,target)
            self.assertFalse((target/'private-key').exists())
            self.assertTrue((target/'hardware-configuration.nix').exists())

class TransactionTests(unittest.TestCase):
    def test_review_keeps_live_settings_and_secrets_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'repo';root.mkdir();state=Path(folder)/'state';state.mkdir()
            subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'flake.nix').write_text('{}');subprocess.run(['git','-C',str(root),'add','flake.nix'],check=True)
            current=settings();rt.write_json(root/'local/settings.json',current)
            manager=Manager(root,state)
            with patch.object(rt,'STATE',state),patch('controller.inventory',return_value=HOST),patch.object(manager,'spawn') as spawn:
                output=manager.review({'hostName':'changed'}, {'portainerPassword':'new-initial-password'})
                self.assertEqual(manager.settings()['hostName'],'fixture')
                directory=manager.job_path(output['id'])
                snapshot=(directory/'source/local/settings.json').read_text()
                self.assertNotIn('new-initial-password',snapshot)
                self.assertNotIn('new-initial-password',(directory/'job.json').read_text())
                self.assertTrue((directory/'secrets.json').exists())
                spawn.assert_called_once_with('build',output['id'])

    def test_confirmation_expiry_does_not_accept_or_delete_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder);manager=Manager(state/'repo',state)
            job_id='a'*32;directory=state/'jobs'/job_id;directory.mkdir()
            rt.write_json(directory/'job.json',{'status':'awaiting-confirmation'})
            rt.write_json(state/'pending.json',{'id':job_id,'deadline':time.time()-1})
            with patch.object(rt,'STATE',state):
                with self.assertRaisesRegex(ValueError,'expired'):
                    manager.confirm(job_id)
            self.assertTrue((state/'pending.json').exists())

    def test_source_drift_blocks_apply(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder);manager=Manager(state/'repo',state);job_id='b'*32
            directory=state/'jobs'/job_id;directory.mkdir()
            rt.write_json(directory/'job.json',{'status':'ready','baseDigest':'old'})
            rt.write_json(state/'active.json',{'id':job_id})
            with patch.object(rt,'STATE',state),patch.object(rt,'digest_source',return_value='new'):
                with self.assertRaisesRegex(ValueError,'changed since review'):
                    manager.apply(job_id)

class CandidateLifecycleTests(unittest.TestCase):
    def test_superseded_candidate_cannot_activate(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder);manager=Manager(state/'repo',state);old='f'*32;new='1'*32
            rt.write_json(state/'jobs'/old/'job.json',{'status':'ready'})
            rt.write_json(state/'active.json',{'id':new})
            with patch.object(rt,'STATE',state):
                with self.assertRaisesRegex(ValueError,'superseded'):
                    manager.apply(old)

    def test_discard_releases_candidate_root_and_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder);manager=Manager(state/'repo',state);job_id='2'*32;directory=state/'jobs'/job_id
            rt.write_json(directory/'job.json',{'status':'ready'})
            rt.atomic(directory/'secrets.json','SECRET')
            target=state/'retained-os';target.write_text('OS');(directory/'system').symlink_to(target)
            with patch.object(rt,'STATE',state):manager.cancel(job_id)
            self.assertEqual(rt.read_json(directory/'job.json')['status'],'cancelled')
            self.assertFalse((directory/'system').exists());self.assertFalse((directory/'secrets.json').exists())
            self.assertTrue(target.exists())

class RecoveryTests(unittest.TestCase):
    def test_module_recovery_restores_changes_and_removes_new_files(self):
        import worker
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'repo'; root.mkdir()
            state = Path(folder)/'state'; state.mkdir()
            job = state/'jobs/example'; job.mkdir(parents=True)
            rt.atomic(root/'site-modules/disks/archive.nix', 'original')
            rt.write_json(root/'local/settings.json', {'old': True})
            with patch.object(rt, 'ROOT', root), patch.object(rt, 'STATE', state), patch.object(rt, 'SECRET_PATHS', {}):
                worker.save_recovery(job)
                rt.atomic(root/'site-modules/disks/archive.nix', 'changed')
                rt.atomic(root/'site-modules/new.nix', 'new')
                worker.restore_files(job)
                worker.restore_files(job)  # Recovery is idempotent.
            self.assertEqual((root/'site-modules/disks/archive.nix').read_text(), 'original')
            self.assertFalse((root/'site-modules/new.nix').exists())

    def test_confirmed_transaction_does_not_rollback_after_interrupted_cleanup(self):
        import worker
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder);job_id='c'*32
            rt.write_json(state/'jobs'/job_id/'job.json',{'status':'confirmed'})
            rt.write_json(state/'pending.json',{'id':job_id,'deadline':time.time()-1})
            with patch.object(rt,'STATE',state),patch.object(worker,'rollback') as rollback:
                worker.guard()
                rollback.assert_not_called()
            self.assertFalse((state/'pending.json').exists())

    def test_expired_pending_activation_invokes_recovery(self):
        import worker
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder);job_id='d'*32
            rt.write_json(state/'jobs'/job_id/'job.json',{'status':'awaiting-confirmation'})
            rt.write_json(state/'pending.json',{'id':job_id,'deadline':time.time()-1})
            with patch.object(rt,'STATE',state),patch.object(worker,'rollback') as rollback:
                worker.guard()
                rollback.assert_called_once_with(state/'jobs'/job_id,'confirmation timed out')

    def test_recovery_restores_old_settings_and_removes_new_secret(self):
        import worker
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder)/'state';state.mkdir();root=Path(folder)/'repo';root.mkdir();directory=state/'jobs'/'e';directory.mkdir(parents=True)
            rt.write_json(root/'local/settings.json',{'old':True});(root/'flake.lock').write_text('old-lock')
            secret=state/'live-secret'
            with patch.object(rt,'STATE',state),patch.object(rt,'ROOT',root),patch.object(rt,'SECRET_PATHS',{'example':secret}):
                worker.save_recovery(directory)
                rt.write_json(root/'local/settings.json',{'new':True});rt.atomic(secret,'NEW-SECRET')
                worker.restore_files(directory)
            self.assertEqual(rt.read_json(root/'local/settings.json'),{'old':True})
            self.assertFalse(secret.exists())

class HttpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.folder=Path(cls.temp.name)
        cls.root=cls.folder/'repo';cls.root.mkdir();cls.state=cls.folder/'state';cls.state.mkdir()
        cfg=settings();cfg['management']['address']='127.0.0.1'
        rt.write_json(cls.root/'local/settings.json',cfg)
        rt.write_json(cls.state/'auth.json',rt.password_record('https-test-password'))
        with patch.object(rt,'STATE',cls.state):rt.issue_tls('127.0.0.1','fixture')
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));cls.port=sock.getsockname()[1]
        cls.origin=f'https://localhost:{cls.port}'
        env=os.environ|{'NIXLAB_ROOT':str(cls.root),'NIXLAB_STATE':str(cls.state),'NIXLAB_SOCKET':str(cls.folder/'broker.sock'),
                        'NIXLAB_PORT':str(cls.port),'PYTHONPATH':str(BASE/'scripts')}
        cls.addClassCleanup(cls.temp.cleanup)
        # This workspace denies filesystem UNIX socket connections. Test the exact
        # broker/HTTPS handlers through a loopback-only transport adapter; production
        # has no TCP broker mode. Unix permissions are checked separately on NixOS.
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));broker_port=sock.getsockname()[1]
        env['PYTHONPATH']=str(BASE/'admin')+os.pathsep+str(BASE/'scripts')
        backend_code="""import main
from http.server import ThreadingHTTPServer
class TestTransport(ThreadingHTTPServer):
 def __init__(self,address,handler):
  super().__init__(('127.0.0.1',BROKER_PORT),handler)
  main.SOCKET.touch()
main.UnixHTTPServer=TestTransport
main.backend()
""".replace('BROKER_PORT',str(broker_port))
        cls.backend=subprocess.Popen([sys.executable,'-c',backend_code],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        frontend_code="import main,http.client; main.UnixConnection=lambda *a,**k:http.client.HTTPConnection('127.0.0.1',"+str(broker_port)+",timeout=30); main.web()"
        cls.web=subprocess.Popen([sys.executable,'-c',frontend_code],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cls.addClassCleanup(lambda: (cls.web.terminate(), cls.web.wait(timeout=10), cls.backend.terminate(), cls.backend.wait(timeout=10)))
        cls.context=ssl.create_default_context(cafile=str(cls.state/'ca.crt'))
        for _ in range(100):
            try:
                _,_,status=cls.request('/',auth=False)
                if status==200:break
                time.sleep(.05)
            except Exception:time.sleep(.05)
        else:raise RuntimeError('HTTPS test server failed to start')
        result,headers,_=cls.request('/api/login',{'password':'https-test-password'},origin=cls.origin,auth=False)
        if 'csrf' not in result: raise RuntimeError(str(result))
        cls.csrf=result['csrf'];cls.cookie=headers['Set-Cookie'].split(';')[0]
    @classmethod
    def tearDownClass(cls):
        pass
    @classmethod
    def request(cls,path,body=None,origin=None,auth=True,csrf=True,host=None):
        headers={}
        if auth and hasattr(cls,'cookie'):headers['Cookie']=cls.cookie
        if csrf and hasattr(cls,'csrf'):headers['X-Nixlab-CSRF']=cls.csrf
        if origin:headers['Origin']=origin
        if host:headers['Host']=host
        data=None
        if body is not None:data=json.dumps(body).encode();headers['Content-Type']='application/json'
        request=urllib.request.Request(cls.origin+path,data=data,headers=headers)
        try:
            response=urllib.request.urlopen(request,context=cls.context,timeout=5)
        except urllib.error.HTTPError as exc:response=exc
        raw=response.read();ctype=response.headers.get('Content-Type','')
        result=json.loads(raw) if 'application/json' in ctype else raw
        return result,response.headers,response.status
    def test_private_status_requires_authentication(self):
        _,_,status=self.request('/api/status',auth=False)
        self.assertEqual(status,401)
    def test_authenticated_read_and_secure_cookie(self):
        _,headers,status=self.request('/api/login',{'password':'https-test-password'},origin=self.origin,auth=False)
        self.assertEqual(status,200)
        for flag in ('HttpOnly','Secure','SameSite=Strict'):self.assertIn(flag,headers['Set-Cookie'])
        data,_,status=self.request('/api/status');self.assertEqual(status,200);self.assertEqual(data['settings']['hostName'],'fixture')
    def test_csrf_and_cross_origin_mutation_refused(self):
        _,_,status=self.request('/api/review',{'settings':{}},origin=self.origin,csrf=False);self.assertEqual(status,401)
        _,_,status=self.request('/api/review',{'settings':{}},origin='https://attacker.invalid');self.assertEqual(status,401)
    def test_dns_rebinding_hostname_refused(self):
        _,_,status=self.request('/api/status',host='attacker.invalid');self.assertEqual(status,401)
    def test_invalid_settings_do_not_create_transaction(self):
        _,_,status=self.request('/api/review',{'settings':{'hostName':'x; execute'}},origin=self.origin)
        self.assertEqual(status,400);self.assertEqual(list((self.state/'jobs').iterdir()),[])
    def test_ca_can_be_verified_before_login(self):
        data,_,status=self.request('/ca.crt',auth=False)
        self.assertEqual(status,200);self.assertIn(b'BEGIN CERTIFICATE',data)

if __name__=='__main__':unittest.main()
