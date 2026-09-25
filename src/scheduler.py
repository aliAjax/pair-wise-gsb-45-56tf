"""航道排班判断：单船通行、危险品隔离、最近可走窗口与候泊原因。"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .tides import TideTable, UKC_REQUIRED_M, navigable_hours


TRANSIT_HOURS = 1           # 单次过闸占用航道的整点时长
DANGEROUS_BUFFER_HOURS = 1  # 危险品船前后各留的小时数


@dataclass(frozen=True)
class ChannelBooking:
    """一条已占用航道的过闸窗口。"""

    record_id: int
    reference: str
    vessel: str
    state: str
    start_hour: int
    end_hour: int
    dangerous: bool

    @property
    def blocked_start(self) -> int:
        return self.start_hour - (DANGEROUS_BUFFER_HOURS if self.dangerous else 0)

    @property
    def blocked_end(self) -> int:
        return self.end_hour + (DANGEROUS_BUFFER_HOURS if self.dangerous else 0)


@dataclass(frozen=True)
class ScheduleDecision:
    """排班结果：排定窗口（scheduled）或进入候泊名单（waiting）。"""

    status: str  # "scheduled" 或 "waiting"
    requested_hour: int
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    depth_m: Optional[float] = None
    adjusted: bool = False
    reason: str = ""


def booking_from_record(record: Dict[str, Any]) -> ChannelBooking:
    payload = record["payload"]
    return ChannelBooking(
        record_id=int(record["id"]),
        reference=str(record["reference"]),
        vessel=str(payload.get("vessel", "")),
        state=str(record["state"]),
        start_hour=int(payload["window_start_hour"]),
        end_hour=int(payload["window_end_hour"]),
        dangerous=bool(payload.get("dangerous_goods")),
    )


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and a_end > b_start


class ChannelScheduler:
    """在给定潮位资料下为船舶寻找当天可走的过闸窗口。"""

    def __init__(self, tide_table: TideTable) -> None:
        self.tide_table = tide_table

    def required_depth(self, draft_m: float) -> float:
        return round(float(draft_m) + UKC_REQUIRED_M, 2)

    def _blocked_by(self, start_hour: int, dangerous: bool, bookings: Sequence[ChannelBooking]) -> List[ChannelBooking]:
        block_start = start_hour - (DANGEROUS_BUFFER_HOURS if dangerous else 0)
        block_end = start_hour + TRANSIT_HOURS + (DANGEROUS_BUFFER_HOURS if dangerous else 0)
        return [b for b in bookings if _overlaps(block_start, block_end, b.blocked_start, b.blocked_end)]

    def decide(self, *, requested_hour: int, draft_m: float, dangerous: bool, bookings: Sequence[ChannelBooking]) -> ScheduleDecision:
        required = self.required_depth(draft_m)
        hours = navigable_hours(self.tide_table, required)
        if not hours:
            max_depth = max(self.tide_table.depth_at(h) for h in range(24))
            return ScheduleDecision(
                status="waiting",
                requested_hour=requested_hour,
                reason="当日航道最大水深%.1fm，低于所需%.1fm，水深不足" % (max_depth, required),
            )
        if requested_hour in hours and not self._blocked_by(requested_hour, dangerous, bookings):
            return ScheduleDecision(
                status="scheduled",
                requested_hour=requested_hour,
                start_hour=requested_hour,
                end_hour=requested_hour + TRANSIT_HOURS,
                depth_m=self.tide_table.depth_at(requested_hour),
            )
        cause = "水深不足" if requested_hour not in hours else "与在航计划冲突"
        blockers: List[str] = []
        for hour in sorted(hours, key=lambda h: (abs(h - requested_hour), h)):
            hits = self._blocked_by(hour, dangerous, bookings)
            if not hits:
                return ScheduleDecision(
                    status="scheduled",
                    requested_hour=requested_hour,
                    start_hour=hour,
                    end_hour=hour + TRANSIT_HOURS,
                    depth_m=self.tide_table.depth_at(hour),
                    adjusted=True,
                    reason="申请%d时%s，已调整至当天最近可走窗口" % (requested_hour, cause),
                )
            for booking in hits:
                if booking.reference not in blockers:
                    blockers.append(booking.reference)
        return ScheduleDecision(
            status="waiting",
            requested_hour=requested_hour,
            reason="当日可走窗口均被在航计划（%s）占用，航道排满" % "、".join(blockers),
        )
