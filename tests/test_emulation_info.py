"""The hardware acceleration report in the start and Setup logs
(shared/emulation_info.py): reading what Windows, the emulator's own check
and emulator.log say."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import start_iisu_pc  # noqa: E402
from shared import emulation_info as info  # noqa: E402

RESUME_LOG = """Community-iiSU-PC v0.5.8-alpha | emulator | Python 3.11.9
INFO         | Android emulator version 37.2.12.0 (build_id 16428233) (CL:N/A)
INFO         | Graphics backend: gfxstream
\x1b[0;39m\x1b[0;39mINFO         | Increasing RAM size to 2560MB
\x1b[0;39mINFO         | emuglConfig_init: vulkan_mode_selected:host gles_mode_selected:host
\x1b[0;39mINFO         | Selecting Vulkan device: Example GPU 3000, Version: 1.4.357
\x1b[0;39mINFO         | Graphics Adapter Android Emulator OpenGL ES Translator (Example GPU 3000/PCIe/SSE2)
\x1b[0;39mWHPX on Windows 10.0.26200 detected.
Windows Hypervisor Platform accelerator is operational
\x1b[0;39mINFO         | Loading snapshot 'default_boot'...
\x1b[0;39mINFO         | Successfully loaded snapshot 'default_boot' using 2955 ms
"""


class WindowsQueryTests(unittest.TestCase):
    def test_reads_the_processor_and_both_virtualization_answers(self):
        parsed = info.parse_windows_query("Example CPU 8-Core Processor           |8|True|True\r\n")
        self.assertEqual(parsed, {
            "processor": "Example CPU 8-Core Processor", "cores": "8",
            "firmware_virtualization": True, "hypervisor_present": True,
        })

    def test_anything_else_is_no_answer(self):
        self.assertEqual(info.parse_windows_query("Get-CimInstance : Access denied\n"), {})

    def test_each_state_is_described(self):
        describe = info.describe_windows_virtualization
        # With a hypervisor running Windows reports the firmware flag as off.
        self.assertIn("hardware virtualization is on", describe(False, True))
        self.assertIn("no hypervisor is active", describe(True, False))
        self.assertIn("BIOS/UEFI", describe(False, False))


class AccelCheckTests(unittest.TestCase):
    def test_reads_the_sentence_and_status(self):
        output = "accel:\r\n0\r\nWHPX(10.0.26200) is installed and usable.\r\naccel\r\n"
        self.assertEqual(info.parse_accel_check(output), "WHPX(10.0.26200) is installed and usable. (status 0)")

    def test_a_failing_check_keeps_its_reason(self):
        output = "accel:\n6\nPlease disable Hyper-V before using the Android Emulator.\naccel\n"
        self.assertIn("(status 6)", info.parse_accel_check(output))

    def test_unexpected_output_is_no_answer(self):
        self.assertIsNone(info.parse_accel_check("PANIC: something else\n"))
        self.assertIsNone(info.accel_check_line(Path("no/such/emulator.exe")))


class EmulatorLogTests(unittest.TestCase):
    def test_a_resumed_start_reports_accelerator_graphics_and_snapshot(self):
        lines = info.emulator_log_lines(RESUME_LOG)
        self.assertEqual(lines, [
            "Emulator: version 37.2.12.0, accelerator Windows Hypervisor Platform (WHPX), operational",
            "Emulator graphics: gfxstream, OpenGL ES host, Vulkan host, Example GPU 3000/PCIe/SSE2",
            "Emulator: memory raised to 2560 MB, resumed from its snapshot in 2955 ms",
        ])

    def test_colour_codes_never_reach_the_log(self):
        self.assertNotIn("\x1b", "".join(info.emulator_log_lines(RESUME_LOG)))

    def test_a_log_with_no_accelerator_says_so(self):
        lines = info.emulator_log_lines("INFO | Graphics backend: gfxstream\n")
        self.assertTrue(any("no accelerator reported" in line for line in lines))

    def test_an_empty_or_missing_log_gives_nothing(self):
        self.assertEqual(info.emulator_log_lines(""), [])
        self.assertEqual(info.emulator_log_lines_from(Path("no/such/emulator.log")), [])


class FeatureProbeTests(unittest.TestCase):
    def test_answers_arrive_from_the_background(self):
        with patch.object(info, "system_lines", return_value=["Processor: Example CPU"]), \
             patch.object(info, "accel_check_line", return_value="Emulator acceleration check: fine (status 0)"):
            probe = info.FeatureProbe(Path("emulator.exe")).start()
            self.assertEqual(probe.lines(), ["Processor: Example CPU", "Emulator acceleration check: fine (status 0)"])

    def test_a_failing_question_never_raises(self):
        with patch.object(info, "system_lines", side_effect=RuntimeError("boom")):
            self.assertEqual(info.FeatureProbe(None).start().lines(), [])

    def test_the_start_log_gets_the_probe_and_the_emulator_log(self):
        class Probe:
            def lines(self, timeout=0):
                return ["Virtualization: a hypervisor is active"]

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "emulator.log"
            log.write_text(RESUME_LOG, encoding="utf-8")
            with patch.object(start_iisu_pc, "EMULATOR_LOG_PATH", log), patch("builtins.print") as printed:
                start_iisu_pc.log_emulation_features(Probe())
            lines = [call.args[0] for call in printed.call_args_list]
            self.assertEqual(lines[0], "[start] Virtualization: a hypervisor is active")
            self.assertEqual(len(lines), 4)
            with patch.object(start_iisu_pc, "EMULATOR_LOG_PATH", log), patch("builtins.print") as printed:
                start_iisu_pc.log_emulation_features(Probe(), debug_console=True)
            self.assertEqual(len(printed.call_args_list), 1)


if __name__ == "__main__":
    unittest.main()
