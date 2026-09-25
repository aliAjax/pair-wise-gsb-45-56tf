"""航道排班判断：单船通航、危险品隔离与当天最近可走窗口搜索。"""
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence

from .domain import Conflict, integer, number, optional_text, text
from .tides import HOURS_PER_DAY, TideTable, valid_date

SAFETY_MARGIN_M = 0.5
DANGEROUS_BUFFER_HOURS = 1
MAX_DURATION_HOURS = 6

BOOKING_TRANSITIONS = {
    "confirm": {"scheduled": "confirmed"},
    "release": {"scheduled": "released", "confirmed": "released"},
}
ACTIVE_STATES = ("scheduled", "confirmed")


@dataclass(frozen=True)
class TransitRequest:
    """调度员录入的过闸申请。"""

    reference: str
    vessel: str
    draft_m: float
    date: str
    desired_hour: int
    duration_hours: int
    dangerous_class: str

    @property
    def dangerous(self) -> bool:
        return bool(self.dangerous_class)


@dataclass(frozen=True)
class OccupiedWindow:
    """航道被占用的时段（dangerous为True时前后各隔离一小时）。"""

    start_hour: int
    end_hour: int
    dangerous: bool


@dataclass(frozen=True)
class ScheduleResult:
    """排班结果：start_hour为None表示进入候泊名单。"""

    start_hour: Optional[int]
    reason: str = ""

    @property
    def scheduled(self) -> bool:
        return self.start_hour is not None


def parse_transit_request(payload: Dict[str, Any]) -> TransitRequest:
    data = dict(payload or {})
    reference = text(data, "reference")
    vessel = text(data, "vessel")
    draft = number(data, "draft_m", 0.1, 30)
    date = valid_date(data.get("date"))
    desired = integer(data, "desired_hour", 0, 23)
    duration = 1
    if data.get("duration_hours") is not None:
        duration = integer(data, "duration_hours", 1, MAX_DURATION_HOURS)
    dangerous_class = optional_text(data, "dangerous_class", "")
    return TransitRequest(reference, vessel, round(draft, 2), date, desired, duration, dangerous_class)


def _buffered(window: OccupiedWindow) -> OccupiedWindow:
    """危险品船的占用时段向前后各扩一小时。"""
    if not window.dangerous:
        return window
    return OccupiedWindow(window.start_hour - DANGEROUS_BUFFER_HOURS, window.end_hour + DANGEROUS_BUFFER_HOURS, False)


def _overlaps(a: OccupiedWindow, b: OccupiedWindow) -> bool:
    return a.start_hour < b.end_hour and b.start_hour < a.end_hour


def depth_ok(tide: TideTable, draft_m: float, start_hour: int, duration_hours: int) -> bool:
    """时段内最小水深是否满足吃水安全余量。"""
    return tide.min_depth_between(start_hour, start_hour + duration_hours) - draft_m >= SAFETY_MARGIN_M


def channel_free(candidate: OccupiedWindow, occupied: Sequence[OccupiedWindow]) -> bool:
    """航道同一时间一艘船，危险品船前后各留一小时。"""
    expanded = _buffered(candidate)
    return all(not _overlaps(expanded, _buffered(other)) for other in occupied)


def _candidate_hours(desired_hour: int) -> Iterator[int]:
    """从预计时刻起按距离由近到远给出候选整点，同等距离优先顺延。"""
    yield desired_hour
    for distance in range(1, HOURS_PER_DAY + 1):
        yield desired_hour + distance
        yield desired_hour - distance


def find_window(request: TransitRequest, tide: TideTable, occupied: Sequence[OccupiedWindow]) -> ScheduleResult:
    """搜索当天最近的可走窗口；找不到时给出候泊原因。"""
    depth_possible = False
    for start in _candidate_hours(request.desired_hour):
        if start < 0 or start + request.duration_hours > HOURS_PER_DAY:
            continue
        if not depth_ok(tide, request.draft_m, start, request.duration_hours):
            continue
        depth_possible = True
        candidate = OccupiedWindow(start, start + request.duration_hours, request.dangerous)
        if channel_free(candidate, occupied):
            return ScheduleResult(start_hour=start)
    if depth_possible:
        return ScheduleResult(None, "当天航道档期与危险品隔离时段冲突，无可走窗口")
    return ScheduleResult(None, "全天潮位水深不足，船舶吃水无法安全通航")


def require_booking_transition(state: str, action: str) -> str:
    allowed = BOOKING_TRANSITIONS.get(action, {}).get(state)
    if allowed is None:
        raise Conflict("当前状态不允许执行%s" % action)
    return allowed
