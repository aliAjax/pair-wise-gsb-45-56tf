"""业务用例编排、权限检查与审计。"""
from typing import Any, Callable, Dict, List, Optional

from . import tides
from .audit import AuditRecorder
from .domain import Actor, PermissionDenied, ValidationError, text
from .repository import Repository
from .rules import DomainRules
from .scheduler import ChannelScheduler, ScheduleDecision, booking_from_record


class Service:
    def __init__(self, repository: Repository, rules: DomainRules, audit: AuditRecorder = None, tide_provider: Callable[[], tides.TideTable] = None) -> None:
        self.repository = repository
        self.rules = rules
        self.audit = audit or AuditRecorder(repository)
        self.tide_provider = tide_provider or tides.default_table

    @staticmethod
    def _actor(actor: Actor) -> Actor:
        if actor is None or not actor.user_id.strip() or not actor.role.strip():
            raise PermissionDenied("缺少调用身份")
        return actor

    def _ensure_known_role(self, actor: Actor) -> None:
        if not self.rules.known_role(actor.role):
            raise PermissionDenied("角色无权访问该服务")

    def create(self, actor: Actor, reference: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        if not self.rules.role_can_create(actor.role):
            raise PermissionDenied("角色无权创建记录")
        reference = text({"reference": reference}, "reference")
        prepared = self.rules.prepare_create(payload or {})
        self.rules.check_create_conflicts(prepared, self.repository.list_records(limit=500))
        return self.repository.create(reference, self.rules.INITIAL_STATE, prepared, actor.user_id)

    def list_records(self, actor: Actor, state: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        return self.repository.list_records(state=state, limit=limit)

    def get_record(self, actor: Actor, record_id: int) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        return self.repository.get(record_id)

    def _schedule(self, record: Dict[str, Any]) -> ScheduleDecision:
        payload = record["payload"]
        if "transit_hour" not in payload:
            raise ValidationError("计划缺少预计过闸时刻")
        bookings = [booking_from_record(item) for item in self.repository.list_channel_bookings(exclude_id=int(record["id"]))]
        scheduler = ChannelScheduler(self.tide_provider())
        return scheduler.decide(
            requested_hour=int(payload["transit_hour"]),
            draft_m=float(payload["draft_m"]),
            dangerous=bool(payload.get("dangerous_goods")),
            bookings=bookings,
        )

    def act(self, actor: Actor, record_id: int, expected_version: int, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        action = text({"action": action}, "action")
        if not self.rules.role_can_action(actor.role, action):
            raise PermissionDenied("角色无权执行该操作")
        record = self.repository.get(record_id)
        self.rules.require_transition(record, action)
        decision = self._schedule(record) if action == "schedule" else None
        new_state, new_payload, summary = self.rules.apply_action(record, action, data or {}, decision=decision)
        return self.repository.mutate(
            record_id=record_id,
            expected_version=int(expected_version),
            state=new_state,
            payload=new_payload,
            actor_id=actor.user_id,
            action=action,
            details={"summary": summary, "input": data or {}, "from": record["state"], "to": new_state},
        )

    def timeline(self, actor: Actor, record_id: int) -> List[Dict[str, Any]]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        return self.audit.timeline(record_id)

    def stats(self, actor: Actor) -> Dict[str, int]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        return self.repository.stats()

    def tide_table_view(self, actor: Actor, draft: Optional[float] = None) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        table = self.tide_provider()
        result: Dict[str, Any] = {
            "date": table.date,
            "channel_base_depth_m": tides.CHANNEL_BASE_DEPTH_M,
            "ukc_required_m": tides.UKC_REQUIRED_M,
            "hours": [{"hour": h, "tide_m": table.height_at(h), "depth_m": table.depth_at(h)} for h in tides.HOURS],
        }
        if draft is not None:
            required = round(float(draft) + tides.UKC_REQUIRED_M, 2)
            result["required_depth_m"] = required
            result["navigable_hours"] = tides.navigable_hours(table, required)
        return result

    def channel_board(self, actor: Actor) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        board = self.tide_table_view(actor)
        bookings = []
        for item in self.repository.list_channel_bookings():
            booking = booking_from_record(item)
            bookings.append({
                "record_id": booking.record_id,
                "reference": booking.reference,
                "vessel": booking.vessel,
                "state": booking.state,
                "start_hour": booking.start_hour,
                "end_hour": booking.end_hour,
                "blocked_start": booking.blocked_start,
                "blocked_end": booking.blocked_end,
                "dangerous": booking.dangerous,
                "dangerous_class": item["payload"].get("dangerous_class", ""),
            })
        bookings.sort(key=lambda entry: entry["start_hour"])
        waiting = []
        for item in self.repository.list_records(state="waiting", limit=100):
            payload = item["payload"]
            waiting.append({
                "record_id": item["id"],
                "reference": item["reference"],
                "vessel": payload.get("vessel", ""),
                "version": item["version"],
                "transit_hour": payload.get("transit_hour"),
                "draft_m": payload.get("draft_m"),
                "dangerous": bool(payload.get("dangerous_goods")),
                "waiting_reason": payload.get("waiting_reason", ""),
            })
        board["bookings"] = bookings
        board["waiting"] = waiting
        return board
