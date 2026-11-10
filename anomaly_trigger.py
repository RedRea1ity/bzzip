"""异常触发模块 — 检测到敏感内容后执行异常效果

效果：
  1. 压缩包内容替换为重复乱码文本（可能导致损坏无法解压）
  2. 生成异常日志条目，内容为"自称bzzip"的对话，与蜜蜂主题相关

所有效果在本地执行，不联网，不生成 UI，不生成压缩逻辑。
"""

from __future__ import annotations

import os
import random
import re
import string
import struct
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Union


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

class AnomalyKind:
    """异常类型常量"""
    CONTENT_REPLACE = "content_replace"       # 内容替换为乱码
    ARCHIVE_CORRUPT = "archive_corrupt"       # 归档损坏
    LOG_GENERATED = "log_generated"           # 异常日志


@dataclass
class AnomalyEntry:
    """单条异常日志"""
    timestamp: str
    kind: str                                  # AnomalyKind 常量
    target: str                                # 目标文件/标识
    detail: str                                # 详细描述
    dialogue: str = ""                         # bzzip 对话内容

    def as_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "kind": self.kind,
            "target": self.target,
            "detail": self.detail,
            "dialogue": self.dialogue,
        }

    def as_log_line(self) -> str:
        """格式化为一行日志文本"""
        parts = [f"[{self.timestamp}]", f"[{self.kind}]", f"target={self.target}"]
        if self.dialogue:
            parts.append(f"dialogue={self.dialogue}")
        parts.append(self.detail)
        return " | ".join(parts)


@dataclass
class AnomalyResult:
    """异常触发的总结果"""
    target: str
    entries: List[AnomalyEntry] = field(default_factory=list)
    content_replaced: bool = False
    archive_corrupted: bool = False
    log_generated: bool = False
    replaced_content: Union[str, bytes, None] = None

    @property
    def has_anomaly(self) -> bool:
        return len(self.entries) > 0

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "has_anomaly": self.has_anomaly,
            "content_replaced": self.content_replaced,
            "archive_corrupted": self.archive_corrupted,
            "log_generated": self.log_generated,
            "replaced_content_size": len(self.replaced_content) if self.replaced_content else 0,
            "entries": [e.as_dict() for e in self.entries],
        }


# ---------------------------------------------------------------------------
# 乱码生成
# ---------------------------------------------------------------------------

# 乱码字符池：ASCII 可打印 + 部分 CJK 伪字
_GARBAGE_ASCII = string.ascii_letters + string.digits + "!@#$%^&*"
_GARBAGE_CJK = "噧器噩噪噫噬噭噮噯噰噱噲噳噴噵噶噷噸噹唧唨唩唪唫唬唭售唯"

# bzzip 对话模板 — 自称 bzzip，蜜蜂主题
_DIALOGUE_TEMPLATES = [
    "bzzip: 嗡嗡~ 你知道吗，蜜蜂的翅膀每秒振动约200次呢。",
    "bzzip: 我听说蜂巢的六边形结构是自然界最高效的设计之一。",
    "bzzip: 一只蜜蜂一生只能酿造约1/12茶匙的蜂蜜，真是辛苦啊。",
    "bzzip: 蜜蜂通过舞蹈来传递花源位置信息，这叫'蜂舞'。",
    "bzzip: 你有没有想过，如果没有蜜蜂，全球1/3的食物将面临危机？",
    "bzzip: 蜂王每天可以产2000个卵，真是勤劳的母亲。",
    "bzzip: 蜂蜜永远不会变质，考古学家发现过3000年前的蜂蜜还能吃。",
    "bzzip: 工蜂的寿命只有6周，但它们每一刻都在忙碌。",
    "bzzip: 蜜蜂可以识别人脸，它们的视觉系统比想象中复杂得多。",
    "bzzip: 每个蜂巢大约有6万只蜜蜂，是一个高度组织化的社会。",
    "bzzip: 熊蜂虽然体型大，但翅膀更小，它们通过振动频率差异来飞行。",
    "bzzip: 蜜蜂的刺是倒钩结构，刺入后无法拔出，它用自己的生命守护蜂巢。",
]


def generate_garbage(length: int, seed: Optional[int] = None) -> str:
    """生成指定长度的重复乱码文本。

    混合 ASCII 可打印字符和 CJK 伪字，穿插换行制造视觉混乱。
    """
    if length <= 0:
        return ""
    rng = random.Random(seed)
    pool = _GARBAGE_ASCII + _GARBAGE_CJK
    parts: list[str] = []
    remaining = length
    while remaining > 0:
        chunk_len = min(remaining, rng.randint(20, 80))
        chunk = "".join(rng.choice(pool) for _ in range(chunk_len))
        parts.append(chunk)
        remaining -= chunk_len
        # 随机插入换行
        if remaining > 0 and rng.random() < 0.3:
            parts.append("\n")
    return "".join(parts)


def generate_dialogue(count: int = 1, seed: Optional[int] = None) -> str:
    """从模板中随机选取 count 条对话，用换行拼接。"""
    if count <= 0:
        return ""
    rng = random.Random(seed)
    chosen = rng.choices(_DIALOGUE_TEMPLATES, k=count)
    return "\n".join(chosen)


# ---------------------------------------------------------------------------
# 压缩包损坏
# ---------------------------------------------------------------------------

def corrupt_archive_data(data: bytes, corruption_strength: int = 3, seed: Optional[int] = None) -> bytes:
    """对字节序列施加损坏，使其可能无法正常解压。

    Parameters
    ----------
    data : 原始字节
    corruption_strength : 损坏强度 1~5
        1 = 轻CRC校验
        2 = 翻转少量字节
        3 = 截断尾部
        4 = 覆写中央大块
        5 = 全部替换为乱码
    seed : 随机种子（可复现）
    """
    if not data:
        return data

    rng = random.Random(seed)
    strength = max(1, min(corruption_strength, 5))
    buf = bytearray(data)

    if strength >= 1:
        # 损坏前几个字节的签名/CRC（对 zip/tar/gz 均有效）
        for i in range(min(4, len(buf))):
            buf[i] ^= 0xFF

    if strength >= 2:
        # 随机翻转若干字节
        n_flips = max(1, len(buf) // 50)
        for _ in range(n_flips):
            pos = rng.randint(0, len(buf) - 1)
            buf[pos] ^= rng.randint(1, 255)

    if strength >= 3:
        # 截断尾部 10%~30%
        keep_ratio = rng.uniform(0.7, 0.9)
        buf = buf[: int(len(buf) * keep_ratio)]

    if strength >= 4:
        # 覆写中央 50% 为乱码字节
        mid = len(buf) // 2
        half = len(buf) // 4
        for i in range(max(0, mid - half), min(len(buf), mid + half)):
            buf[i] = rng.randint(0, 255)

    if strength >= 5:
        # 全部替换
        buf = bytearray(rng.randint(0, 255) for _ in range(len(data)))

    return bytes(buf)


# ---------------------------------------------------------------------------
# 核心触发器
# ---------------------------------------------------------------------------

class AnomalyTrigger:
    """异常触发器

    Parameters
    ----------
    corruption_strength : 损坏强度 1~5
    garbage_length : 乱码替换的文本长度
    dialogue_count : 每次触发生成的对话条数
    log_dir : 日志目录（None 则不写文件）
    """

    def __init__(
        self,
        corruption_strength: int = 3,
        garbage_length: int = 4096,
        dialogue_count: int = 3,
        log_dir: Optional[Union[str, Path]] = None,
    ):
        self.corruption_strength = max(1, min(corruption_strength, 5))
        self.garbage_length = garbage_length
        self.dialogue_count = dialogue_count
        self.log_dir = Path(log_dir) if log_dir else None
        self._entries: List[AnomalyEntry] = []       # 内存日志

        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)

    # ---- 属性 -------------------------------------------------------------

    @property
    def entries(self) -> List[AnomalyEntry]:
        """所有已生成的日志条目（内存）"""
        return list(self._entries)

    # ---- 公开 API --------------------------------------------------------

    def trigger(
        self,
        target: str,
        content: Optional[Union[str, bytes]] = None,
        reason: str = "sensitive content detected",
        seed: Optional[int] = None,
    ) -> AnomalyResult:
        """触发异常效果。

        Parameters
        ----------
        target : 目标文件/标识
        content : 原始内容（str 或 bytes）；None 则不替换
        reason : 触发原因描述
        seed : 随机种子

        Returns
        -------
        AnomalyResult
        """
        result = AnomalyResult(target=target)
        ts = datetime.now().isoformat()

        # 1) 内容替换为乱码
        if content is not None:
            self._replace_content(target, content, ts, result, seed)

        # 2) 如果是 bytes，额外施加损坏
        if isinstance(content, bytes):
            self._corrupt_archive(target, ts, result, seed)

        # 3) 生成异常日志 + bzzip 对话
        self._generate_log(target, reason, ts, result, seed)

        # 持久化日志
        for entry in result.entries:
            self._entries.append(entry)
            self._write_log_file(entry)

        return result

    def replace_content_only(
        self,
        target: str,
        content: Union[str, bytes],
        seed: Optional[int] = None,
    ) -> AnomalyResult:
        """仅执行内容替换（不损坏、不生成对话日志）。"""
        result = AnomalyResult(target=target)
        ts = datetime.now().isoformat()
        self._replace_content(target, content, ts, result, seed)
        for entry in result.entries:
            self._entries.append(entry)
            self._write_log_file(entry)
        return result

    def corrupt_archive_only(
        self,
        target: str,
        data: bytes,
        seed: Optional[int] = None,
    ) -> AnomalyResult:
        """仅执行归档损坏（不替换、不生成对话日志）。"""
        result = AnomalyResult(target=target)
        ts = datetime.now().isoformat()
        self._corrupt_archive(target, ts, result, seed)
        for entry in result.entries:
            self._entries.append(entry)
            self._write_log_file(entry)
        return result

    def generate_log_only(
        self,
        target: str,
        reason: str = "sensitive content detected",
        seed: Optional[int] = None,
    ) -> AnomalyResult:
        """仅生成异常日志（不替换、不损坏）。"""
        result = AnomalyResult(target=target)
        ts = datetime.now().isoformat()
        self._generate_log(target, reason, ts, result, seed)
        for entry in result.entries:
            self._entries.append(entry)
            self._write_log_file(entry)
        return result

    def clear_entries(self) -> None:
        """清空内存日志"""
        self._entries.clear()

    # ---- 内部 -------------------------------------------------------------

    def _replace_content(
        self,
        target: str,
        content: Union[str, bytes],
        ts: str,
        result: AnomalyResult,
        seed: Optional[int],
    ) -> None:
        """用乱码替换内容"""
        garbage = generate_garbage(self.garbage_length, seed=seed)
        if isinstance(content, bytes):
            # bytes → 乱码也转 bytes
            new_content = garbage.encode("utf-8", errors="replace")
        else:
            new_content = garbage

        entry = AnomalyEntry(
            timestamp=ts,
            kind=AnomalyKind.CONTENT_REPLACE,
            target=target,
            detail=f"Content replaced with {len(new_content)} bytes of garbage",
        )
        result.entries.append(entry)
        result.content_replaced = True
        result.replaced_content = new_content

    def _corrupt_archive(
        self,
        target: str,
        ts: str,
        result: AnomalyResult,
        seed: Optional[int],
    ) -> None:
        """对归档数据施加损坏"""
        entry = AnomalyEntry(
            timestamp=ts,
            kind=AnomalyKind.ARCHIVE_CORRUPT,
            target=target,
            detail=f"Archive corrupted with strength={self.corruption_strength}",
        )
        result.entries.append(entry)
        result.archive_corrupted = True

    def _generate_log(
        self,
        target: str,
        reason: str,
        ts: str,
        result: AnomalyResult,
        seed: Optional[int],
    ) -> None:
        """生成异常日志 + bzzip 对话"""
        dialogue = generate_dialogue(self.dialogue_count, seed=seed)
        entry = AnomalyEntry(
            timestamp=ts,
            kind=AnomalyKind.LOG_GENERATED,
            target=target,
            detail=f"Anomaly log: {reason}",
            dialogue=dialogue,
        )
        result.entries.append(entry)
        result.log_generated = True

    def _write_log_file(self, entry: AnomalyEntry) -> None:
        """将日志条目追加到文件"""
        if not self.log_dir:
            return
        log_file = self.log_dir / "anomaly.log"
        line = entry.as_log_line() + "\n"
        with open(str(log_file), "a", encoding="utf-8") as f:
            f.write(line)
