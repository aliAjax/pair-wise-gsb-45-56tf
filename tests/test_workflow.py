import tempfile
import unittest
from pathlib import Path

from app import build_service
from src.domain import Actor, Conflict


CREATE_DATA = {'vessel': 'HaiYun', 'berth': 'B12', 'vessel_length_m': 180, 'berth_length_m': 220, 'draft_m': 10.2, 'berth_depth_m': 11.5, 'eta_hour': 6, 'etd_hour': 18, 'transit_hour': 6, 'risk_level': 'medium', 'dangerous_goods': False, 'dangerous_class': ''}
FLOW = [('confirm', 'port_controller', {'pilot_id': 'P-01'}, 'confirmed'), ('schedule', 'port_controller', {}, 'scheduled'), ('confirm_window', 'port_controller', {}, 'window_confirmed'), ('berth', 'port_controller', {'actual_draft_m': 10.3}, 'berthed'), ('depart', 'port_controller', {'cargo_operation_complete': True}, 'departed')]


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_workflow_and_audit(self):
        record = self.service.create(Actor("creator", "port_controller"), "VOY-21001", CREATE_DATA)
        self.assertEqual(record["state"], "draft")
        for action, role, data, expected_state in FLOW:
            record = self.service.act(Actor("operator", role), record["id"], record["version"], action, data)
            self.assertEqual(record["state"], expected_state)
        timeline = self.service.timeline(Actor("creator", "port_controller"), record["id"])
        self.assertEqual(len(timeline), len(FLOW) + 1)
        self.assertEqual(timeline[-1]["action"], FLOW[-1][0])

    def test_schedule_assigns_transit_window(self):
        record = self.service.create(Actor("creator", "port_controller"), "VOY-21002", CREATE_DATA)
        record = self.service.act(Actor("operator", "port_controller"), record["id"], record["version"], "confirm", {"pilot_id": "P-01"})
        record = self.service.act(Actor("operator", "port_controller"), record["id"], record["version"], "schedule", {})
        self.assertEqual(record["state"], "scheduled")
        self.assertEqual(record["payload"]["window_start_hour"], 6)
        self.assertEqual(record["payload"]["window_end_hour"], 7)
        self.assertFalse(record["payload"]["window_adjusted"])
