import unittest
from unittest import mock

from interaction_rhythm.windows_raw_input import (
    RI_KEY_BREAK,
    RI_MOUSE_HWHEEL,
    RI_MOUSE_LEFT_BUTTON_DOWN,
    RI_MOUSE_WHEEL,
    WM_KEYDOWN,
    WM_KEYUP,
    RawInputCounter,
    raw_input_device_inventory,
)
from scripts.benchmark_windows_raw_input import compare_aggregate_counts, run_raw_input_benchmark
from interaction_rhythm.win32_input_protocol import benchmark_extra_info


class WindowsRawInputTests(unittest.TestCase):
    def test_counter_normalizes_repeat_without_retaining_sensitive_values(self):
        counter = RawInputCounter()
        counter.record_keyboard(WM_KEYDOWN, 1, 0, 101)
        counter.record_keyboard(WM_KEYDOWN, 1, 0, 101)
        counter.record_keyboard(WM_KEYUP, 1, RI_KEY_BREAK, 101)
        counter.record_mouse(RI_MOUSE_LEFT_BUTTON_DOWN | RI_MOUSE_WHEEL, 202)
        counter.record_mouse(RI_MOUSE_HWHEEL, 202)

        result = counter.snapshot

        self.assertEqual(result["keyboard_raw_down_messages"], 2)
        self.assertEqual(result["keyboard_presses"], 1)
        self.assertEqual(result["keyboard_repeat_messages"], 1)
        self.assertEqual(result["keyboard_releases"], 1)
        self.assertEqual(result["mouse_button_presses"], 1)
        self.assertEqual(result["vertical_scroll_events"], 1)
        self.assertEqual(result["horizontal_scroll_events"], 1)
        self.assertEqual(result["distinct_device_count"], 2)
        self.assertFalse(result["key_values_retained"])
        self.assertFalse(result["device_identifiers_retained"])
        self.assertFalse(result["coordinates_accessed"])
        self.assertNotIn("make_code", result)

    def test_counter_filters_marked_raw_input_before_normalization(self):
        counter = RawInputCounter()
        marker = benchmark_extra_info(7)

        counter.record_keyboard(WM_KEYDOWN, 1, 0, 0, marker)
        counter.record_keyboard(WM_KEYUP, 1, RI_KEY_BREAK, 0, marker)
        counter.record_mouse(RI_MOUSE_LEFT_BUTTON_DOWN, 0, marker)
        result = counter.snapshot

        self.assertEqual(result["keyboard_packets"], 2)
        self.assertEqual(result["mouse_packets"], 1)
        self.assertEqual(result["benchmark_packets_ignored"], 3)
        self.assertEqual(result["keyboard_presses"], 0)
        self.assertEqual(result["keyboard_releases"], 0)
        self.assertEqual(result["mouse_button_presses"], 0)

    def test_comparison_requires_observed_input_and_exact_aggregate_match(self):
        empty = compare_aggregate_counts({}, {})
        matched = compare_aggregate_counts(
            {"keyboard_presses": 2, "mouse_button_presses": 1, "vertical_scroll_events": 1},
            {"keyboard_presses": 2, "mouse_button_presses": 1, "scroll_events": 1},
        )
        mismatch = compare_aggregate_counts(
            {"keyboard_presses": 1}, {"keyboard_presses": 2},
        )

        self.assertEqual(empty["status"], "insufficient_physical_input")
        self.assertFalse(empty["exact_match"])
        self.assertTrue(matched["exact_match"])
        self.assertFalse(mismatch["exact_match"])

    def test_real_message_only_transport_registers_and_releases(self):
        report = run_raw_input_benchmark()

        self.assertTrue(report["passed"])
        self.assertFalse(report["replacement_gate_passed"])
        self.assertEqual(report["comparison"]["status"], "transport_only")
        self.assertIsNone(report["injection_filter_passed"])
        self.assertFalse(report["hook_probe_started"])
        self.assertEqual(report["registration_stats"]["window_kind"], "message_only")
        self.assertTrue(report["registration_stats"]["devices_registered"])
        self.assertTrue(report["resource_released"])
        self.assertFalse(report["final_stats"]["window_active"])
        self.assertFalse(report["final_stats"]["devices_registered"])
        self.assertIsNone(report["database_write_path"])
        inventory = raw_input_device_inventory()
        self.assertEqual(inventory["total"], sum(inventory[key] for key in ("keyboard", "mouse", "hid", "other")))
        self.assertFalse(inventory["device_names_retained"])

    def test_injection_fixture_refuses_an_active_canonical_recorder(self):
        with mock.patch(
            "scripts.benchmark_windows_raw_input.local_recorder_active",
            return_value=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "canonical recorder is active"):
                run_raw_input_benchmark(injection_count=1)


if __name__ == "__main__":
    unittest.main()
