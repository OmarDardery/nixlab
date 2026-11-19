"""Project-level Docker Compose operations for the isolated VM."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from locking import open_lock

ROOT = Path("/srv/compose")

def compose(project, *args, **kwargs):
    command = ["docker", "compose", "--project-directory", str(ROOT / project),
               "--project-name", project, "--file", str(ROOT / project / "compose.yaml")]
    override = ROOT / project / "compose.images.yaml"
    if override.is_file():
        command += ["--file", str(override)]
    return subprocess.run(command + list(args), check=True, **kwargs)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["deploy", "update", "restart", "down", "logs", "backup", "lock"])
    p.add_argument("project")
    args = p.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.project):
        p.error("Invalid Compose project name")
    directory = ROOT / args.project
    if not (directory / "compose.yaml").is_file():
        p.error("Project has no /srv/compose/<name>/compose.yaml")
    if os.geteuid() != 0:
        p.error("Run via sudo or authenticated Incus exec")
    with open_lock('/run/lock/nixlab-project.lock') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        compose(args.project, "config", "--quiet")
        if args.action == "deploy":
            compose(args.project, "up", "--detach", "--remove-orphans", "--wait", "--wait-timeout", "180")
        elif args.action == "update":
            spec = json.loads(compose(args.project, "config", "--format", "json", capture_output=True, text=True).stdout)
            services = [name for name, config in spec["services"].items()
                        if config.get("pull_policy") != "never" and "build" not in config]
            if services:
                compose(args.project, "pull", *services)
            compose(args.project, "up", "--detach", "--remove-orphans", "--wait", "--wait-timeout", "180")
        elif args.action == "logs":
            compose(args.project, "logs", "--tail", "200")
        elif args.action == "down":
            compose(args.project, "down") # never delete volumes
        elif args.action == "restart":
            compose(args.project, "restart")
        elif args.action == "lock":
            # Only write image overrides; resolved Compose environment may contain secrets.
            spec = json.loads(compose(args.project, "config", "--format", "json", capture_output=True, text=True).stdout)
            images = {}
            for service, config in spec["services"].items():
                if config.get("pull_policy") == "never":
                    continue # locally built Nix image, pinned by flake.lock
                info = json.loads(subprocess.run(["docker", "image", "inspect", config["image"]],
                                  capture_output=True, text=True, check=True).stdout)[0]
                if not info.get("RepoDigests"):
                    raise RuntimeError("Image lacks an immutable registry digest")
                images[service] = {"image": info["RepoDigests"][0]}
            (directory / "compose.images.yaml").write_text(json.dumps({"services": images}, indent=2))
        elif args.action == "backup":
            if args.project != "demo":
                p.error("No database-consistency hook declared for this project")
            destination = Path("/var/lib/nixlab/dumps/demo.dump")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=destination.parent)
            try:
                with os.fdopen(fd, "wb") as out:
                    compose("demo", "exec", "-T", "postgres", "pg_dump", "-U", "demo", "-d", "demo", "--format=custom", stdout=out)
                if Path(temporary).stat().st_size == 0:
                    raise RuntimeError("Empty database dump")
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)

if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
