"""潮位资料：当日逐时潮位、航道水深与可走小时计算。"""
import math
from dataclasses import dataclass
from datetime import date as _date
from typing import List, Optional, Tuple


CHANNEL_BASE_DEPTH_M = 9.5   # 航道基准水深（米，海图水深）
UKC_REQUIRED_M = 0.5         # 过闸富余水深要求（米）
HOURS = tuple(range(24))

# 内置标准半日潮：高潮05:00/17:00，低潮11:00/23:00
_MEAN_TIDE_M = 2.1
_TIDE_RANGE_M = 1.7
_HIGH_TIDE_HOUR = 5


@dataclass(frozen=True)
class TideTable:
    """某日逐时潮位（米，潮高基准面以上）。"""

    date: str
    heights: Tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.heights) != 24:
            raise ValueError("潮位表必须包含24个小时")

    def height_at(self, hour: int) -> float:
        return float(self.heights[hour % 24])

    def depth_at(self, hour: int) -> float:
        """该整点航道水深 = 基准水深 + 潮位。"""
        return round(CHANNEL_BASE_DEPTH_M + self.height_at(hour), 2)


def default_table(day: Optional[str] = None) -> TideTable:
    """未接入实测潮位时使用的当日标准半日潮。"""
    label = day or _date.today().isoformat()
    heights = tuple(
        round(_MEAN_TIDE_M + _TIDE_RANGE_M * math.cos((h - _HIGH_TIDE_HOUR) * math.pi / 6), 2)
        for h in HOURS
    )
    return TideTable(date=label, heights=heights)


def navigable_hours(table: TideTable, required_depth_m: float) -> List[int]:
    """航道水深满足所需水深的整点小时列表。"""
    return [h for h in HOURS if table.depth_at(h) >= required_depth_m]
