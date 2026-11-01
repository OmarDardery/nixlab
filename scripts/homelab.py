"""Authenticated Incus CLI wrapper. Argument vectors preserve quoting and exit status."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import syslog
import time


def name(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise argparse.ArgumentTypeError("Use a guest/project name, or its configured numeric alias")
    return value


def execute(argv, action, target):
    # Deliberately do not log command arguments/output: they can contain secrets.
    start = time.monotonic()
    syslog.syslog(syslog.LOG_NOTICE, f"nixlab begin uid={os.getuid()} action={action} target={target}")
    result = subprocess.run(argv, check=False)
    syslog.syslog(syslog.LOG_NOTICE, f"nixlab end action={action} target={target} exit={result.returncode} elapsed={time.monotonic()-start:.2f}s")
    return result.returncode


def main():
    syslog.openlog("nixlab", syslog.LOG_PID, syslog.LOG_AUTHPRIV)
    default = Path("/etc/nixlab/client.json")
    config = Path(os.environ.get("HOMELAB_CONFIG", str(default)))
    cfg = json.loads(config.read_text()) if config.exists() else {}
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--remote", default=cfg.get("remote", "local"), type=name)
    p.add_argument("--project", default=cfg.get("project", "lab"), type=name)
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    sub.add_parser("doctor")
    sub.add_parser("enroll-key").add_argument("file", type=Path)
    portainer = sub.add_parser("portainer")
    portainer.add_argument("operation", choices=["reset-password"])
    for action in ("exec", "exec-many", "restart", "backup", "deploy", "update", "logs", "down", "project-restart", "action"):
        q = sub.add_parser(action)
        q.add_argument("target")
        if action in ("exec", "exec-many", "action"):
            q.add_argument("command", nargs=argparse.REMAINDER)
    args = p.parse_args()
    if args.action in ("doctor", "enroll-key", "portainer"):
        if os.geteuid() != 0:
            p.error('Run this host administration command with sudo')
        command = ["nixlab-admin", args.action]
        if args.action == "enroll-key": command += [str(args.file)]
        if args.action == "portainer": command += [args.operation]
        return execute(command, args.action, "host")
    prefix = ["incus", "--project", args.project]
    if args.action == "list":
        return execute(prefix + ["list", args.remote + ":"], "list", args.project)
    target = args.target
    if args.action in ("deploy", "update", "logs", "down", "project-restart"):
        name(target)
        vm = name(cfg.get("appsInstance", "apps"))
        action = "restart" if args.action == "project-restart" else args.action
        return execute(prefix + ["exec", args.remote + ":" + vm, "--", "nixlab-project", action, target], args.action, target)
    targets = target.split(",") if args.action == "exec-many" else [target]
    failed = 0
    for target in targets:
        target = name(str(cfg.get("aliases", {}).get(target, target)))
        full = args.remote + ":" + target
        if args.action in ("exec", "exec-many", "action"):
            command = args.command
            if command[:1] == ["--"]:
                command = command[1:]
            if args.action == "action":
                actions = {"health": ["systemctl", "is-system-running"],
                           "disk": ["df", "-h"], "services": ["systemctl", "list-units", "--type=service", "--no-pager"]}
                if len(command) != 1 or command[0] not in actions:
                    p.error("predefined actions: health, disk, services")
                command = actions[command[0]]
            if args.action == "exec-many" and not command:
                p.error("exec-many requires a command")
            argv = prefix + ["exec", full, "--"] + (command or ["/bin/sh"])
        elif args.action == "restart":
            argv = prefix + ["restart", full, "--timeout", "120"]
        elif args.action == "backup":
            # Native API export works for trusted remote clients too. This is crash-consistent
            # unless the operator quiesces the guest; scheduled backups use the offline job.
            dest = Path(cfg.get("exportDirectory", "."))
            dest.mkdir(mode=0o700, parents=True, exist_ok=True)
            filename = dest / f"{args.project}-{target}-{time.time_ns()}.tar.gz"
            argv = prefix + ["export", full, str(filename), "--compression", "gzip"]
        else:
            p.error("Unsupported action")
        if len(targets) > 1:
            print(f"== {target} ==", flush=True)
        code = execute(argv, args.action, target)
        failed = failed or code
    return failed

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, argparse.ArgumentTypeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

