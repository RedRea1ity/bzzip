"""SQLite 历史记录模块 — 压缩工具操作日志

仅依赖 Python 标准库 (sqlite3, pathlib, datetime)。
不生成 UI 代码。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence, Union


# ---------------------------------------------------------------------------
# 数据行
# ---------------------------------------------------------------------------

class HistoryRecord:
    """一条操作历史记录"""
    __slots__ = (
        "id", "op_type", "source", "target",
        "timestamp", "original_size", "compressed_size",
        "compression_ratio", "status",
    )

    def __init__(
        self,
        id: Optional[int] = None,
        op_type: str = "",
        source: str = "",
        target: str = "",
        timestamp: Optional[str] = None,
        original_size: int = 0,
        compressed_size: int = 0,
        compression_ratio: float = 0.0,
        status: str = "",
    ):
        self.id = id
        self.op_type = op_type
        self.source = source
        self.target = target
        self.timestamp = timestamp or datetime.now().isoformat()
        self.original_size = original_size
        self.compressed_size = compressed_size
        self.compression_ratio = compression_ratio
        self.status = status

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "op_type": self.op_type,
            "source": self.source,
            "target": self.target,
            "timestamp": self.timestamp,
            "original_size": self.original_size,
            "compressed_size": self.compressed_size,
            "compression_ratio": self.compression_ratio,
            "status": self.status,
        }

    def __repr__(self) -> str:
        return (
            f"HistoryRecord(id={self.id}, op={self.op_type!r}, "
            f"src={self.source!r}, status={self.status!r}, "
            f"ratio={self.compression_ratio:.2%})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HistoryRecord):
            return NotImplemented
        return self.as_dict() == other.as_dict()


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    op_type         TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    target          TEXT    NOT NULL DEFAULT '',
    timestamp       TEXT    NOT NULL,
    original_size   INTEGER NOT NULL DEFAULT 0,
    compressed_size INTEGER NOT NULL DEFAULT 0,
    compression_ratio REAL  NOT NULL DEFAULT 0.0,
    status          TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_history_op_type    ON history (op_type);
CREATE INDEX IF NOT EXISTS idx_history_timestamp  ON history (timestamp);
CREATE INDEX IF NOT EXISTS idx_history_status     ON history (status);
"""


class HistoryDB:
    """SQLite 历史记录数据库（线程安全）

    连接允许跨线程使用（GUI 工作线程会调用 add_record），
    所有操作通过互斥锁串行化。
    """

    def __init__(self, db_path: Union[str, Path] = ":memory:"):
        """
        Parameters
        ----------
        db_path : 数据库文件路径，默认 ':memory:' 内存库
        """
        self._path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    # ---- 关闭 / 上下文管理 ------------------------------------------------

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None          # type: ignore[assignment]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- add_record -------------------------------------------------------

    def add_record(
        self,
        op_type: str,
        source: str,
        target: str = "",
        original_size: int = 0,
        compressed_size: int = 0,
        compression_ratio: float = 0.0,
        status: str = "success",
        timestamp: Optional[str] = None,
    ) -> int:
        """插入一条历史记录，返回自增 id。

        Parameters
        ----------
        op_type         : 操作类型 (compress / extract / test / …)
        source          : 源文件/目录
        target          : 目标文件/目录
        original_size   : 原始字节数
        compressed_size : 压缩后字节数
        compression_ratio : 压缩率 0.0~1.0
        status          : 状态 (success / failed / …)
        timestamp       : ISO 时间戳，默认 now
        """
        if timestamp is None:
            timestamp = datetime.now().isoformat()

        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO history
                    (op_type, source, target, timestamp,
                     original_size, compressed_size, compression_ratio, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    op_type, source, target, timestamp,
                    original_size, compressed_size, compression_ratio, status,
                ),
            )
            self._conn.commit()
            return cur.lastrowid        # type: ignore[return-value]

    # ---- query_records ----------------------------------------------------

    def query_records(
        self,
        op_type: Optional[str] = None,
        source: Optional[str] = None,
        target: Optional[str] = None,
        status: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[HistoryRecord]:
        """按条件查询历史记录，返回列表（按时间倒序）。

        Parameters
        ----------
        op_type : 精确匹配操作类型
        source  : LIKE 模糊匹配源路径
        target  : LIKE 模糊匹配目标路径
        status  : 精确匹配状态
        since   : 起始时间 (ISO, >=)
        until   : 截止时间 (ISO, <=)
        limit   : 返回条数上限
        offset  : 偏移量
        """
        clauses: List[str] = []
        params: List[Any] = []

        if op_type is not None:
            clauses.append("op_type = ?")
            params.append(op_type)
        if source is not None:
            clauses.append("source LIKE ?")
            params.append(f"%{source}%")
        if target is not None:
            clauses.append("target LIKE ?")
            params.append(f"%{target}%")
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        sql = f"""
            SELECT id, op_type, source, target, timestamp,
                   original_size, compressed_size, compression_ratio, status
            FROM history
            {where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    # ---- clear_history ----------------------------------------------------

    def clear_history(
        self,
        op_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        """清空历史记录。

        Parameters
        ----------
        op_type : 仅删除指定操作类型；None 表示全部
        status  : 仅删除指定状态；None 表示全部

        Returns
        -------
        删除的行数
        """
        clauses: List[str] = []
        params: List[Any] = []

        if op_type is not None:
            clauses.append("op_type = ?")
            params.append(op_type)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        with self._lock:
            cur = self._conn.execute(f"DELETE FROM history {where}", params)
            self._conn.commit()
            return cur.rowcount

    # ---- 统计 --------------------------------------------------------------

    def count_records(
        self,
        op_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        """返回符合条件的记录总数。"""
        clauses: List[str] = []
        params: List[Any] = []

        if op_type is not None:
            clauses.append("op_type = ?")
            params.append(op_type)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) AS cnt FROM history {where}", params
            ).fetchone()
        return row["cnt"]              # type: ignore[index]

    # ---- 内部 --------------------------------------------------------------

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> HistoryRecord:
        return HistoryRecord(
            id=row["id"],
            op_type=row["op_type"],
            source=row["source"],
            target=row["target"],
            timestamp=row["timestamp"],
            original_size=row["original_size"],
            compressed_size=row["compressed_size"],
            compression_ratio=row["compression_ratio"],
            status=row["status"],
        )
