"""Disposable pre-credential VM boot diagnostics; never installed on a live host."""
import base64
import json
from pathlib import Path
import socket

try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect('/tmp/nixlab-apps-debug.qmp')
        stream = connection.makefile('rwb')
        json.loads(stream.readline())
        request_id = 0

        def command(name, arguments=None):
            global request_id
            request_id += 1
            request = {'execute': name, 'id': request_id}
            if arguments is not None: request['arguments'] = arguments
            stream.write(json.dumps(request).encode() + b'\n'); stream.flush()
            while True:
                reply = json.loads(stream.readline())
                if reply.get('id') != request_id: continue
                if 'error' in reply: raise RuntimeError(reply['error'].get('class', 'QMP error'))
                return reply['return']

        command('qmp_capabilities')
        print('VM status:', json.dumps(command('query-status')))
        for query in ('info cpus', 'info registers', 'info block'):
            print(query + ':', command('human-monitor-command', {'command-line': query}))
        destination = '/tmp/nixlab-apps-boot.png'
        command('screendump', {'filename': destination, 'format': 'png'})
        screenshot = Path(destination)
        if screenshot.stat().st_size <= 2 * 1024 * 1024:
            print('NIXLAB_VM_SCREENSHOT:' + base64.b64encode(screenshot.read_bytes()).decode())
except (OSError, ValueError, RuntimeError) as exc:
    print('VM diagnostic unavailable:', type(exc).__name__)
