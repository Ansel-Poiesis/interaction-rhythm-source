import unittest

from window import should_hide_for_close_reason


class WindowClosePolicyTests(unittest.TestCase):
    def test_user_close_hides_to_tray(self):
        self.assertTrue(should_hide_for_close_reason("UserClosing"))

    def test_system_and_process_closes_are_not_vetoed(self):
        self.assertFalse(should_hide_for_close_reason("WindowsShutDown"))
        self.assertFalse(should_hide_for_close_reason("TaskManagerClosing"))
        self.assertFalse(should_hide_for_close_reason("ApplicationExitCall"))


if __name__ == "__main__":
    unittest.main()
