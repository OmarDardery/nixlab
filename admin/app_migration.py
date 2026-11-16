"""Read-only Portainer stack inventory before an explicit Arcane migration.

Never applies Compose, starts/stops containers, or exports secret values.
The root-only report is advisory, not proof that a stack is migration-ready.
"""
import hashlib
import http.client
import json
import re
import ssl
from pathlib import Path

import diagnostics
import runtime as rt

MAX_RESPONSE = 2 * 1024 * 1024
MAX_STACKS = 200
REPORT = "portainer-to-arcane-inventory.json"


class InventoryError(ValueError):
    """Fixed error text: never expose API responses or credentials."""


def _request(settings, path, *, body=None, bearer=None):
    """Only call fixed local management API paths with TLS verification."""
    if not (path == "/api/auth" or path == "/api/stacks" or
            re.fullmatch(r"/api/stacks/[1-9][0-9]*/file", path)):
        raise InventoryError("Unsupported inventory request")
    address = diagnostics.management_address(settings)
    context = ssl.create_default_context(cafile=str(rt.STATE / "ca.crt"))
    connection = http.client.HTTPSConnection(
        address, settings["apps"]["portainerPort"], context=context, timeout=10)
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    try:
        connection.request("POST" if body is not None else "GET", path,
                           json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        data = response.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE or response.status != 200:
            raise InventoryError("Portainer inventory API was unavailable")
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise InventoryError("Portainer inventory response was invalid") from None
    except (OSError, http.client.HTTPException, ssl.SSLError):
        raise InventoryError("Portainer inventory API was unavailable") from None
    finally:
        connection.close()


def _token(settings):
    password_path = rt.SECRET_PATHS["portainerPassword"]
    if not password_path.is_file():
        raise InventoryError("Portainer login credential is not available")
    account = rt.read_json(rt.STATE / "portainer-account.json", {})
    username = account.get("username", "admin")
    if not isinstance(username, str) or not username:
        raise InventoryError("Portainer account metadata is invalid")
    password = password_path.read_text().strip()
    if not password:
        raise InventoryError("Portainer login credential is not available")
    result = _request(settings, "/api/auth", body={"Username": username, "Password": password})
    if not isinstance(result, dict) or not isinstance(result.get("jwt"), str) or not result["jwt"]:
        raise InventoryError("Portainer login was not accepted")
    return result["jwt"]


def _stack_summary(stack, file_info):
    """Allowlist fields. Never persist stack JSON, Env values or Compose contents."""
    if not isinstance(stack, dict):
        raise InventoryError("Portainer returned invalid stack metadata")
    identity = stack.get("Id")
    if type(identity) is not int or identity <= 0:
        raise InventoryError("Portainer returned an invalid stack ID")
    name = stack.get("Name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise InventoryError("Portainer returned an invalid stack name")
    if not isinstance(file_info, dict):
        raise InventoryError("Portainer returned an invalid stack file")
    contents = file_info.get("StackFileContent")
    if not isinstance(contents, str):
        raise InventoryError("Portainer did not return a Compose file")
    environment = stack.get("Env") or []
    if not isinstance(environment, list):
        raise InventoryError("Portainer returned invalid environment metadata")
    keys = []
    for item in environment:
        if not isinstance(item, dict):
            raise InventoryError("Portainer returned invalid environment metadata")
        key = item.get("name", item.get("Name"))
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise InventoryError("Portainer returned an invalid environment key")
        keys.append(key)
    return {
        "id": identity,
        "name": name,
        "type": stack.get("Type") if type(stack.get("Type")) is int else None,
        "status": stack.get("Status") if type(stack.get("Status")) is int else None,
        "gitBacked": bool(stack.get("GitConfig")),
        "environmentVariableNames": sorted(set(keys)),
        "composeSha256": hashlib.sha256(contents.encode("utf-8")).hexdigest(),
        "composeBytes": len(contents.encode("utf-8")),
        "manualReviewRequired": True,
    }


def inventory(settings):
    """Read-only snapshot. Return only safe metadata, never credentials or YAML."""
    if not settings["apps"]["enable"]:
        raise InventoryError("The applications VM is disabled")
    token = _token(settings)
    stacks = _request(settings, "/api/stacks", bearer=token)
    if not isinstance(stacks, list) or len(stacks) > MAX_STACKS:
        raise InventoryError("Portainer stack inventory exceeds supported bounds")
    ids = set()
    summaries = []
    for stack in stacks:
        if not isinstance(stack, dict) or type(stack.get("Id")) is not int or stack["Id"] <= 0:
            raise InventoryError("Portainer returned invalid stack metadata")
        identity = stack["Id"]
        if identity in ids:
            raise InventoryError("Portainer returned duplicate stack IDs")
        ids.add(identity)
        contents = _request(settings, f"/api/stacks/{identity}/file", bearer=token)
        summaries.append(_stack_summary(stack, contents))
    return {
        "format": 1,
        "source": "portainer-read-only-api",
        "target": "arcane",
        "complete": True,
        "safeToSwitch": False,
        "note": "Inventory only. Verify Compose files, bind paths, external volumes, networks, environment values and backups before approving a migration.",
        "stackCount": len(summaries),
        "stacks": sorted(summaries, key=lambda item: item["name"]),
    }


def collect(settings):
    result = inventory(settings)
    # Root-owned state is persistent; no report or secret contents are printed.
    destination = rt.STATE / REPORT
    rt.write_json(destination, result)
    return {"report": str(destination), "stackCount": result["stackCount"],
            "complete": result["complete"], "safeToSwitch": False}
