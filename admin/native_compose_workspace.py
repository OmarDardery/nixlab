"""Ephemeral, byte-exact Incus Compose source and file-secret staging.

This does NOT run incus-compose, activate a guest or qualify its file-secret
semantics. Upstream currently copies the file into guest rootfs, so this
staging primitive must not be used as proof of secure deployment. It is only
a private frozen working directory for a future reviewed runtime adapter.
"""
from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import tempfile

from native_compose_secret_policy import classify_compose_secrets
from native_imports import PublishedSources, _verified_source, check_files
from native_secrets_delivery import _private_directory
from oci_source_plan import COMPOSE
import runtime as rt

# The *only* permitted static blocker during preparation is the upstream
# guest-rootfs persistence that the later runtime adapter must eliminate.
# All other risks fail before any plaintext is decrypted.
UNQUALIFIED_ROOTFS = "incus-compose-secret-persists-in-guest-rootfs-unqualified"


class FrozenComposeWorkspace:
    def __init__(self, delivery, publisher=None):
        self.delivery = delivery
        self.publisher = publisher or PublishedSources()

    @contextmanager
    def stage(self, project, source_revision, secret_revision, inventory):
        """Prepare original Compose bytes and SOPS-managed relative file secrets.

        Internal use only: outputs are private file paths, never an HTTP/CLI
        response and never permission to deploy through upstream incus-compose.
        """
        if not isinstance(inventory, dict) or inventory.get("name") != project:
            raise ValueError("Compose project inventory mismatch")
        secrets = inventory.get("secrets")
        if (not isinstance(secrets, dict) or secrets.get("file") != "secrets.enc.yaml"
                or not isinstance(secrets.get("bindings"), dict)):
            raise ValueError("Compose source has no typed SOPS bindings")
        attested = self.publisher.attest(project, source_revision)
        if attested["revision"] != source_revision:
            raise ValueError("Compose source changed since review")
        published = self.publisher.root / project / "revisions" / source_revision
        manifest = rt.read_json(published / "manifest.json")
        files = check_files(manifest["files"])
        originals = published / "source"
        compose = [entry["path"] for entry in files
                   if Path(entry["path"]).name in COMPOSE]
        if len(compose) != 1:
            raise ValueError("Exactly one reviewed Compose entrypoint is required")
        # Attest the entire immutable source tree a second time in the copy,
        # without executing uploaded code or following source symlinks.
        original = (originals / compose[0]).read_bytes()
        review = classify_compose_secrets(original, secrets["bindings"])
        if not review["fileRoutes"] or set(review["blocking"]) != {UNQUALIFIED_ROOTFS}:
            raise ValueError("Compose secret delivery has unqualified persistence or bindings")
        # A single Compose-secret file must never be mixed with an original
        # uploaded file of the same path: the source bytes are immutable.
        files_on_disk = {entry["path"] for entry in files}
        for route in review["fileRoutes"]:
            expected = (Path(compose[0]).parent / route["relativeFile"]).as_posix()
            if expected in files_on_disk:
                raise ValueError("Compose secret file collides with published source")

        _private_directory(self.delivery.staging)
        with tempfile.TemporaryDirectory(dir=self.delivery.staging,
                                         prefix=".nixlab-compose-") as temp:
            directory = _private_directory(temp)
            root = directory / "source"
            root.mkdir(mode=0o700)
            _verified_source(originals, files, destination=root)
            with ExitStack() as stack:
                scopes = {}
                for service in sorted({route["service"] for route in review["fileRoutes"]}):
                    scopes[service] = stack.enter_context(self.delivery.open(
                        project, secret_revision, inventory, service))
                secret_paths = {}
                for route in review["fileRoutes"]:
                    source_file = scopes[route["service"]]["paths"][route["alias"]]
                    expected = (Path(compose[0]).parent / route["relativeFile"])
                    destination = root / expected
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    # Same tmpfs as the scoped 0400 file. Exclusive creation:
                    # never replace an uploaded file, symlink or other secret.
                    os.link(source_file, destination, follow_symlinks=False)
                    secret_paths[route["alias"]] = destination
                rt.audit("native-compose-working-directory-staged",
                         project=project, sourceRevision=source_revision,
                         secretRevision=secret_revision,
                         services=len(scopes), secretCount=len(secret_paths))
                yield {
                    "directory": root,
                    "compose": root / compose[0],
                    "paths": secret_paths,
                    "runtimeQualified": False,
                }
        # Exiting the scoped contexts and TemporaryDirectory removes links and
        # decrypted originals, including when exceptions/signals unwind.
