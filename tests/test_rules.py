import unittest

from src.domain import Actor, Conflict, ValidationError
from src.rules import DomainRules
from src.scheduler import ScheduleDecision


CREATE_DATA = {'vessel': 'HaiYun', 'berth': 'B12', 'vessel_length_m': 180, 'berth_length_m': 220, 'draft_m': 10.2, 'berth_depth_m': 11.5, 'eta_hour': 6, 'etd_hour': 18, 'transit_hour': 6, 'risk_level': 'medium', 'dangerous_goods': False, 'dangerous_class': ''}
FLOW = [('confirm', 'port_controller', {'pilot_id': 'P-01'}, 'confirmed'), ('schedule', 'port_controller', {}, 'scheduled'), ('confirm_window', 'port_controller', {}, 'window_confirmed'), ('berth', 'port_controller', {'actual_draft_m': 10.3}, 'berthed'), ('depart', 'port_controller', {'cargo_operation_complete': True}, 'departed')]


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.rules = DomainRules()

    def test_prepare_create(self):
        prepared = self.rules.prepare_create(CREATE_DATA)
        self.assertEqual(prepared["safety_margin_m"], 1.3)
        self.assertTrue(prepared["quay_ok"])
        self.assertEqual(prepared["window_hours"], 12)

    def test_action_calculation(self):
        action, role, data, expected_state = FLOW[0]
        record = {"id": 1, "state": self.rules.INITIAL_STATE, "payload": self.rules.prepare_create(CREATE_DATA)}
        state, payload, summary = self.rules.apply_action(record, action, data)
        self.assertEqual(state, expected_state)
        self.assertEqual(payload["pilot_id"], "P-01")

    def test_schedule_decision_applied(self):
        record = {"id": 1, "state": "confirmed", "payload": self.rules.prepare_create(CREATE_DATA)}
        decision = ScheduleDecision(status="scheduled", requested_hour=6, start_hour=6, end_hour=7, depth_m=13.07)
        state, payload, summary = self.rules.apply_action(record, "schedule", {}, decision=decision)
        self.assertEqual(state, "scheduled")
        self.assertEqual(payload["window_start_hour"], 6)
        self.assertEqual(payload["window_end_hour"], 7)

    def test_schedule_waiting_records_reason(self):
        record = {"id": 1, "state": "confirmed", "payload": self.rules.prepare_create(CREATE_DATA)}
        decision = ScheduleDecision(status="waiting", requested_hour=6, reason="当日无可走窗口")
        state, payload, summary = self.rules.apply_action(record, "schedule", {}, decision=decision)
        self.assertEqual(state, "waiting")
        self.assertEqual(payload["waiting_reason"], "当日无可走窗口")

    def test_berth_requires_confirmed_window(self):
        record = {"id": 1, "state": "confirmed", "payload": self.rules.prepare_create(CREATE_DATA)}
        with self.assertRaises(Conflict):
            self.rules.apply_action(record, "berth", {"actual_draft_m": 10.3})

    def test_invalid_input(self):
        invalid = dict(CREATE_DATA)
        invalid["draft_m"] = 12.0
        with self.assertRaises(ValidationError):
            self.rules.prepare_create(invalid)
