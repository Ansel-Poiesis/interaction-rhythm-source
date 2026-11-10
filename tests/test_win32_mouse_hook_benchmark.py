from collections import Counter
import unittest

from monitor import WM_LBUTTONDOWN, WM_MOUSEWHEEL
from scripts.benchmark_win32_mouse_hook import (
    _fixture_spec,
    _observed_detail,
    analyze_mouse_records,
)


class Win32MouseHookBenchmarkTests(unittest.TestCase):
    def test_fixture_contract_separates_buttons_and_wheel_directions(self):
        self.assertEqual(_fixture_spec("left")[:2], (WM_LBUTTONDOWN, 0))
        self.assertEqual(_fixture_spec("wheel_up")[:2], (WM_MOUSEWHEEL, 120))
        self.assertEqual(_fixture_spec("wheel_down")[:2], (WM_MOUSEWHEEL, -120))
        self.assertEqual(_observed_detail(WM_MOUSEWHEEL, 120 << 16), 120)
        self.assertEqual(
            _observed_detail(WM_MOUSEWHEEL, ((-120) & 0xFFFF) << 16),
            -120,
        )

    def test_analysis_detects_delivery_and_foreground_attribution(self):
        expected = Counter({(1, WM_LBUTTONDOWN, 0): 1})
        records = [{
            "sequence": 1,
            "message": WM_LBUTTONDOWN,
            "detail": 0,
            "app": "IRBenchA.exe",
            "latency_ms": 1,
        }]

        passed = analyze_mouse_records(expected, records, {1: "IRBenchA.exe"})
        moved = analyze_mouse_records(expected, records, {1: "IRBenchB.exe"})

        self.assertEqual(passed["lost_event_count"], 0)
        self.assertEqual(passed["duplicate_event_count"], 0)
        self.assertEqual(passed["foreground_attribution_error_count"], 0)
        self.assertEqual(moved["foreground_attribution_error_count"], 1)


if __name__ == "__main__":
    unittest.main()
