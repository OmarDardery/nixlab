"""Read-only root-broker projection of declarative project source.

Never evaluates user Nix or returns file contents, credential values, or
a path supplied by an HTTP client. This is not a deployment endpoint.
"""
import os
from pathlib import Path
import stat

from project_workspace import validate_workspace, WorkspaceError

ROOT = Path(os.environ.get("NIXLAB_PROJECTS", "/srv/nixlab/projects"))


def overview(root=None):
    root = Path(root) if root is not None else ROOT
    if not root.exists() and not root.is_symlink():
        return {"available": False, "reason": "not-configured", "projects": []}
    # Root-broker-owned workspace and parent; reject link replacement before
    # reading untrusted manifests. Source publishing remains separately guarded.
    try:
        for path in (root.parent, root):
            meta = path.lstat()
            if (not stat.S_ISDIR(meta.st_mode) or stat.S_ISLNK(meta.st_mode)
                    or meta.st_uid != os.geteuid() or meta.st_mode & 0o022):
                raise WorkspaceError("Untrusted project workspace ownership")
        manifest = validate_workspace(root)
        projects = [
            {"name": name, "entrypoint": detail["default"],
             "moduleCount": len(detail["modules"])}
            for name, detail in sorted(manifest["projects"].items())
        ]
        return {
            "available": True, "sourceHash": manifest["sourceHash"],
            "projects": projects, "fileCount": len(manifest["files"]),
            "bytes": manifest["bytes"]
        }
    except (OSError, ValueError, WorkspaceError):
        # Hide root paths and source bytes from browser/API diagnostics.
        return {"available": False, "reason": "invalid-workspace", "projects": []}
