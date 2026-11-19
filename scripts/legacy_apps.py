"""Read-only Arcane/Docker apps VM inventory for an explicit Incus migration.

Never starts or stops a guest, reads credentials, exports files, or changes
Docker/Incus configuration. This is a migration preflight, NOT backup proof.
"""
import json
import subprocess
from urllib.parse import quote

MAX_ITEMS = 256
MAX_OUTPUT = 1024 * 1024


def run(*arguments, timeout=30):
    result = subprocess.run(
        ["incus", "--force-local", *arguments],
        capture_output=True, text=True, timeout=timeout,
        check=False,
    )
    if result.returncode:
        # Never echo raw stderr from an upstream container: it can expose
        # sensitive configuration or user data.
        raise RuntimeError("Incus inventory query failed; inspect Incus locally")
    if len(result.stdout) > MAX_OUTPUT:
        raise ValueError("Inventory exceeds maximum response size")
    return result.stdout


def parsed_json(text, expected):
    result = json.loads(text)
    if not isinstance(result, expected):
        raise ValueError("Unexpected inventory response type")
    return result


def filtered_docker(raw, fields):
    """Parse bounded docker --format '{{json .}}' output, never emit unknown fields."""
    rows = []
    if raw.strip():
        for line in raw.splitlines():
            if len(rows) >= MAX_ITEMS:
                raise ValueError("Legacy app inventory exceeds limit")
            item = parsed_json(line, dict)
            rows.append({dest: str(item.get(source, ""))[:255] for dest, source in fields.items()})
    return rows


def inventory():
    instances = parsed_json(
        run("list", "--project", "lab", "--columns=ns", "--format=json"),
        list,
    )
    if len(instances) > MAX_ITEMS:
        raise ValueError("Incus lab project inventory exceeds limit")
    matches = [i for i in instances if isinstance(i, dict) and i.get("name") == "apps"]
    if not matches:
        return {
            "schemaVersion": 1, "present": False,
            "migrationStatus": "no-legacy-apps-vm",
            "safeToDelete": False,
            "note": "No lab/apps VM found; this is not proof that other apps or volumes are absent.",
        }
    if len(matches) != 1:
        raise ValueError("Ambiguous legacy apps VM inventory")

    metadata = parsed_json(run(
        "query", "/1.0/instances/" + quote("apps", safe="") + "?project=lab",
    ), dict)
    if metadata.get("type") != "virtual-machine":
        raise ValueError("lab/apps is not the expected legacy virtual machine; refuse migration assumptions")
    active = matches[0].get("status") == "Running"
    devices = metadata.get("expanded_devices")
    if not isinstance(devices, dict):
        devices = metadata.get("devices") if isinstance(metadata.get("devices"), dict) else {}

    disks = []
    for name, device in sorted(devices.items()):
        if not isinstance(device, dict):
            continue
        if device.get("type") == "disk":
            disks.append({
                "name": name,
                "pool": str(device.get("pool", ""))[:255],
                "source": str(device.get("source", ""))[:255],
                "path": str(device.get("path", ""))[:255],
                "size": str(device.get("size", ""))[:255],
            })

    report = {
        "schemaVersion": 1,
        "present": True,
        "instance": {
            "project": "lab", "name": "apps",
            "type": "virtual-machine",
            "status": str(matches[0].get("status", "unknown"))[:64],
            "nixlabManaged": metadata.get("config", {}).get("user.nixlab.managed") == "true",
            "disks": disks,
        },
        "docker": {
            "status": "not-inspected",
            "containers": None,
            "volumes": None,
            "stacks": None,
        },
        "migrationStatus": "backup-and-restore-not-verified",
        "safeToDelete": False,
        "requirements": [
            "Copy reviewed Compose/Nix source and encrypted project secret files independently.",
            "Export the powered-off VM and any independent Incus custom volumes off-host.",
            "Make application-consistent database and Docker named-volume backups.",
            "Restore application data and credentials in an isolated Incus target.",
            "Verify restore, then explicitly cut over; keep the old VM until the operator confirms recovery.",
        ],
    }

    if not active:
        report["docker"]["status"] = "vm-not-running"
        return report

    # The guest is already running. These commands are read-only and bounded.
    # A failed docker command does not prove there are no workloads.
    def docker(*args):
        return run(
            "exec", "apps", "--project", "lab", "--",
            "docker", *args,
            timeout=30,
        )

    try:
        report["docker"]["containers"] = filtered_docker(
            docker("ps", "--all", "--format", "{{json .}}"),
            {"name": "Names", "image": "Image", "state": "State"},
        )
        report["docker"]["volumes"] = filtered_docker(
            docker("volume", "ls", "--format", "{{json .}}"),
            {"name": "Name", "driver": "Driver"},
        )
        stacks = parsed_json(docker("compose", "ls", "--format", "json"), list)
        if len(stacks) > MAX_ITEMS:
            raise ValueError("Legacy Compose stack inventory exceeds limit")
        report["docker"]["stacks"] = [{
            "name": str(stack.get("Name", ""))[:255],
            "status": str(stack.get("Status", ""))[:255],
        } for stack in stacks if isinstance(stack, dict)]
        report["docker"]["status"] = "inspected"
    except (RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        report["docker"]["status"] = "unavailable-or-partial"
        # Do not replace unknown or failed data with empty lists.
    return report
