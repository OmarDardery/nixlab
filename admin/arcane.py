"""Arcane private authentication and upstream interactive account recovery."""
import http.client
import json
import subprocess
import sys
import time
from pathlib import Path
import diagnostics
import runtime as rt


def incus(*args, timeout=120):
    result = subprocess.run(['incus', '--force-local', *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError('Arcane command failed')
    return result.stdout + result.stderr


def guest(*args, timeout=120):
    return incus('exec', 'apps', '--project', 'lab', '--', *args, timeout=timeout)


def authentication_ready(settings):
    if credential_status(settings)['state'] != 'initialized': return False
    try:
        return json.loads(guest('nixlab-arcane-credentials','verified',timeout=15)).get('ok') is True
    except (OSError,ValueError,subprocess.SubprocessError): return False


def reset(settings):
    if not settings['apps']['enable'] or not sys.stdin.isatty():
        raise ValueError('Use a private host terminal with the applications VM enabled')
    try:
        result = subprocess.run(['incus', '--force-local', 'exec', 'apps', '--project', 'lab', '--mode', 'interactive', '--',
            'docker', 'exec', '-it', '-e', 'ARCANE_ALLOW_CLI_PASSWORD_RESET=true', 'arcane',
            '/app/arcane', 'admin', 'reset-password', '--username', 'admin'], check=False)
    except OSError:
        raise ValueError('Arcane account recovery failed') from None
    if result.returncode: raise ValueError('Arcane account recovery failed')
    return {'url': 'https://' + diagnostics.management_address(settings) + ':' + str(settings['apps']['arcanePort'])}


def credential_status(settings):
    if not settings['apps']['enable']: return {'state':'disabled'}
    try:
        result=json.loads(guest('nixlab-arcane-credentials','status',timeout=15))
        if result.get('state') in ('fresh','initializing','initialized','blocked'): return result
    except (ValueError,OSError,subprocess.SubprocessError): pass
    return {'state':'unavailable'}


def supplied_password(settings, value, action):
    # Validate again inside root authority; never trust gateway validation alone.
    if not isinstance(value,str) or not value:
        raise ValueError('Supply a valid Arcane password')
    rt.encode_secrets({'arcanePassword':value},settings)
    if action not in ('enroll','reset') or not settings['apps']['enable']:
        raise ValueError('Arcane applications VM is disabled')
    result=subprocess.run(['incus','--force-local','exec','apps','--project','lab','--mode','non-interactive','--',
                           'nixlab-arcane-credentials',action],input=value,text=True,capture_output=True,timeout=300)
    if result.returncode: raise ValueError('Arcane credential operation failed; inspect service state or use local recovery')
    try: success=json.loads(result.stdout).get('ok') is True
    except (ValueError,AttributeError): success=False
    if not success: raise ValueError('Arcane credential verification failed')
    rt.audit('arcane-credential-'+action)
    return {'ok':True}


def review_secrets(body, actor):
    import accounts
    values=dict(body.get('secrets',{}))
    reuse=body.pop('reuseArcanePassword',False)
    current=body.pop('arcaneCurrent',None)
    if not isinstance(reuse,bool): raise ValueError('Explicit password reuse choice required')
    if reuse:
        if not accounts.matches(actor,current): raise ValueError('Current portal password is incorrect')
        if values.get('arcanePassword'): raise ValueError('Choose separate password or explicit reuse')
        values['arcanePassword']=current
    return values


def onboard(settings):
    path=rt.SECRET_PATHS['arcanePassword']
    try:
        current=credential_status(settings)['state']
        if current=='initialized':
            # Existing databases are authoritative, including upgrade from the
            # former retained-file flow. Never apply replacement bootstrap input.
            guest('systemctl','restart','docker-arcane.service')
            return {'ok':True,'existing':True}
        if current not in ('fresh','initializing') or not path.is_file():
            raise ValueError('Arcane requires explicit enrollment or local recovery')
        if time.time()-path.stat().st_mtime >= 900:
            raise ValueError('Arcane initial credential expired; resubmit it')
        return supplied_password(settings,path.read_text().rstrip('\r\n'),'enroll')
    finally:
        retire(path)
        retire(Path('/var/lib/nixlab-secrets/apps-arcane-password'))


def retire(path):
    if path.exists() or path.is_symlink(): rt.durable_unlink(path)


def expire_host():
    retire(Path('/var/lib/nixlab-secrets/apps-arcane-password'))
    path=rt.SECRET_PATHS.get('arcanePassword')
    if path is not None and path.exists() and time.time()-path.stat().st_mtime>=900: rt.durable_unlink(path)


if __name__=='__main__':
    try:
        if sys.argv[1]=='expire': expire_host()
        else:
            settings=json.loads(open(sys.argv[2]).read())
            onboard(settings)
    except Exception:
        raise SystemExit('Arcane enrollment failed; resubmit an initial credential or use recovery') from None
