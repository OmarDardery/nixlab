"""Compose must not request host privileges, secrets, or unreviewed builds."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "admin"))
import oci_compose as oci

REVISION = "a" * 64
FILES = [
    {"path": "apps/compose.yaml", "size": 25, "sha256": REVISION},
    {"path": "apps/frontend/Dockerfile", "size": 14, "sha256": REVISION},
    {"path": "apps/backend/docker/Containerfile", "size": 16, "sha256": REVISION},
]


def model():
    return {"services": {
        "frontend": {
            "build": {"context": "./frontend"},
            "volumes": ["frontend-data:/usr/share/nginx/html"],
            "ports": ["8080:80"],
        },
        "backend": {
            "build": {"context": "./backend", "dockerfile": "docker/Containerfile"},
            "depends_on": ["frontend"],
        }
    }}


class ComposePolicyTests(unittest.TestCase):
    def validate(self, obj, **kw):
        return oci.validate_model(
            obj, project="my-app", revision=REVISION,
            compose_path=kw.get("path", "apps/compose.yaml"), files=FILES)

    def test_nested_builds_and_named_volume_without_source_rewrite(self):
        original = json.dumps(model(), sort_keys=True)
        result = self.validate(model())
        self.assertEqual(len(result["services"]), 2)
        self.assertEqual([i["service"] for i in result["services"]], ["backend", "frontend"])
        self.assertEqual(result["services"][0]["dockerfile"], "apps/backend/docker/Containerfile")
        self.assertEqual(result["services"][1]["dockerfile"], "apps/frontend/Dockerfile")
        self.assertIn("build: null", result["separateOverride"])
        self.assertIn(REVISION[:32], result["separateOverride"])
        self.assertEqual(json.dumps(model(), sort_keys=True), original, "Never mutate Compose JSON/YAML source")
        self.assertTrue(result["requiresBuilder"])

    def test_changed_build_context_changes_image_identity(self):
        newer = oci.validate_model(model(), project="my-app", revision="b" * 64,
                                   compose_path="apps/compose.yaml", files=FILES)
        before = self.validate(model())
        self.assertNotEqual(newer["intentHash"], before["intentHash"])
        self.assertNotEqual(newer["separateOverride"], before["separateOverride"])

    def test_absent_dockerfile_path_and_escaping_context_fail_closed(self):
        for build in ({"context": "../../etc"}, {"context": "./missing"},
                      {"context": "./frontend", "dockerfile": "../escape"},
                      {"context": "/etc"}, {"context": "."}):
            m = model()
            m["services"]["frontend"]["build"] = build
            with self.subTest(build=build), self.assertRaises(ValueError):
                self.validate(m)

    def test_privilege_and_secret_attempts_fail_closed(self):
        attempts = [
            {"privileged": True},
            {"devices": ["/dev/kvm:/dev/kvm"]},
            {"network_mode": "host"},
            {"pid": "host"},
            {"cap_add": ["SYS_ADMIN"]},
            {"x-incus": {"raw.lxc": "lxc.mount.entry=/etc"}},
            {"volumes": ["/:/host:ro"]},
            {"volumes": ["../secret:/secret:ro"]},
            {"volumes": [{"type": "bind", "source": "/etc", "target": "/host"}]},
            {"secrets": ["password"]},
            {"env_file": [".env"]},
            {"environment": {"DB_PASSWORD": "plaintext"}},
            {"build": {"context": "./frontend", "args": {"TOKEN": "plaintext"}}},
            {"build": {"context": "https://evil.example/build.tar"}},
        ]
        for item in attempts:
            m = model()
            m["services"]["frontend"].update(item)
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.validate(m)
        with self.assertRaises(ValueError):
            self.validate({"secrets": {"key": {}}, "services": model()["services"]})
        with self.assertRaises(ValueError):
            self.validate({"services": {"container": {"image": "nginx:latest"}}})

    def test_immutable_digest_pinned_image_consumers_and_zero_build(self):
        obj = {"services": {"db": {
            "image": "docker.io/library/postgres@sha256:" + "e" * 64,
            "volumes": ["db-data:/var/lib/postgresql/data"],
        }}}
        result = self.validate(obj)
        self.assertFalse(result["requiresBuilder"])
        self.assertEqual(result["separateOverride"], "")
        self.assertEqual(result["services"][0]["namedVolumes"], ["db-data"])

    def test_invalid_services_and_duplicate_ownership_are_not_adopted(self):
        for invalid in ({}, {"services": {}},
                        {"services": {"web": {"image": "nginx:latest"}, "web..": {}}},
                        {"services": {"web": "oops"}}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.validate(invalid)


if __name__ == "__main__":
    unittest.main()
