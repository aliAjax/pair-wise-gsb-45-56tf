import tempfile
import unittest
from pathlib import Path

from app import build_service
from src.domain import Actor, Conflict


CREATE_DATA = {'vessel': 'HaiYun', 'berth': 'B12', 'vessel_length_m': 180, 'berth_length_m': 220, 'draft_m': 10.2, 'berth_depth_m': 11.5, 'eta_hour': 6, 'etd_hour': 18, 'risk_level': 'medium', 'dangerous_goods': False, 'dangerous_class': ''}
FLOW = [('confirm', 'port_controller', {'pilot_id': 'P-01'}, 'confirmed'), ('berth', 'port_controller', {'actual_draft_m': 10.3}, 'berthed'), ('depart', 'port_controller', {'cargo_operation_complete': True}, 'departed')]
TIDE = {'date': '2026-09-25', 'channel_depth_m': 12.0, 'heights': [0.0] * 24}
DISPATCHER = Actor('dispatcher', 'port_controller')


def confirm_channel_window(service, reference, desired_hour=6):
    """录入潮位、申请通航并由调度员确认窗口，返回确认后的排班。"""
    service.channel.save_tide(DISPATCHER, TIDE)
    booking = service.channel.request_transit(DISPATCHER, {
        'reference': reference, 'vessel': 'HaiYun', 'draft_m': 10.2,
        'date': TIDE['date'], 'desired_hour': desired_hour, 'duration_hours': 1, 'dangerous_class': '',
    })
    assert booking['state'] == 'scheduled'
    return service.channel.act(DISPATCHER, booking['id'], booking['version'], 'confirm')


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_workflow_and_audit(self):
        record = self.service.create(Actor("creator", "port_controller"), "VOY-21001", CREATE_DATA)
        self.assertEqual(record["state"], "draft")
        booking = confirm_channel_window(self.service, "VOY-21001")
        for action, role, data, expected_state in FLOW:
            record = self.service.act(Actor("operator", role), record["id"], record["version"], action, data)
            self.assertEqual(record["state"], expected_state)
        timeline = self.service.timeline(Actor("creator", "port_controller"), record["id"])
        self.assertEqual(len(timeline), len(FLOW) + 1)
        self.assertEqual(timeline[-1]["action"], FLOW[-1][0])
        booking = self.service.channel.get_booking(DISPATCHER, booking["id"])
        self.assertEqual(booking["state"], "released")
