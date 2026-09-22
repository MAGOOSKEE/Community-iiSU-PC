"""
Unit tests for installer/stub_apk.py's _manifest_xml -- the redirector
stub's AndroidManifest.xml generator. Pure string function, no apktool/
build-tools/keystore involved, but importing stub_apk.py does pull in
portable_sdk.py (its import-time adb PATH fix), so this only runs
wherever the project itself does.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))
sys.path.insert(0, str(PROJECT_ROOT / "installer"))

from stub_apk import _manifest_xml  # noqa: E402 -- path set up above


class ManifestXmlTests(unittest.TestCase):
    def test_plain_package_and_label_round_trip(self):
        xml = _manifest_xml("com.iisupc.stub.duckstation", "DuckStation")
        self.assertIn('package="com.iisupc.stub.duckstation"', xml)
        self.assertIn('android:label="DuckStation (Community-iiSU-PC redirector)"', xml)

    def test_ampersand_in_label_is_escaped(self):
        # A future label containing "&" used to produce a manifest apktool
        # couldn't parse -- only quotes were escaped before. Confirms the
        # fix (installer/stub_apk.py) actually escapes it. The apostrophe
        # is deliberately left alone: attributes here are double-quoted,
        # so a literal ' is already well-formed XML and doesn't need
        # &apos;.
        xml = _manifest_xml("com.iisupc.stub.test", "Bill & Ted's Emulator")
        self.assertIn("Bill &amp; Ted's Emulator", xml)
        self.assertNotIn("Bill & Ted's", xml)

    def test_angle_brackets_in_label_are_escaped(self):
        xml = _manifest_xml("com.iisupc.stub.test", "<script>alert(1)</script>")
        self.assertNotIn("<script>", xml)
        self.assertIn("&lt;script&gt;", xml)

    def test_double_quote_in_package_is_escaped(self):
        xml = _manifest_xml('com.iisupc.stub."quoted"', "Label")
        self.assertIn("&quot;quoted&quot;", xml)
        self.assertNotIn('com.iisupc.stub."quoted"', xml)

    def test_output_is_well_formed_xml(self):
        import xml.etree.ElementTree as ET

        xml_text = _manifest_xml("com.iisupc.stub.test", "Test & <Weird> \"Label\"")
        # Raises if the escaping ever regresses into unparseable XML.
        ET.fromstring(xml_text)


if __name__ == "__main__":
    unittest.main()
