"""港口泊位与航道调度领域规则与状态转换。"""
from typing import Any, Dict, Iterable, Optional, Tuple

from .domain import Conflict, ValidationError, boolean, choice, integer, number, text
from .scheduler import ScheduleDecision


INITIAL_STATE = "draft"
CREATE_ROLES = {'port_controller'}
ACTION_ROLES = {'confirm': {'port_controller'}, 'schedule': {'port_controller'}, 'confirm_window': {'port_controller'}, 'berth': {'port_controller'}, 'depart': {'port_controller'}, 'cancel': {'port_controller'}}
TRANSITIONS = {
    'confirm': {'draft': ('confirmed',)},
    'schedule': {'confirmed': ('scheduled', 'waiting'), 'waiting': ('scheduled', 'waiting')},
    'confirm_window': {'scheduled': ('window_confirmed',)},
    'berth': {'window_confirmed': ('berthed',)},
    'depart': {'berthed': ('departed',)},
    'cancel': {'draft': ('cancelled',), 'confirmed': ('cancelled',), 'scheduled': ('cancelled',), 'waiting': ('cancelled',), 'window_confirmed': ('cancelled',)},
}


class DomainRules:
    INITIAL_STATE = INITIAL_STATE

    def known_role(self, role: str) -> bool:
        all_roles = set(CREATE_ROLES)
        for roles in ACTION_ROLES.values():
            all_roles.update(roles)
        return role == "admin" or role in all_roles

    def role_can_create(self, role: str) -> bool:
        return role == "admin" or role in CREATE_ROLES

    def role_can_action(self, role: str, action: str) -> bool:
        return role == "admin" or role in ACTION_ROLES.get(action, set())

    def validate_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        p = dict(payload)
        vessel = text(p, "vessel")
        berth = text(p, "berth")
        vessel_length = number(p, "vessel_length_m", 1)
        berth_length = number(p, "berth_length_m", 1)
        draft = number(p, "draft_m", 0)
        berth_depth = number(p, "berth_depth_m", 0)
        eta = integer(p, "eta_hour", 0, 23)
        transit = integer(p, "transit_hour", 0, 23)
        etd = integer(p, "etd_hour", 1, 24)
        choice(p, "risk_level", ["low", "medium", "high"])
        dangerous = boolean(p, "dangerous_goods")
        if etd <= eta:
            raise ValidationError("etd_hour必须晚于eta_hour")
        if berth_length < vessel_length:
            raise ValidationError("泊位长度不足")
        if berth_depth - draft < 0.5:
            raise ValidationError("剩余水深不足")
        if dangerous:
            text(p, "dangerous_class")
        return p

    def prepare_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        p = self.validate_create(payload)
        p["safety_margin_m"] = round(float(p["berth_depth_m"]) - float(p["draft_m"]), 2)
        p["window_hours"] = int(p["etd_hour"]) - int(p["eta_hour"])
        p["quay_ok"] = bool(p["berth_length_m"] >= p["vessel_length_m"] and p["safety_margin_m"] >= 0.5)
        return p

    def check_create_conflicts(self, payload: Dict[str, Any], existing: Iterable[Dict[str, Any]]) -> None:
        for item in existing:
            other = item["payload"]
            if item["state"] in {"cancelled", "departed"} or other.get("berth") != payload.get("berth"):
                continue
            if int(payload["eta_hour"]) < int(other.get("etd_hour", 0)) and int(payload["etd_hour"]) > int(other.get("eta_hour", 24)):
                raise Conflict("同一泊位时间窗冲突")

    def require_transition(self, record: Dict[str, Any], action: str) -> Tuple[str, ...]:
        targets = TRANSITIONS.get(action, {}).get(record["state"])
        if targets is None:
            raise Conflict("当前状态不允许执行%s" % action)
        return targets

    @staticmethod
    def _window_label(payload: Dict[str, Any]) -> str:
        return "%02d:00-%02d:00" % (int(payload["window_start_hour"]), int(payload["window_end_hour"]))

    def apply_action(self, record: Dict[str, Any], action: str, data: Dict[str, Any], decision: Optional[ScheduleDecision] = None) -> Tuple[str, Dict[str, Any], str]:
        targets = self.require_transition(record, action)
        data = dict(data or {})
        p = dict(record["payload"])
        changes: Dict[str, Any] = {}
        if action == "confirm":
            changes["pilot_id"] = text(data, "pilot_id")
            new_state = "confirmed"
            summary = "已确认引航员"
        elif action == "schedule":
            if decision is None:
                raise ValidationError("缺少排班结果")
            new_state = decision.status
            if new_state not in targets:
                raise Conflict("排班结果与当前状态不符")
            if decision.status == "scheduled":
                p.pop("waiting_reason", None)
                changes["window_start_hour"] = int(decision.start_hour)
                changes["window_end_hour"] = int(decision.end_hour)
                changes["window_depth_m"] = float(decision.depth_m)
                changes["window_adjusted"] = bool(decision.adjusted)
                label = "%02d:00-%02d:00" % (int(decision.start_hour), int(decision.end_hour))
                summary = "已安排过闸窗口%s（航道水深%.1fm）" % (label, float(decision.depth_m))
                if decision.reason:
                    summary = "%s，%s" % (summary, decision.reason)
            else:
                for key in ("window_start_hour", "window_end_hour", "window_depth_m", "window_adjusted"):
                    p.pop(key, None)
                changes["waiting_reason"] = decision.reason
                summary = "进入候泊名单：%s" % decision.reason
        elif action == "confirm_window":
            new_state = "window_confirmed"
            summary = "调度员已确认通航窗口%s" % self._window_label(p)
        elif action == "berth":
            actual = number(data, "actual_draft_m", 0)
            if float(p["berth_depth_m"]) - actual < 0.5:
                raise ValidationError("实际吃水导致水深不足")
            changes["actual_draft_m"] = actual
            new_state = "berthed"
            summary = "船舶已靠泊"
        elif action == "depart":
            if not boolean(data, "cargo_operation_complete"):
                raise ValidationError("货物作业尚未完成")
            new_state = "departed"
            summary = "船舶已离泊，航道已释放"
        elif action == "cancel":
            changes["cancel_reason"] = text(data, "cancel_reason")
            new_state = "cancelled"
            summary = "计划已取消"
        else:
            raise Conflict("未知操作%s" % action)
        p.update(changes)
        return new_state, p, summary
