"""潮位资料：逐小时潮位、航道水深计算与录入校验。"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List

from .domain import ValidationError, number

HOURS_PER_DAY = 24
DEFAULT_CHANNEL_DEPTH_M = 12.0
MAX_TIDE_HEIGHT_M = 10.0


def valid_date(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("date不能为空")
    value = value.strip()
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValidationError("date必须是YYYY-MM-DD格式") from exc
    return value


def valid_heights(value: Any) -> List[float]:
    if not isinstance(value, list) or len(value) != HOURS_PER_DAY:
        raise ValidationError("heights必须是0-23时共24个潮位数值")
    heights = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValidationError("潮位必须是数字")
        item = float(item)
        if abs(item) > MAX_TIDE_HEIGHT_M:
            raise ValidationError("潮位超出合理范围")
        heights.append(round(item, 2))
    return heights


@dataclass(frozen=True)
class TideTable:
    """某日期的逐小时潮位与航道基准水深。"""

    date: str
    heights: List[float]
    channel_depth_m: float = DEFAULT_CHANNEL_DEPTH_M

    def depth_at(self, hour: int) -> float:
        """某小时的航道可用水深（基准水深+潮位）。"""
        if not 0 <= int(hour) < HOURS_PER_DAY:
            raise ValidationError("小时必须在0-23之间")
        return round(self.channel_depth_m + self.heights[int(hour)], 2)

    def min_depth_between(self, start_hour: int, end_hour: int) -> float:
        """时段内最小可用水深。"""
        return min(self.depth_at(hour) for hour in range(start_hour, end_hour))

    def hourly_depths(self) -> List[float]:
        return [self.depth_at(hour) for hour in range(HOURS_PER_DAY)]


def build_tide_table(payload: Dict[str, Any]) -> TideTable:
    data = dict(payload or {})
    date = valid_date(data.get("date"))
    heights = valid_heights(data.get("heights"))
    depth = number({"channel_depth_m": data.get("channel_depth_m", DEFAULT_CHANNEL_DEPTH_M)}, "channel_depth_m", 1, 30)
    return TideTable(date=date, heights=heights, channel_depth_m=round(depth, 2))
