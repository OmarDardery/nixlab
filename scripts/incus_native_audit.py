#!/usr/bin/env python3
"""Track the per-file Incus-native rebuild; strict mode is a release gate."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
QUOTE = chr(96)
ROW = re.compile(r"^\| " + QUOTE + r"([^" + QUOTE + r"]+)" + QUOTE + r" \| (COPY|TRANSLATE|REMOVE) \|")


def decisions(file):
    result = {}
    for line in file.read_text(encoding="utf-8").splitlines():
        found = ROW.match(line)
        if not found:
            continue
        name, action = found.groups()
        if name in result or name.startswith("/") or "\\" in name or ".." in Path(name).parts:
            raise ValueError("Invalid or repeated path in migration inventory")
        result[name] = action
    if not result:
        raise ValueError("Migration inventory has no source entries")
    return result


def status(root, inventory, completed):
    if not isinstance(completed, list) or len(set(completed)) != len(completed):
        raise ValueError("Completed translation names must be distinct")
    if any(not isinstance(path, str) or inventory.get(path) != "TRANSLATE" for path in completed):
        raise ValueError("Completed paths must be known TRANSLATE candidates")
    done = set(completed)
    pending = [path for path, action in inventory.items()
               if action == "TRANSLATE" and (path not in done or not (root / path).is_file())]
    legacy = [path for path, action in inventory.items()
              if action == "REMOVE" and (root / path).exists()]
    missing = [path for path, action in inventory.items()
               if action == "COPY" and not (root / path).is_file()]
    return {
        "total": len(inventory),
        "copy": sum(v == "COPY" for v in inventory.values()),
        "translate": sum(v == "TRANSLATE" for v in inventory.values()),
        "remove": sum(v == "REMOVE" for v in inventory.values()),
        "completedTranslations": len(done),
        "pendingTranslations": pending,
        "legacyPathsRemaining": legacy,
        "missingCopies": missing,
        "ready": not (pending or legacy or missing),
        "warning": "Source accounting only; it does not qualify runtime, backups or security"
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    inventory = decisions(ROOT / "docs/incus-native-file-inventory.md")
    progress = json.loads((ROOT / "docs/incus-native-progress.json").read_text())
    result = status(ROOT, inventory, progress["completedTranslations"])
    summary = {k: len(v) if isinstance(v, list) else v for k, v in result.items()}
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.strict and not result["ready"]:
        print("Incus-native source migration is not release-ready", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
