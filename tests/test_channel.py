import tempfile
import unittest
from pathlib import Path

from app import build_service
from src import tides
from src.domain import Actor, Conflict
from src.scheduler import ChannelBooking, ChannelScheduler
from src.tides import TideTable


CREATE_DATA = {'vessel': 'HaiYun', 'berth': 'B12', 'vessel_length_m': 180, 'berth_length_m': 220, 'draft_m': 10.2, 'berth_depth_m': 11.5, 'eta_hour': 6, 'etd_hour': 18, 'transit_hour': 6, 'risk_level': 'medium', 'dangerous_goods': False, 'dangerous_class': ''}
CONTROLLER = Actor("operator", "port_controller")


def flat_tide(height):
    return TideTable(date="2026-09-25", heights=tuple([height] * 24))


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.scheduler = ChannelScheduler(tides.default_table("2026-09-25"))

    def test_requested_window_used_when_free(self):
        decision = self.scheduler.decide(requested_hour=6, draft_m=10.2, dangerous=False, bookings=[])
        self.assertEqual(decision.status, "scheduled")
        self.assertEqual((decision.start_hour, decision.end_hour), (6, 7))
        self.assertFalse(decision.adjusted)

    def test_shallow_request_moves_to_nearest_window(self):
        decision = self.scheduler.decide(requested_hour=10, draft_m=10.2, dangerous=False, bookings=[])
        self.assertEqual(decision.status, "scheduled")
        self.assertEqual(decision.start_hour, 9)
        self.assertTrue(decision.adjusted)
        self.assertIn("水深不足", decision.reason)

    def test_conflict_moves_to_nearest_free_window(self):
        booking = ChannelBooking(record_id=1, reference="VOY-1", vessel="A", state="scheduled", start_hour=9, end_hour=10, dangerous=False)
        decision = self.scheduler.decide(requested_hour=9, draft_m=10.2, dangerous=False, bookings=[booking])
        self.assertEqual(decision.status, "scheduled")
        self.assertEqual(decision.start_hour, 8)
        self.assertIn("冲突", decision.reason)

    def test_dangerous_booking_blocks_neighbor_hours(self):
        booking = ChannelBooking(record_id=1, reference="VOY-1", vessel="A", state="scheduled", start_hour=6, end_hour=7, dangerous=True)
        decision = self.scheduler.decide(requested_hour=6, draft_m=10.2, dangerous=False, bookings=[booking])
        self.assertEqual(decision.status, "scheduled")
        self.assertEqual(decision.start_hour, 4)

    def test_dangerous_ship_keeps_distance_from_booking(self):
        booking = ChannelBooking(record_id=1, reference="VOY-1", vessel="A", state="scheduled", start_hour=6, end_hour=7, dangerous=False)
        decision = self.scheduler.decide(requested_hour=6, draft_m=10.2, dangerous=True, bookings=[booking])
        self.assertEqual(decision.status, "scheduled")
        self.assertEqual(decision.start_hour, 4)

    def test_waiting_when_depth_never_enough(self):
        scheduler = ChannelScheduler(flat_tide(0.0))
        decision = scheduler.decide(requested_hour=6, draft_m=10.2, dangerous=False, bookings=[])
        self.assertEqual(decision.status, "waiting")
        self.assertIn("水深不足", decision.reason)

    def test_waiting_when_channel_full(self):
        hours = list(range(1, 10)) + list(range(13, 22))
        bookings = [ChannelBooking(record_id=i, reference="VOY-%d" % i, vessel="V", state="scheduled", start_hour=h, end_hour=h + 1, dangerous=False) for i, h in enumerate(hours)]
        decision = self.scheduler.decide(requested_hour=6, draft_m=10.2, dangerous=False, bookings=bookings)
        self.assertEqual(decision.status, "waiting")
        self.assertIn("占用", decision.reason)


class ChannelFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))

    def tearDown(self):
        self.temp.cleanup()

    def _create(self, reference, **overrides):
        data = dict(CREATE_DATA)
        data.update(overrides)
        return self.service.create(Actor("creator", "port_controller"), reference, data)

    def _act(self, record, action, data=None):
        return self.service.act(CONTROLLER, record["id"], record["version"], action, data or {})

    def test_berth_requires_confirmed_window(self):
        record = self._create("VOY-31001")
        record = self._act(record, "confirm", {"pilot_id": "P-01"})
        with self.assertRaises(Conflict):
            self._act(record, "berth", {"actual_draft_m": 10.3})

    def test_dangerous_buffer_adjusts_next_ship(self):
        first = self._create("VOY-31002", dangerous_goods=True, dangerous_class="3类易燃液体")
        first = self._act(first, "confirm", {"pilot_id": "P-01"})
        first = self._act(first, "schedule")
        self.assertEqual(first["payload"]["window_start_hour"], 6)
        second = self._create("VOY-31003", berth="B13")
        second = self._act(second, "confirm", {"pilot_id": "P-02"})
        second = self._act(second, "schedule")
        self.assertEqual(second["state"], "scheduled")
        self.assertEqual(second["payload"]["window_start_hour"], 4)
        self.assertTrue(second["payload"]["window_adjusted"])

    def test_waiting_then_depart_releases_channel(self):
        self.service.tide_provider = lambda: TideTable(date="2026-09-25", heights=tuple(1.2 if h == 6 else 0.0 for h in range(24)))
        first = self._create("VOY-31004")
        first = self._act(first, "confirm", {"pilot_id": "P-01"})
        first = self._act(first, "schedule")
        self.assertEqual(first["state"], "scheduled")
        second = self._create("VOY-31005", berth="B13")
        second = self._act(second, "confirm", {"pilot_id": "P-02"})
        second = self._act(second, "schedule")
        self.assertEqual(second["state"], "waiting")
        self.assertIn("VOY-31004", second["payload"]["waiting_reason"])
        first = self._act(first, "confirm_window")
        first = self._act(first, "berth", {"actual_draft_m": 10.3})
        first = self._act(first, "depart", {"cargo_operation_complete": True})
        second = self._act(second, "schedule")
        self.assertEqual(second["state"], "scheduled")
        self.assertEqual(second["payload"]["window_start_hour"], 6)

    def test_tide_view_marks_navigable_hours(self):
        view = self.service.tide_table_view(CONTROLLER, draft=10.2)
        self.assertEqual(view["navigable_hours"], list(range(1, 10)) + list(range(13, 22)))
