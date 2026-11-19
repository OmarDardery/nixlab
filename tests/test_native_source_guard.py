"""Original source bytes must be preserved, while unsafe publication fails closed."""
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "admin"))
from native_source_guard import check_source


class SourceGuardTests(unittest.TestCase):
    def assert_result(self, name, data, allowed, message=None):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            manifest = [{"path": name, "size": len(data), "sha256": "0" * 64}]
            if allowed:
                check_source(root, manifest)
            else:
                with self.assertRaises(ValueError) as caught:
                    check_source(root, manifest)
                self.assertIn(message or "publication blocked", str(caught.exception))
                self.assertNotIn("literal-private-value", str(caught.exception))
                self.assertIn("SOPS/age", str(caught.exception))
            self.assertEqual(data, target.read_bytes(), "Original source must remain byte exact")

    def test_valid_dockerfile_and_compose_sources(self):
        self.assert_result("Dockerfile", b"FROM alpine:3.20\nRUN echo hello\n", True)
        self.assert_result("docker-compose.yml", (
            b"services:\n  api:\n    image: alpine\n    env_file: .env\n"
            b"    environment:\n      API_TOKEN: ${API_TOKEN}\n"
        ), True)
        self.assert_result("nested/Dockerfile.worker", b"FROM alpine\n", True)
        self.assert_result("nested/Containerfile.worker", b"FROM alpine\n", True)
        self.assert_result("Containerfile", b"ARG DATABASE_PASSWORD\n", False, "unsafe build ARG/ENV")

    def test_plaintext_dockerfile_credentials_blocked(self):
        self.assert_result("Dockerfile", b"FROM alpine\nENV API_TOKEN=literal-private-value\n",
                           False, "plaintext credential")
        self.assert_result("Dockerfile", b"ARG DATABASE_PASSWORD\n", False,
                           "unsafe build ARG/ENV")

    def test_plaintext_compose_credentials_blocked(self):
        self.assert_result("compose.yaml", (
            b"services:\n  api:\n    image: alpine\n    environment:\n"
            b"      API_TOKEN: literal-private-value\n"), False, "plaintext credential")

    def test_unsupported_build_secrets_and_ssh_are_explicit(self):
        for feature in ("secrets", "ssh"):
            with self.subTest(feature=feature):
                self.assert_result("compose.yml", (
                    "services:\n  api:\n    build:\n      context: .\n"
                    f"      {feature}: [provided]\n").encode(), False,
                    f"unsupported upstream Compose build.{feature}")
        self.assert_result("Dockerfile", b"FROM alpine\nRUN --mount=type=secret,id=token echo ok\n",
                           True)

    def test_env_file_and_key_material_fail_without_echoing(self):
        self.assert_result(".env", b"API_TOKEN=literal-private-value\n", False, ".env upload")
        self.assert_result("nested/database.env", b"API_TOKEN=literal-private-value\n", False, ".env upload")
        self.assert_result(".env.example", b"API_TOKEN=literal-private-value\n",
                           False, "plaintext credential")
        self.assert_result("Dockerfile", b"# AGE-SECRET-KEY-1example\nFROM alpine\n",
                           False, "private key material")

    def test_cannot_inspect_large_or_invalid_text(self):
        self.assert_result("Dockerfile", b"x" * (2 * 1024 * 1024 + 1), False, "inspection limit")
        self.assert_result("compose.yaml", b"\xff\xfe", False, "UTF-8")

    def test_build_args_secret_key_rejected_even_with_placeholder(self):
        self.assert_result("compose.yml", (
            b"services:\n  api:\n    build:\n      context: .\n"
            b"      args:\n        ACCESS_TOKEN: ${ACCESS_TOKEN}\n"
        ), False, "build.args")


if __name__ == "__main__":
    unittest.main()
