"""Regression tests for the Incus-native migration gate."""
from pathlib import Path
import tempfile
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import incus_native_audit as audit


class InventoryAuditTests(unittest.TestCase):
    def test_exact_297_source_decisions(self):
        root = Path(__file__).resolve().parents[1]
        rows = audit.decisions(root / "docs/incus-native-file-inventory.md")
        self.assertEqual(len(rows), 297)
        self.assertEqual(sum(x == "COPY" for x in rows.values()), 20)
        self.assertEqual(sum(x == "TRANSLATE" for x in rows.values()), 256)
        self.assertEqual(sum(x == "REMOVE" for x in rows.values()), 21)

    def test_strict_requires_translation_and_removal(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "copy").write_text("safe")
            (root / "translate").write_text("new")
            (root / "retire").write_text("obsolete")
            inventory = {"copy": "COPY", "translate": "TRANSLATE", "retire": "REMOVE"}
            incomplete = audit.status(root, inventory, [])
            self.assertFalse(incomplete["ready"])
            self.assertEqual(incomplete["pendingTranslations"], ["translate"])
            self.assertEqual(incomplete["legacyPathsRemaining"], ["retire"])
            (root / "retire").unlink()
            self.assertTrue(audit.status(root, inventory, ["translate"])["ready"])

    def test_unknown_completion_and_duplicate_path_fail(self):
        with tempfile.TemporaryDirectory() as name:
            file = Path(name) / "inventory.md"
            q = chr(96)
            file.write_text("| " + q + "same" + q + " | TRANSLATE | reason |\n"
                            + "| " + q + "same" + q + " | COPY | reason |\n")
            with self.assertRaisesRegex(ValueError, "repeated"):
                audit.decisions(file)
            with self.assertRaisesRegex(ValueError, "known TRANSLATE"):
                audit.status(Path(name), {"copied": "COPY"}, ["copied"])


if __name__ == "__main__":
    unittest.main()
