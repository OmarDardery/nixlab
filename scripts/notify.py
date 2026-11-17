"""Deliver service failure metadata using a runtime credential; never log its URL."""
import json
import os
from pathlib import Path
import re
import ssl
import sys
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError('Webhook redirects are refused')


def send(unit, credential):
    if not re.fullmatch(r'[a-zA-Z0-9_.@:-]{1,180}', unit): raise ValueError('Invalid service identity')
    url = Path(credential).read_text().strip()
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use an HTTPS webhook URL without userinfo')
    request = Request(url, data=json.dumps({'service': unit, 'status': 'failed'}).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(request, timeout=15) as response:
        if not 200 <= response.status < 300: raise ValueError('Webhook refused the notification')


if __name__ == '__main__':
    try:
        send(sys.argv[1], Path(os.environ['CREDENTIALS_DIRECTORY']) / 'webhook')
    except Exception:
        sys.exit('Notification failed. Check the destination and runtime credential; URL and response are withheld.')
