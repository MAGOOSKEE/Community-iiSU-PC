"""The extra detail in the logs: line times, the PC summary, and Setup's
virtualization line and step durations."""

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))
sys.path.insert(0, str(PROJECT_ROOT / "installer"))

import setup_wizard  # noqa: E402
from shared import adb_client, log_stamp, system_info  # noqa: E402


class TimestampedStreamTests(unittest.TestCase):
    def _stream(self):
        target = io.StringIO()
        return target, log_stamp.TimestampedStream(target, clock=lambda: 0.25)

    def test_every_line_starts_with_its_time(self):
        target, stream = self._stream()
        stream.write("one\ntwo\n")
        lines = target.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        for line, text in zip(lines, ("one", "two")):
            self.assertRegex(line, rf"^\d\d:\d\d:\d\d\.250 {text}$")

    def test_a_line_written_in_pieces_gets_one_time(self):
        target, stream = self._stream()
        for piece in ("[bridge] laun", "ching", "\n", "next\n"):
            stream.write(piece)
        lines = target.getvalue().splitlines()
        self.assertTrue(lines[0].endswith(" [bridge] launching"))
        self.assertEqual(lines[0].count(":"), 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith(" next"))

    def test_print_works_through_it(self):
        target, stream = self._stream()
        print("hello", "there", file=stream)
        self.assertTrue(target.getvalue().endswith(" hello there\n"))

    def test_lines_from_two_threads_never_share_a_line(self):
        import threading

        target, stream = self._stream()
        barrier = threading.Barrier(2)

        def chatter(name: str) -> None:
            barrier.wait()
            for number in range(200):
                # print() writes the text and the line end separately.
                print(f"[{name}] line {number}", file=stream)

        threads = [threading.Thread(target=chatter, args=(name,)) for name in ("bridge", "controller")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        lines = target.getvalue().splitlines()
        self.assertEqual(len(lines), 400)
        for line in lines:
            self.assertRegex(line, r"^\d\d:\d\d:\d\d\.250 \[(bridge|controller)\] line \d+$")

    def test_an_unfinished_line_is_written_on_flush(self):
        target, stream = self._stream()
        stream.write("no line end yet")
        self.assertEqual(target.getvalue(), "")
        stream.flush()
        self.assertTrue(target.getvalue().endswith(" no line end yet\n"))

    def test_no_output_at_all_is_left_alone(self):
        with patch.object(sys, "stdout", None):
            log_stamp.stamp_stdout()
            self.assertIsNone(sys.stdout)

    def test_a_log_file_is_stamped_once(self):
        target = io.StringIO()
        with patch.object(sys, "stdout", target), patch.object(sys, "stderr", target):
            log_stamp.stamp_stdout()
            first = sys.stdout
            log_stamp.stamp_stdout()
            self.assertIsInstance(first, log_stamp.TimestampedStream)
            self.assertIs(sys.stdout, first)


class SystemInfoTests(unittest.TestCase):
    def test_the_summary_names_the_pc_and_the_install(self):
        text = "\n".join(system_info.summary_lines(PROJECT_ROOT))
        self.assertIn("System:", text)
        self.assertIn("logical processors", text)
        self.assertIn(f"Install folder: {PROJECT_ROOT}", text)
        self.assertIn("GB free", text)

    def test_windows_11_is_named_by_its_build_number(self):
        self.assertEqual(system_info.system_name("Windows", "10", "10.0.26200"), "Windows 11")
        self.assertEqual(system_info.system_name("Windows", "10", "10.0.19045"), "Windows 10")
        self.assertEqual(system_info.system_name("Windows", "10", "odd"), "Windows 10")
        self.assertEqual(system_info.system_name("Linux", "6.8.0", "#1 SMP"), "Linux 6.8.0")

    def test_memory_is_read_and_plausible(self):
        total, free = system_info.memory_gb()
        self.assertGreater(total, 0.5)
        self.assertLessEqual(free, total)

    def test_a_folder_that_is_not_there_still_gets_a_line(self):
        lines = system_info.summary_lines(PROJECT_ROOT / "no" / "such" / "folder")
        self.assertTrue(any(line.startswith("Install folder:") for line in lines))


class SetupDetailTests(unittest.TestCase):
    def test_durations_read_naturally(self):
        self.assertEqual(setup_wizard.format_duration(0.4), "0s")
        self.assertEqual(setup_wizard.format_duration(42.4), "42s")
        self.assertEqual(setup_wizard.format_duration(185), "3m 05s")


class AdbServerRunningTests(unittest.TestCase):
    def test_no_server_is_reported_without_starting_one(self):
        with patch.object(adb_client, "_connect", side_effect=OSError("refused")), \
             patch.object(adb_client, "start_server") as start:
            self.assertFalse(adb_client.server_running())
        start.assert_not_called()

    def test_an_answering_server_is_running(self):
        class Connection:
            def close(self):
                pass

        with patch.object(adb_client, "_connect", return_value=Connection()):
            self.assertTrue(adb_client.server_running())


if __name__ == "__main__":
    unittest.main()
