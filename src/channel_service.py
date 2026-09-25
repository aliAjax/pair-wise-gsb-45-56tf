"""潮汐通航台用例编排：潮位录入、申请排班、窗口确认与航道释放。"""
from typing import Any, Dict, List

from .channel_repository import ChannelRepository
from .channel_rules import (
    ACTIVE_STATES,
    OccupiedWindow,
    find_window,
    parse_transit_request,
    require_booking_transition,
)
from .domain import Actor, Conflict, NotFound, PermissionDenied, ValidationError
from .tides import TideTable, build_tide_table, valid_date

CHANNEL_ROLES = {"port_controller", "admin"}
ACTION_SUMMARIES = {"confirm": "调度员已确认通航窗口", "release": "航道已释放"}


class ChannelService:
    def __init__(self, repository: ChannelRepository) -> None:
        self.repository = repository

    @staticmethod
    def _actor(actor: Actor) -> Actor:
        if actor is None or not actor.user_id.strip() or not actor.role.strip():
            raise PermissionDenied("缺少调用身份")
        if actor.role not in CHANNEL_ROLES:
            raise PermissionDenied("角色无权访问潮汐通航台")
        return actor

    # 潮位资料
    def save_tide(self, actor: Actor, payload: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        tide = build_tide_table(payload)
        return self.repository.save_tide(tide, actor.user_id)

    def get_tide(self, actor: Actor, date: str) -> Dict[str, Any]:
        self._actor(actor)
        tide = self.repository.get_tide(valid_date(date))
        if tide is None:
            raise NotFound("该日期尚无潮位资料")
        tide["depths"] = [round(tide["channel_depth_m"] + height, 2) for height in tide["heights"]]
        return tide

    # 通航申请与排班
    def request_transit(self, actor: Actor, payload: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        request = parse_transit_request(payload)
        tide_row = self.repository.get_tide(request.date)
        if tide_row is None:
            raise ValidationError("该日期尚无潮位资料，请先录入潮位")
        tide = TideTable(date=tide_row["date"], heights=tide_row["heights"], channel_depth_m=tide_row["channel_depth_m"])
        occupied = [
            OccupiedWindow(int(item["start_hour"]), int(item["end_hour"]), bool(item["dangerous_class"]))
            for item in self.repository.occupied_windows(request.date)
        ]
        result = find_window(request, tide, occupied)
        if result.scheduled:
            return self.repository.create_booking(request, "scheduled", result.start_hour, "", actor.user_id)
        return self.repository.create_booking(request, "waiting", None, result.reason, actor.user_id)

    def act(self, actor: Actor, booking_id: int, expected_version: int, action: str) -> Dict[str, Any]:
        actor = self._actor(actor)
        booking = self.repository.get_booking(booking_id)
        new_state = require_booking_transition(booking["state"], action)
        return self.repository.mutate_booking(
            booking_id,
            int(expected_version),
            new_state,
            actor.user_id,
            action,
            {"summary": ACTION_SUMMARIES[action], "from": booking["state"], "to": new_state},
        )

    def release_by_reference(self, reference: str, actor_id: str) -> None:
        """船舶离港时释放航道（内部调用，无申请则忽略）。"""
        booking = self.repository.find_active_by_reference(reference, ACTIVE_STATES)
        if booking is None:
            return
        self.repository.mutate_booking(
            booking["id"], booking["version"], "released", actor_id, "release", {"summary": "船舶离港，航道已释放"}
        )

    def require_confirmed_window(self, reference: str) -> None:
        """靠泊前置检查：必须有调度员确认过的通航窗口。"""
        booking = self.repository.find_active_by_reference(reference, ACTIVE_STATES + ("waiting",))
        if booking is None:
            raise Conflict("靠泊前需先申请通航窗口")
        if booking["state"] == "waiting":
            raise Conflict("船舶仍在候泊名单：%s" % booking["wait_reason"])
        if booking["state"] != "confirmed":
            raise Conflict("通航窗口尚未经调度员确认，不能靠泊")

    # 通航台与候泊名单
    def board(self, actor: Actor, date: str) -> Dict[str, Any]:
        self._actor(actor)
        date = valid_date(date)
        tide = self.repository.get_tide(date)
        if tide is not None:
            tide["depths"] = [round(tide["channel_depth_m"] + height, 2) for height in tide["heights"]]
        bookings = self.repository.list_bookings(date=date)
        return {
            "date": date,
            "tide": tide,
            "bookings": [item for item in bookings if item["state"] != "waiting"],
            "waiting": [item for item in bookings if item["state"] == "waiting"],
        }

    def waiting(self, actor: Actor) -> List[Dict[str, Any]]:
        self._actor(actor)
        return self.repository.list_bookings(state="waiting")

    def get_booking(self, actor: Actor, booking_id: int) -> Dict[str, Any]:
        self._actor(actor)
        booking = self.repository.get_booking(booking_id)
        booking["events"] = self.repository.events(booking_id)
        return booking
