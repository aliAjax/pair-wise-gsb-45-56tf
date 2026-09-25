"""潮汐资料与航道排班的SQLite持久化。"""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .domain import Conflict, NotFound
from .tides import TideTable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChannelRepository:
    """潮位表、通航申请与排班事件的存取。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tide_tables (
                    date TEXT PRIMARY KEY,
                    channel_depth_m REAL NOT NULL,
                    heights TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS channel_bookings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reference TEXT NOT NULL,
                    vessel TEXT NOT NULL,
                    draft_m REAL NOT NULL,
                    transit_date TEXT NOT NULL,
                    desired_hour INTEGER NOT NULL,
                    duration_hours INTEGER NOT NULL,
                    dangerous_class TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    start_hour INTEGER,
                    end_hour INTEGER,
                    wait_reason TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_active_reference
                    ON channel_bookings(reference) WHERE state IN ('scheduled','confirmed','waiting');
                CREATE INDEX IF NOT EXISTS idx_bookings_date ON channel_bookings(transit_date, state);
                CREATE TABLE IF NOT EXISTS channel_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    booking_id INTEGER NOT NULL REFERENCES channel_bookings(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    details TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_channel_events_booking ON channel_events(booking_id, id);
                """
            )

    # 潮位资料
    def save_tide(self, tide: TideTable, actor_id: str) -> Dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tide_tables(date,channel_depth_m,heights,updated_by,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(date) DO UPDATE SET channel_depth_m=excluded.channel_depth_m,
                    heights=excluded.heights, updated_by=excluded.updated_by, updated_at=excluded.updated_at
                """,
                (tide.date, tide.channel_depth_m, json.dumps(list(tide.heights)), actor_id, now),
            )
        return self.get_tide(tide.date)

    def get_tide(self, date: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tide_tables WHERE date=?", (date,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["heights"] = json.loads(item["heights"])
        return item

    # 通航申请与排班
    def create_booking(self, request: Any, state: str, start_hour: Optional[int], wait_reason: str, actor_id: str) -> Dict[str, Any]:
        now = _now()
        end_hour = start_hour + request.duration_hours if start_hour is not None else None
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO channel_bookings(reference,vessel,draft_m,transit_date,desired_hour,
                        duration_hours,dangerous_class,state,start_hour,end_hour,wait_reason,version,
                        created_by,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        request.reference, request.vessel, request.draft_m, request.date, request.desired_hour,
                        request.duration_hours, request.dangerous_class, state, start_hour, end_hour,
                        wait_reason, 1, actor_id, actor_id, now, now,
                    ),
                )
                booking_id = int(cursor.lastrowid)
                connection.execute(
                    "INSERT INTO channel_events(booking_id,action,actor_id,version,details,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        booking_id, "created", actor_id, 1,
                        json.dumps({"state": state, "start_hour": start_hour, "wait_reason": wait_reason}, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise Conflict("该航次已有进行中的通航申请") from exc
        return self.get_booking(booking_id)

    def get_booking(self, booking_id: int) -> Dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM channel_bookings WHERE id=?", (booking_id,)).fetchone()
        if row is None:
            raise NotFound("通航申请不存在")
        return dict(row)

    def list_bookings(self, date: Optional[str] = None, state: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM channel_bookings"
        clauses, params = [], []
        if date:
            clauses.append("transit_date=?")
            params.append(date)
        if state:
            clauses.append("state=?")
            params.append(state)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY transit_date, COALESCE(start_hour, 99), id"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def occupied_windows(self, date: str) -> List[Dict[str, Any]]:
        """当天已排班（待确认/已确认）的航道占用时段。"""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT start_hour,end_hour,dangerous_class FROM channel_bookings "
                "WHERE transit_date=? AND state IN ('scheduled','confirmed') AND start_hour IS NOT NULL",
                (date,),
            ).fetchall()
        return [dict(row) for row in rows]

    def find_active_by_reference(self, reference: str, states: Sequence[str]) -> Optional[Dict[str, Any]]:
        placeholders = ",".join("?" for _ in states)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM channel_bookings WHERE reference=? AND state IN (%s) ORDER BY id DESC LIMIT 1" % placeholders,
                (reference, *states),
            ).fetchone()
        return dict(row) if row is not None else None

    def mutate_booking(self, booking_id: int, expected_version: int, state: str, actor_id: str, action: str, details: Dict[str, Any]) -> Dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT version FROM channel_bookings WHERE id=?", (booking_id,)).fetchone()
            if row is None:
                connection.rollback()
                raise NotFound("通航申请不存在")
            if int(row["version"]) != int(expected_version):
                connection.rollback()
                raise Conflict("版本冲突，请刷新后重试")
            version = int(expected_version) + 1
            connection.execute(
                "UPDATE channel_bookings SET state=?,version=?,updated_by=?,updated_at=? WHERE id=?",
                (state, version, actor_id, now, booking_id),
            )
            connection.execute(
                "INSERT INTO channel_events(booking_id,action,actor_id,version,details,created_at) VALUES(?,?,?,?,?,?)",
                (booking_id, action, actor_id, version, json.dumps(details, ensure_ascii=False, sort_keys=True), now),
            )
            connection.commit()
        return self.get_booking(booking_id)

    def events(self, booking_id: int) -> List[Dict[str, Any]]:
        self.get_booking(booking_id)
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM channel_events WHERE booking_id=? ORDER BY id", (booking_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item["details"])
            result.append(item)
        return result
