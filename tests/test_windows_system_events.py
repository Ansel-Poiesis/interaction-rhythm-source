import unittest

from interaction_rhythm.windows_system_events import (
    PBT_APMRESUMEAUTOMATIC,
    PBT_APMSUSPEND,
    WM_ENDSESSION,
    WM_POWERBROADCAST,
    WM_WTSSESSION_CHANGE,
    WTS_SESSION_CONNECT,
    WTS_SESSION_DISCONNECT,
    WTS_SESSION_LOGOFF,
    WTS_SESSION_LOCK,
    normalize_system_event,
)
from scripts.benchmark_windows_system_events import (
    FIXTURE_SEQUENCE,
    run_windows_system_event_benchmark,
)


class WindowsSystemEventTests(unittest.TestCase):
    def test_message_codes_normalize_without_session_or_payload_content(self):
        locked = normalize_system_event(WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK)
        suspended = normalize_system_event(WM_POWERBROADCAST, PBT_APMSUSPEND)
        resumed = normalize_system_event(WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC)
        ending = normalize_system_event(WM_ENDSESSION, 1)

        self.assertEqual(locked.as_dict(), {
            "source": "wts", "kind": "session_lock", "code": WTS_SESSION_LOCK,
        })
        disconnected = normalize_system_event(WM_WTSSESSION_CHANGE, WTS_SESSION_DISCONNECT)
        connected = normalize_system_event(WM_WTSSESSION_CHANGE, WTS_SESSION_CONNECT)
        logged_off = normalize_system_event(WM_WTSSESSION_CHANGE, WTS_SESSION_LOGOFF)
        self.assertEqual(disconnected.kind, "session_disconnect")
        self.assertEqual(connected.kind, "session_connect")
        self.assertEqual(logged_off.kind, "session_logoff")
        self.assertEqual(suspended.kind, "suspend")
        self.assertEqual(resumed.kind, "resume_automatic")
        self.assertEqual(ending.kind, "session_end")
        self.assertIsNone(normalize_system_event(WM_ENDSESSION, 0))
        self.assertIsNone(normalize_system_event(0, 0))

    def test_hidden_window_registers_dispatches_and_releases(self):
        report = run_windows_system_event_benchmark()

        self.assertTrue(report["passed"])
        self.assertEqual(
            [event["kind"] for event in report["events"]],
            list(FIXTURE_SEQUENCE),
        )
        self.assertTrue(report["registration_stats"]["wts_registered"])
        self.assertEqual(report["query_end_session_result"], 1)
        self.assertEqual(report["delivery_stats"]["event_count"], 6)
        self.assertTrue(report["architecture"]["production_collector_integration"])
        self.assertFalse(
            report["architecture"]["integration_exercised_by_this_benchmark"]
        )
        self.assertTrue(report["resource_released"])
        self.assertFalse(report["final_stats"]["healthy"])
        self.assertFalse(report["final_stats"]["window_active"])
        self.assertFalse(report["final_stats"]["class_registered"])
        self.assertFalse(report["final_stats"]["thread_alive"])


if __name__ == "__main__":
    unittest.main()
