"""Unit tests for shared/iisu_install_record.py's patch version: which iiSUs
must be repatched after a project update, and the guard that makes changing
the patch without bumping PATCH_VERSION fail here instead of leaving every
patched iiSU on the old patch with no prompt. Temporary folders only."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared import iisu_install_record as record  # noqa: E402; path set up above

# The smali patch each PATCH_VERSION shipped. When this test fails because
# installer/smali_patch/ changed: if an iiSU patched before the change needs
# repatching, bump PATCH_VERSION, describe it in PATCH_CHANGES and add its hash
# here; if not (a comment, say), replace the current version's hash.
PATCH_HASHES = {
    2: "d54594b86ec6e4cae15330e2f4dafd0922b54c3f22fed14714710ea275c8ebc1",
}


def smali_patch_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted((PROJECT_ROOT / "installer" / "smali_patch").glob("*.smali")):
        digest.update(path.name.encode("utf-8"))
        # Line endings depend on how git checked the files out.
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


class PatchVersionTests(unittest.TestCase):
    def test_the_patch_changed_only_with_its_version(self):
        self.assertEqual(
            smali_patch_hash(), PATCH_HASHES.get(record.PATCH_VERSION),
            "installer/smali_patch/ changed: see PATCH_HASHES in this file for what to update",
        )

    def test_every_version_says_what_it_brings(self):
        for version in range(record.UNVERSIONED_PATCH + 1, record.PATCH_VERSION + 1):
            with self.subTest(version=version):
                self.assertTrue(record.PATCH_CHANGES.get(version))

    def test_a_new_install_is_recorded_with_the_current_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.json"
            record.save("iiSU-Alpha-7.5.apk", "0.1.6.1", path)
            saved = record.load(path)
        self.assertEqual(saved["patch_version"], record.PATCH_VERSION)
        self.assertFalse(record.needs_repatch(saved))
        self.assertEqual(record.changes_since(saved), [])

    def test_an_older_or_unknown_patch_must_be_repatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.json"
            # A record written before patch versions were kept.
            path.write_text(json.dumps({"apk_name": "iiSU-Alpha-7.4.apk", "version_name": "0.1.6.1"}), encoding="utf-8")
            old = record.load(path)
        for case in (old, None, {"apk_name": "x.apk", "patch_version": "2"}):
            with self.subTest(case=case):
                self.assertTrue(record.needs_repatch(case))
                self.assertEqual(record.changes_since(case)[-1], record.PATCH_CHANGES[record.PATCH_VERSION])


if __name__ == "__main__":
    unittest.main()
