"""Static Compose secret-policy coverage; never approves Incus runtime delivery."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "admin"))
from native_compose_secret_policy import classify_compose_secrets as classify


def bindings():
    return {
        "API_TOKEN": {"key": "API_TOKEN", "instance": "api", "backend": "incus-file"},
        "DB_PASS": {"key": "DB_PASSWORD", "instance": "db", "backend": "incus-file"},
    }


def compose(body):
    return body.encode("utf-8")


class ComposeSecretPolicyTests(unittest.TestCase):
    def test_no_secret_delivered_without_runtime(self):
        result = classify(compose("services:\n  api:\n    image: alpine:3.20\n"))
        self.assertEqual(result["qualification"], "no-secret-delivery-claimed")
        self.assertEqual(result["fileRoutes"], [])

    def test_byte_exact_file_mapping_is_not_ephemeral(self):
        raw = compose(
            "services:\n"
            "  api:\n    image: alpine\n    secrets: [API_TOKEN]\n"
            "  db:\n    image: postgres\n    secrets: [DB_PASS]\n"
            "secrets:\n"
            "  API_TOKEN:\n    file: ./secrets/api_token\n"
            "  DB_PASS:\n    file: ./secrets/db_pass\n")
        result = classify(raw, bindings())
        self.assertEqual(result["fileRoutes"], [
            {"service": "api", "alias": "API_TOKEN", "relativeFile": "secrets/api_token"},
            {"service": "db", "alias": "DB_PASS", "relativeFile": "secrets/db_pass"},
        ])
        self.assertEqual(result["qualification"], "blocked")
        self.assertIn("incus-compose-secret-persists-in-guest-rootfs-unqualified",
                      result["blocking"])
        self.assertNotIn("password", str(result).lower().replace("db_password", ""))
        self.assertEqual(raw[-1:], b"\n")

    def test_environment_reference_and_env_file_are_not_safe(self):
        raw = compose(
            "services:\n"
            "  api:\n"
            "    image: alpine\n"
            "    env_file: .env\n"
            "    environment:\n      API_TOKEN: \u0024{API_TOKEN}\n"
            "    secrets: [API_TOKEN]\n"
            "secrets:\n"
            "  API_TOKEN:\n    file: ./runtime/token\n")
        verdict = classify(raw, {"API_TOKEN": bindings()["API_TOKEN"]})
        self.assertIn("compose-env-file-may-persist-in-incus-config", verdict["blocking"])
        self.assertIn("compose-secret-environment-may-persist-in-incus-config", verdict["blocking"])
        self.assertIn("secret-interpolation-may-persist-in-incus-config", verdict["blocking"])
        self.assertNotIn("literal-private-value", str(verdict))

    def test_unbound_or_cross_service_reference_denied(self):
        raw = compose(
            "services:\n  api:\n    image: alpine\n    secrets: [DB_PASS]\n"
            "secrets:\n  DB_PASS:\n    file: ./db\n")
        verdict = classify(raw, bindings())
        self.assertIn("compose-secret-grant-not-bound-to-service", verdict["blocking"])
        self.assertEqual(verdict["fileRoutes"], [])

    def test_external_environment_and_inline_secret_not_accepted(self):
        for source in (
            "environment: DB_PASS",
            "external: true",
            "file: /etc/passwd",
            "file: ../../secret.txt",
            "file: .env",
            "file: \u0024{DB_PASS}",
        ):
            with self.subTest(source=source):
                raw = compose(
                    "services:\n  db:\n    image: alpine\n    secrets: [DB_PASS]\n"
                    "secrets:\n  DB_PASS:\n    " + source + "\n")
                verdict = classify(raw, {"DB_PASS": bindings()["DB_PASS"]})
                self.assertEqual(verdict["qualification"], "blocked")
                self.assertFalse(verdict["fileRoutes"])

    def test_non_string_environment_and_invalid_yaml_rejected(self):
        bad = (
            b"services: [api]\n",
            b"services:\n  api:\n    environment: [22]\n",
            b"services:\n  api:\n    environment: bad\n",
            b"services:\n  api:\n    secrets: bad\n",
            b"services: [\n",
            b"\xff\xfe",
        )
        for raw in bad:
            with self.subTest(raw=raw):
                with self.assertRaises((ValueError, TypeError, AttributeError)):
                    classify(raw)

    def test_unknown_binding_format_rejected(self):
        raw = compose("services:\n  api:\n    image: alpine\n")
        for bad in ({"X": {"key": "X", "instance": "api", "backend": "nixos-sops"}},
                    {"..": {"key": "TOKEN", "instance": "api", "backend": "incus-file"}}):
            with self.assertRaises(ValueError):
                classify(raw, bad)


if __name__ == "__main__":
    unittest.main()
