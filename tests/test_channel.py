import tempfile
import unittest
from pathlib import Path

from app import build_service
from src.domain import Actor, Conflict, NotFound, PermissionDenied, ValidationError


DISPATCHER = Actor("dispatcher", "port_controller")
DATE = "2026-09-25"
CREATE_DATA = {'vessel': 'HaiYun', 'berth': 'B12', 'vessel_length_m': 180, 'berth_length_m': 220, 'draft_m': 10.2, 'berth_depth_m': 11.5, 'eta_hour': 6, 'etd_hour': 18, 'risk_level': 'medium', 'dangerous_goods': False, 'dangerous_class': ''}


def tide_payload(heights, depth=12.0):
    return {"date": DATE, "channel_depth_m": depth, "heights": heights}


def request_payload(reference, desired_hour, draft=10.0, duration=1, dangerous_class=""):
    return {
        "reference": reference, "vessel": "V-" + reference, "draft_m": draft,
        "date": DATE, "desired_hour": desired_hour, "duration_hours": duration,
        "dangerous_class": dangerous_class,
    }


class ChannelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))
        self.channel = self.service.channel

    def tearDown(self):
        self.temp.cleanup()

    def save_tide(self, heights=None, depth=12.0):
        return self.channel.save_tide(DISPATCHER, tide_payload(heights if heights is not None else [0.0] * 24, depth))

    def request(self, reference, desired_hour, **kwargs):
        return self.channel.request_transit(DISPATCHER, request_payload(reference, desired_hour, **kwargs))

    def confirm_window(self, reference, desired_hour=6, **kwargs):
        booking = self.request(reference, desired_hour, **kwargs)
        assert booking["state"] == "scheduled"
        return self.channel.act(DISPATCHER, booking["id"], booking["version"], "confirm")

    def test_tide_save_and_read(self):
        self.save_tide()
        tide = self.channel.get_tide(DISPATCHER, DATE)
        self.assertEqual(tide["channel_depth_m"], 12.0)
        self.assertEqual(len(tide["heights"]), 24)
        self.assertEqual(tide["depths"][0], 12.0)

    def test_tide_validation(self):
        with self.assertRaises(ValidationError):
            self.channel.save_tide(DISPATCHER, tide_payload([0.0] * 23))
        with self.assertRaises(ValidationError):
            self.channel.save_tide(DISPATCHER, {"date": "2026-13-01", "heights": [0.0] * 24})
        with self.assertRaises(NotFound):
            self.channel.get_tide(DISPATCHER, "2026-01-01")

    def test_schedule_at_desired_hour(self):
        self.save_tide()
        booking = self.request("A1", 6)
        self.assertEqual(booking["state"], "scheduled")
        self.assertEqual(booking["start_hour"], 6)
        self.assertEqual(booking["end_hour"], 7)

    def test_depth_shortage_moves_to_nearest_window(self):
        heights = [0.0] * 10 + [0.5] * 14  # 0-9时水深11.0米，10时起11.5米
        self.save_tide(heights, depth=11.0)
        booking = self.request("A2", 6, draft=10.6)
        self.assertEqual(booking["state"], "scheduled")
        self.assertEqual(booking["start_hour"], 10)

    def test_channel_one_ship_at_a_time(self):
        self.save_tide()
        first = self.request("A3", 6, duration=2)
        self.assertEqual(first["start_hour"], 6)
        second = self.request("A4", 6, duration=2)
        self.assertEqual(second["start_hour"], 8)

    def test_dangerous_goods_buffer(self):
        self.save_tide()
        dangerous = self.request("A5", 10, dangerous_class="易燃液体")
        self.assertEqual(dangerous["start_hour"], 10)
        after = self.request("A6", 11)
        self.assertEqual(after["start_hour"], 12)
        before = self.request("A7", 9)
        self.assertEqual(before["start_hour"], 8)

    def test_dangerous_candidate_keeps_buffer(self):
        self.save_tide()
        self.request("A8", 15)
        dangerous = self.request("A9", 16, dangerous_class="爆炸品")
        self.assertEqual(dangerous["start_hour"], 17)

    def test_waiting_when_depth_impossible(self):
        self.save_tide(depth=10.0)
        booking = self.request("B1", 6, draft=10.2)
        self.assertEqual(booking["state"], "waiting")
        self.assertIn("水深不足", booking["wait_reason"])
        waiting = self.channel.waiting(DISPATCHER)
        self.assertEqual([item["reference"] for item in waiting], ["B1"])

    def test_waiting_when_channel_full(self):
        self.save_tide()
        for index, hour in enumerate([0, 6, 12, 18]):
            booking = self.request("C%d" % index, hour, duration=6)
            self.assertEqual(booking["state"], "scheduled")
        booking = self.request("C9", 3)
        self.assertEqual(booking["state"], "waiting")
        self.assertIn("冲突", booking["wait_reason"])

    def test_request_requires_tide(self):
        with self.assertRaises(ValidationError):
            self.request("D1", 6)

    def test_duplicate_active_reference_rejected(self):
        self.save_tide()
        self.request("D2", 6)
        with self.assertRaises(Conflict):
            self.request("D2", 8)

    def test_confirm_version_conflict(self):
        self.save_tide()
        booking = self.request("D3", 6)
        with self.assertRaises(Conflict):
            self.channel.act(DISPATCHER, booking["id"], booking["version"] + 1, "confirm")

    def test_berth_requires_confirmed_window(self):
        record = self.service.create(Actor("creator", "port_controller"), "VOY-1", CREATE_DATA)
        record = self.service.act(Actor("operator", "port_controller"), record["id"], record["version"], "confirm", {"pilot_id": "P-1"})
        with self.assertRaises(Conflict):
            self.service.act(Actor("operator", "port_controller"), record["id"], record["version"], "berth", {"actual_draft_m": 10.3})

    def test_berth_blocked_while_waiting(self):
        self.save_tide(depth=10.0)
        booking = self.request("VOY-2", 6, draft=10.2)
        self.assertEqual(booking["state"], "waiting")
        record = self.service.create(Actor("creator", "port_controller"), "VOY-2", CREATE_DATA)
        record = self.service.act(Actor("operator", "port_controller"), record["id"], record["version"], "confirm", {"pilot_id": "P-1"})
        with self.assertRaises(Conflict) as ctx:
            self.service.act(Actor("operator", "port_controller"), record["id"], record["version"], "berth", {"actual_draft_m": 10.3})
        self.assertIn("候泊", str(ctx.exception))

    def test_depart_releases_channel(self):
        self.save_tide()
        booking = self.confirm_window("VOY-3", 6)
        record = self.service.create(Actor("creator", "port_controller"), "VOY-3", CREATE_DATA)
        record = self.service.act(Actor("operator", "port_controller"), record["id"], record["version"], "confirm", {"pilot_id": "P-1"})
        record = self.service.act(Actor("operator", "port_controller"), record["id"], record["version"], "berth", {"actual_draft_m": 10.3})
        record = self.service.act(Actor("operator", "port_controller"), record["id"], record["version"], "depart", {"cargo_operation_complete": True})
        self.assertEqual(record["state"], "departed")
        booking = self.channel.get_booking(DISPATCHER, booking["id"])
        self.assertEqual(booking["state"], "released")
        again = self.request("D9", 6)
        self.assertEqual(again["start_hour"], 6)

    def test_permission_denied(self):
        outsider = Actor("outsider", "outsider")
        with self.assertRaises(PermissionDenied):
            self.channel.save_tide(outsider, tide_payload([0.0] * 24))
        with self.assertRaises(PermissionDenied):
            self.channel.request_transit(outsider, request_payload("E1", 6))
        with self.assertRaises(PermissionDenied):
            self.channel.board(outsider, DATE)

    def test_board_groups_waiting(self):
        self.save_tide(depth=10.0)
        self.request("F1", 6, draft=10.2)
        board = self.channel.board(DISPATCHER, DATE)
        self.assertEqual(board["bookings"], [])
        self.assertEqual(len(board["waiting"]), 1)
        self.assertTrue(board["waiting"][0]["wait_reason"])
