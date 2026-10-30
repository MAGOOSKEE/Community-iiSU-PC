"""
The iiSU update check. iiSU's version name (0.1.6.1 for Alpha 7.4) and its
release tags (0.0.7.4) use different numbering, so the check compares the APK
file this install recorded with the latest release's APK. The device and
GitHub are faked; the record lives in a temporary folder.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))
sys.path.insert(0, str(PROJECT_ROOT / "installer"))

from services import iisu_update_service as svc  # noqa: E402; path set up above
from shared import iisu_install_record  # noqa: E402

LATEST = svc.IisuReleaseInfo(
    version="0.0.7.4", asset_name="iiSU-Alpha-7.4.apk",
    download_url="https://github.com/iisu-network/iiSU/releases/download/0.0.7.4/iiSU-Alpha-7.4.apk",
    release_url="https://github.com/iisu-network/iiSU/releases/tag/0.0.7.4",
)


class RecordTests(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.json"
            iisu_install_record.save("iiSU-Alpha-7.4.apk", "0.1.6.1", path)
            self.assertEqual(
                iisu_install_record.load(path),
                {"apk_name": "iiSU-Alpha-7.4.apk", "version_name": "0.1.6.1",
                 "patch_version": iisu_install_record.PATCH_VERSION},
            )

    def test_missing_or_broken_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.json"
            self.assertIsNone(iisu_install_record.load(path))
            path.write_text("{nope", encoding="utf-8")
            self.assertIsNone(iisu_install_record.load(path))
            path.write_text('{"apk_name": ""}', encoding="utf-8")
            self.assertIsNone(iisu_install_record.load(path))


class CheckTests(unittest.TestCase):
    def _check(self, installed="0.1.6.1", record=None, latest=LATEST):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.json"
            if record is not None:
                iisu_install_record.save(*record, path)
            with patch.object(iisu_install_record, "RECORD_PATH", path), \
                 patch.object(iisu_install_record.load, "__defaults__", (path,)), \
                 patch.object(svc, "get_installed_iisu_version", return_value=installed), \
                 patch.object(svc, "_fetch_latest_official_release", return_value=latest):
                return svc.check_for_iisu_update()

    def test_the_latest_release_installed_is_up_to_date(self):
        result = self._check(record=("iiSU-Alpha-7.4.apk", "0.1.6.1"))
        self.assertFalse(result.update_available)
        self.assertIn("Up to date", result.message)

    def test_an_older_release_installed_offers_the_update(self):
        result = self._check(installed="0.1.5.0", record=("iiSU-Alpha-7.3.apk", "0.1.5.0"))
        self.assertTrue(result.update_available)
        self.assertIn("This install has iiSU-Alpha-7.3.apk", result.message)

    def test_a_record_without_a_version_name_still_counts(self):
        self.assertFalse(self._check(record=("iiSU-Alpha-7.4.apk", None)).update_available)

    def test_iisu_replaced_some_other_way_makes_the_record_stale(self):
        result = self._check(installed="0.2.0.0", record=("iiSU-Alpha-7.4.apk", "0.1.6.1"))
        self.assertTrue(result.update_available)
        self.assertIn("can't tell", result.message)

    def test_no_record_says_it_cannot_tell_instead_of_claiming_an_update(self):
        result = self._check(record=None)
        self.assertIn("can't tell", result.message)
        self.assertNotIn("update available", result.message.lower())
        self.assertTrue(result.update_available, "the update stays available, and installing it records it")

    def test_matching_numbers_without_a_record_are_up_to_date(self):
        self.assertFalse(self._check(installed="0.0.7.4", record=None).update_available)

    def test_a_stopped_vm_or_offline_github_never_offers_an_update(self):
        self.assertFalse(self._check(installed=None, record=("iiSU-Alpha-7.4.apk", "0.1.6.1")).update_available)
        self.assertFalse(self._check(latest=None).update_available)


class ApplyRecordsTheSourceNameTests(unittest.TestCase):
    def test_a_release_download_is_recorded_under_its_own_file_name(self):
        import setup_wizard

        with patch.object(svc, "_download"), patch.object(setup_wizard, "update_iisu") as update:
            message = svc.apply_iisu_update(LATEST.download_url)
        self.assertEqual(update.call_args.kwargs["source_name"], "iiSU-Alpha-7.4.apk")
        self.assertIn("iiSU-Alpha-7.4.apk", message)

    def test_a_chosen_file_is_recorded_under_its_name(self):
        import setup_wizard

        with patch.object(setup_wizard, "update_iisu") as update:
            svc.apply_iisu_update(Path("C:/Downloads/iiSU-Alpha-7.5-prerelease1.apk"))
        self.assertEqual(update.call_args.kwargs["source_name"], "iiSU-Alpha-7.5-prerelease1.apk")


if __name__ == "__main__":
    unittest.main()
