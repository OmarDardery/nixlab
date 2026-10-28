"""Small ownership-aware Incus reconciler. No shell interpolation or deletion."""
import copy
import fcntl
import http.client
import socket
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit
from locking import open_lock

OWNER = "user.nixlab.managed"
INITIALIZING = "user.nixlab.initializing"
LAST_APPLIED = "user.nixlab.last-applied"
MISSING = object()

def run(*args, data=None):
    result = subprocess.run(["incus", *args], input=data, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Incus exited {result.returncode}")
    return result.stdout

def desired_state(spec):
    config = copy.deepcopy(spec.get('config', {}))
    if any(k.startswith('volatile.') or k in (INITIALIZING, LAST_APPLIED) for k in config):
        raise ValueError('Incus volatile and Nixlab state keys are reserved')
    config.pop(OWNER, None)
    return {'version': 1, 'config': config, 'devices': copy.deepcopy(spec.get('devices', {})),
            'profiles': spec.get('profiles', ['base'])}


def encode_state(spec):
    return json.dumps(desired_state(spec), sort_keys=True, separators=(',', ':'))


def merge_fields(current, previous, desired, location):
    result = copy.deepcopy(current)
    for key in previous.keys() | desired.keys():
        before, wanted, actual = previous.get(key, MISSING), desired.get(key, MISSING), current.get(key, MISSING)
        if before is not MISSING and actual != before and actual != wanted:
            raise ValueError('Runtime drift at ' + location + '.' + key + '; resolve before rebuilding')
        if wanted is MISSING:
            result.pop(key, None)
        else:
            result[key] = copy.deepcopy(wanted)
    return result


def plan(current, spec):
    if current.get('config', {}).get(OWNER) != 'true':
        raise ValueError(f"Refusing to modify unowned instance {spec['name']}")
    if current['type'] != spec['type']:
        raise ValueError('Instance type is immutable; migrate explicitly')
    if OWNER in spec.get('config', {}) and spec['config'][OWNER] != 'true':
        raise ValueError('The Nixlab ownership marker cannot be removed by reconciliation')
    desired = desired_state(spec)
    old = json.loads(current.get('config', {}).get(LAST_APPLIED, '{}'))
    if old and old.get('version') != 1:
        raise ValueError('Unsupported last-applied ownership version')
    if spec.get('manageExistingOnly') and old:
        # A shutdown sentinel changes only its explicit fields, not the guest's definition.
        desired = copy.deepcopy(old)
        desired['config'].update(spec.get('config', {}))
        desired['devices'].update(spec.get('devices', {}))
        if 'profiles' in spec: desired['profiles'] = spec['profiles']
    writable = {k: copy.deepcopy(current[k]) for k in
                ('architecture', 'config', 'devices', 'ephemeral', 'profiles', 'description') if k in current}
    result = copy.deepcopy(writable)
    result['config'] = merge_fields(current.get('config', {}), old.get('config', {}), desired['config'], 'config')
    devices = copy.deepcopy(current.get('devices', {}))
    for name in old.get('devices', {}).keys() | desired['devices'].keys():
        previous, wanted = old.get('devices', {}).get(name, {}), desired['devices'].get(name)
        if wanted is None:
            raise ValueError('Device removal requires explicit migration: ' + name)
        actual = devices.get(name, {})
        if previous and not actual:
            raise ValueError('Runtime drift: owned device removed: ' + name)
        for key in ('type', 'pool', 'source', 'path'):
            if key in actual and key in wanted and actual[key] != wanted[key]:
                raise ValueError('Device backing change requires explicit migration: ' + name + '.' + key)
        devices[name] = merge_fields(actual, previous, wanted, 'devices.' + name)
    result['devices'] = devices
    if 'profiles' in old and current.get('profiles', []) != old['profiles'] and current.get('profiles', []) != desired['profiles']:
        raise ValueError('Runtime drift at profiles')
    result['profiles'] = desired['profiles']
    result['config'][LAST_APPLIED] = json.dumps(desired, sort_keys=True, separators=(',', ':'))
    return result if result != writable else None


class LocalConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(120)
        self.sock.connect('/var/lib/incus/unix.socket')


def api(method, path, data=None, etag=None):
    connection = LocalConnection('localhost', timeout=120)
    try:
        headers = {'Content-Type': 'application/json'}
        if etag: headers['If-Match'] = etag
        connection.request(method, path, body=json.dumps(data) if data is not None else None, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        if response.status >= 400 or payload.get('type') == 'error':
            raise RuntimeError('Incus API request failed: HTTP ' + str(response.status))
        return payload, response.getheader('ETag')
    finally:
        connection.close()


def read_instance(project, name):
    payload, etag = api('GET', '/1.0/instances/' + quote(name, safe='') + '?project=' + quote(project, safe=''))
    if not etag:
        raise RuntimeError('Incus did not return an ETag; refusing unsafe update')
    return payload['metadata'], etag


def write_instance(project, name, data, etag):
    payload, _ = api('PUT', '/1.0/instances/' + quote(name, safe='') + '?project=' + quote(project, safe=''), data, etag)
    if payload.get('type') == 'async':
        operation = urlsplit(payload['operation'])
        if operation.netloc or not operation.path.startswith('/1.0/operations/'):
            raise RuntimeError('Invalid Incus operation path')
        result = json.loads(run('query', operation.path + '/wait' + ('?' + operation.query if operation.query else '')))
        if result.get('status_code') != 200:
            raise RuntimeError('Incus configuration operation failed')


def reconcile(spec):
    desired_state(spec)
    if INITIALIZING in spec.get("config", {}):
        raise ValueError("The Nixlab initialization marker is reserved")
    if OWNER in spec.get("config", {}) and spec["config"][OWNER] != "true":
        raise ValueError("The Nixlab ownership marker cannot be removed by reconciliation")
    for k in ("name", "project"):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", spec[k]):
            raise ValueError(f"Invalid {k}")
    project, name = spec["project"], spec["name"]
    # List is checked first: an API/permission error is never mistaken for absence.
    existing = json.loads(run("list", "--project", project, "--format=json"))
    found = next((i for i in existing if i["name"] == name), None)
    if found is None and spec.get("manageExistingOnly"):
        print(f"absent {project}/{name}; no VM created")
        return
    if found is None:
        args = ["init", spec["image"], name, "--project", project,
                "--config", OWNER + "=true", "--config", INITIALIZING + "=true"]
        if spec["type"] == "virtual-machine":
            args.append("--vm")
        elif spec["type"] != "container":
            raise ValueError("Unknown instance type")
        for profile in spec.get("profiles", ["base"]):
            args += ["--profile", profile]
        # Apply disks/NIC reservations before the first boot, including after a failed prior run.
        run(*args)
    current, etag = read_instance(project, name)
    if spec.get("manageExistingOnly") and current.get("config", {}).get(OWNER) != "true":
        print(f"unowned {project}/{name}; left untouched")
        return
    update = plan(current, spec)
    if update is not None:
        write_instance(project, name, update, etag)
        print(f"updated {project}/{name}")
    else:
        print(f"unchanged {project}/{name}")
    state = json.loads(run("list", "--project", project, "--format=json"))
    status = next(i["status"] for i in state if i["name"] == name)
    if spec.get("stop", False) and status == "Running":
        run("stop", name, "--project", project, "--timeout", "120")
    elif spec.get("start", current.get("config", {}).get(INITIALIZING) == "true") and status == "Stopped":
        run("start", name, "--project", project)
    # A failed initialization remains marked and is retried on the next run.
    # Existing stopped guests are not restarted by unrelated reconciliations.
    if current.get("config", {}).get(INITIALIZING) == "true":
        run("config", "unset", name, INITIALIZING, "--project", project)

if __name__ == "__main__":
    # Shared with the export job; a rebuild cannot change instance resources mid-export.
    with open_lock('/run/lock/nixlab-incus.lock') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for spec in json.loads(Path(sys.argv[1]).read_text()):
            reconcile(spec)
