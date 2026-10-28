"""Compare evaluated mount resources with the setup variant's legacy inventory."""
import json
import sys
from pathlib import Path

mounts = json.loads(Path(sys.argv[1]).read_text())
assert mounts == {
    "guests": {
        "name": "guests", "uuid": "11111111-1111-1111-1111-111111111111",
        "path": "/srv/nixlab/guests", "fsType": "ext4", "role": "guests",
        "required": True, "readOnly": False,
    },
    "archive": {
        "name": "archive", "uuid": "22222222-2222-2222-2222-222222222222",
        "path": "/srv/nixlab/archive", "fsType": "xfs", "role": "pool",
        "required": True, "readOnly": False,
    },
}, mounts
