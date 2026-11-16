"""Guest-root Arcane credential lifecycle. Secrets enter stdin and never leave it."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

DATA = Path('/srv/arcane')
RUNTIME = Path('/run/nixlab-arcane')
TTL = 900


def atomic(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(value); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def policy(value):
    if (not isinstance(value, str) or not 16 <= len(value) <= 256
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or not all((any(c.isupper() for c in value), any(c.islower() for c in value),
                        any(c.isdigit() for c in value), any(not c.isalnum() and not c.isspace() for c in value)))):
        raise ValueError('Invalid Arcane password')
    return value


def state():
    db = DATA / 'arcane.db'
    marker = DATA / '.nixlab-bootstrap.json'
    if db.is_symlink(): raise ValueError('Unsafe database')
    if not db.exists():
        if not marker.exists(): return 'blocked' if (DATA/'encryption-key').exists() else 'fresh'
        try:
            phase=json.loads(marker.read_text()).get('state')
            return phase if phase in ('fresh','initializing') else 'blocked'
        except (OSError,ValueError): return 'blocked'
    try:
        with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
                # A crash during the first schema migration may precede users.
                # Only our durable initialization intent permits retry; arbitrary
                # existing or corrupt SQLite files never count as fresh.
                try: pending=json.loads(marker.read_text()).get('state')=='initializing'
                except (OSError,ValueError): pending=False
                return 'initializing' if pending and connection.execute('PRAGMA quick_check').fetchone()[0]=='ok' else 'blocked'
            # Any existing identity prohibits first-boot credential replacement.
            count = connection.execute('SELECT count(*) FROM users').fetchone()[0]
        if count: return 'initialized'
        try: return 'initializing' if json.loads(marker.read_text()).get('state')=='initializing' else 'blocked'
        except (OSError,ValueError): return 'blocked'
    except sqlite3.Error:
        return 'blocked'


def expire():
    path = RUNTIME / 'password'
    if path.exists() and time.time() - path.stat().st_mtime >= TTL:
        path.unlink()


def prepare():
    expire()
    current = state()
    if current == 'blocked': raise ValueError('Restore Arcane database or use recovery')
    if current == 'initialized': (DATA/'initial-admin-password').unlink(missing_ok=True)
    # Migrate the former private file to tmpfs only on a provably fresh DB.
    legacy = Path('/var/lib/nixlab-secrets/arcane-admin-password')
    if current == 'fresh' and not (RUNTIME/'password').exists() and legacy.is_file():
        atomic(RUNTIME/'password', policy(legacy.read_text().rstrip('\r\n')))
    legacy.unlink(missing_ok=True)
    if current != 'initialized':
        path = RUNTIME/'password'
        if not path.is_file(): raise ValueError('Supply a fresh initial credential')
        policy(path.read_text())
        atomic(DATA/'.nixlab-bootstrap.json', json.dumps({'state':'initializing'}))
        content = 'ARCANE_DEFAULT_ADMIN_PASSWORD_FILE=/run/nixlab-arcane/password\n'
    else:
        content = ''
    atomic(RUNTIME/'environment', content)


def api(path, password=None):
    cert = Path('/var/lib/nixlab-secrets/tls.crt')
    tls = cert.exists()
    origin = os.environ.get('ARCANE_ORIGIN', 'https://apps.internal:9443' if tls else 'http://localhost:9443')
    context = ssl.create_default_context(cafile=str(cert)) if tls else None
    # Connect locally while retaining the certificate DNS name and public Origin.
    url = ('https://apps.internal:9443' if tls else 'http://localhost:9443') + path
    body = json.dumps({'username':'admin','password':password}).encode() if password is not None else None
    request = urllib.request.Request(url, data=body, headers={'Content-Type':'application/json','Origin':origin})
    with urllib.request.urlopen(request, context=context, timeout=5) as response:
        result = json.loads(response.read(1024*1024))
        if password is not None:
            return response.status == 200 and isinstance(result.get('data',{}).get('token'),str) and bool(result['data']['token'])
        return response.status == 200


def verify(password):
    deadline = time.monotonic()+90
    while time.monotonic() < deadline:
        try:
            if api('/api/auth/login', password): return
        except (OSError, ValueError, urllib.error.URLError): pass
        time.sleep(1)
    raise ValueError('Arcane authentication unavailable')


def confirm():
    path = RUNTIME/'password'
    if path.exists():
        try:
            verify(path.read_text())
            atomic(DATA/'.nixlab-bootstrap.json', json.dumps({'state':'initialized','verifiedAt':int(time.time())}))
        finally:
            path.unlink(missing_ok=True)
    elif state() != 'initialized':
        raise ValueError('Arcane requires enrollment or recovery')


def command(*args):
    result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    if result.returncode: raise ValueError('Arcane service operation failed')


def terminal_reset(value):
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp('docker',['docker','exec','-it','-e','ARCANE_ALLOW_CLI_PASSWORD_RESET=true',
                           'arcane','/app/arcane','admin','reset-password','--username','admin'])
    output = bytearray()
    reaped = False
    try:
        for prompt in (b'New password:',b'Confirm new password:'):
            deadline = time.monotonic()+30
            while prompt not in output and time.monotonic()<deadline:
                if select.select([fd],[],[],.1)[0]:
                    try: output.extend(os.read(fd,4096))
                    except OSError: break
                if len(output)>65536: raise ValueError('Recovery output exceeded limit')
            if prompt not in output: raise ValueError('Recovery prompt unavailable')
            os.write(fd,value.encode()+b'\n')
        deadline = time.monotonic()+30
        while time.monotonic()<deadline:
            child,status=os.waitpid(pid,os.WNOHANG)
            if child:
                reaped = True
                if os.waitstatus_to_exitcode(status): raise ValueError('Recovery failed')
                return
            time.sleep(.1)
        raise ValueError('Recovery timed out')
    finally:
        if not reaped:
            try: os.kill(pid,signal.SIGKILL)
            except ProcessLookupError: pass
            try: os.waitpid(pid,0)
            except ChildProcessError: pass
        os.close(fd)


def recover_pending(stop=True):
    checkpoint = DATA.parent/'arcane-credential-checkpoint'
    if not (checkpoint/'pending').exists(): return
    if not stop and (RUNTIME/'reset-active').exists(): return
    if stop: command('systemctl','stop','docker-arcane.service')
    # SQLite backup is consistent and contains no plaintext. Never touch volumes,
    # Compose definitions, the encryption key, or other application data.
    for suffix in ('-wal','-shm'): (DATA/('arcane.db'+suffix)).unlink(missing_ok=True)
    with sqlite3.connect(checkpoint/'arcane.db') as source, sqlite3.connect(DATA/'arcane.db') as target:
        source.backup(target)
    (checkpoint/'pending').unlink()


def reset(value):
    value=policy(value)
    if state()!='initialized': raise ValueError('Existing administrator required')
    checkpoint=DATA.parent/'arcane-credential-checkpoint'
    checkpoint.mkdir(mode=0o700,exist_ok=True)
    command('systemctl','stop','docker-arcane.service')
    with sqlite3.connect(DATA/'arcane.db') as source, sqlite3.connect(checkpoint/'arcane.db') as target:
        source.backup(target)
    os.chmod(checkpoint/'arcane.db',0o600)
    atomic(checkpoint/'pending','pending\n')
    atomic(RUNTIME/'reset-active','active\n')
    try:
        command('systemctl','start','docker-arcane.service')
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            try:
                if api('/api/health'): break
            except (OSError,ValueError): pass
            time.sleep(1)
        terminal_reset(value)
        verify(value)
        atomic(DATA/'.nixlab-bootstrap.json',json.dumps({'state':'initialized','verifiedAt':int(time.time())}))
        (checkpoint/'pending').unlink()
    except BaseException:
        recover_pending()
        command('systemctl','start','docker-arcane.service')
        raise
    finally:
        (RUNTIME/'reset-active').unlink(missing_ok=True)


def main():
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o700,parents=True,exist_ok=True)
    action=sys.argv[1]
    # prepare/confirm run as part of systemd startup and cannot take the lock
    # held by enroll/reset while they restart that service.
    if action in ('prepare','confirm','expire','restore'):
        {'prepare':prepare,'confirm':confirm,'expire':expire,'restore':lambda: recover_pending(stop=False)}[action]()
        return
    with (RUNTIME/'lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if action=='status':
            expire()
            print(json.dumps({'state':state(),'bootstrapPresent':(RUNTIME/'password').exists()}));return
        if action=='verified':
            marker=json.loads((DATA/'.nixlab-bootstrap.json').read_text())
            print(json.dumps({'ok':state()=='initialized' and marker.get('verifiedAt',0)>0 and api('/api/health')}));return
        value=policy(sys.stdin.read(1025))
        if action=='enroll':
            if state() not in ('fresh','initializing'): raise ValueError('Existing state cannot be enrolled')
            atomic(RUNTIME/'password',value)
            try:
                command('systemctl','restart','docker-arcane.service')
                # The confirmation unit consumes the secret after actual login.
                command('systemctl','restart','nixlab-arcane-confirm.service')
            finally: (RUNTIME/'password').unlink(missing_ok=True)
        elif action=='reset':
            recover_pending()
            (RUNTIME/'reset-active').unlink(missing_ok=True)
            reset(value)
        else: raise ValueError('Unknown credential operation')
        print(json.dumps({'ok':True}))


if __name__=='__main__':
    try: main()
    except Exception:
        raise SystemExit('Arcane credential operation failed; inspect state or use protected recovery') from None
