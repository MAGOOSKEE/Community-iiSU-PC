"""Setup's step checklist (bridge/ui/widgets/step_checklist.py): which step
is running, done or failed as Setup reports its stages. Offscreen, skipped
without PySide6."""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import PySide6  # noqa: F401
except ImportError:  # pragma: no cover; depends on the environment
    PySide6 = None


@unittest.skipIf(PySide6 is None, "PySide6 is not installed")
class StepChecklistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import bridge.ui  # noqa: F401
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def make(self):
        from bridge.ui.widgets.step_checklist import StepChecklist

        return StepChecklist(["One", "Two", "Three"], {"Two": "slow"})

    def test_starting_a_step_finishes_the_ones_before(self):
        from bridge.ui.widgets.step_checklist import DONE, PENDING, RUNNING

        checklist = self.make()
        checklist.start(1)
        self.assertEqual([checklist.state(i) for i in range(3)], [DONE, RUNNING, PENDING])
        self.assertEqual(checklist.done_count(), 1)

    def test_failure_marks_the_running_step(self):
        from bridge.ui.widgets.step_checklist import FAILED, PENDING

        checklist = self.make()
        checklist.start(1)
        checklist.fail_current()
        self.assertEqual(checklist.state(1), FAILED)
        self.assertEqual(checklist.state(2), PENDING)

    def test_finish_all(self):
        checklist = self.make()
        checklist.start(0)
        checklist.finish_all()
        self.assertEqual(checklist.done_count(), 3)

    def test_format_elapsed(self):
        from bridge.ui.widgets.step_checklist import format_elapsed

        self.assertEqual(format_elapsed(5.4), "5s")
        self.assertEqual(format_elapsed(125), "2:05")

    def test_setup_window_lists_every_stage_by_its_shown_name(self):
        import bridge.ui.setup_app as setup_app
        import setup_wizard

        for stage in setup_wizard.SETUP_STAGES + setup_wizard.WAYDROID_SETUP_STAGES:
            name = setup_app.STAGE_NAMES.get(stage, stage)
            self.assertTrue(name)
        for hinted in setup_app.STAGE_HINTS:
            shown = set(setup_app.STAGE_NAMES.values()) | set(setup_wizard.SETUP_STAGES) | {setup_app.DOWNLOAD_STAGE}
            self.assertIn(hinted, shown)


if __name__ == "__main__":
    unittest.main()
