"""压缩次数累积模块 - 跟踪文件被压缩的次数，模拟内容逐渐丢失"""

import hashlib
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from simplify_file import simplify_file


class AccumulationTracker:
    """跟踪文件压缩次数的累积器（线程安全）"""

    def __init__(self, db_path: Optional[str] = None):
        """
        初始化累积器
        
        Args:
            db_path: SQLite数据库路径，None时使用内存模式
        """
        self.db_path = db_path
        self._conn = None
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self):
        """初始化数据库连接和表结构"""
        if self.db_path is None:
            # 内存模式
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            # 文件模式
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        
        # 创建表（如果不存在）
        cursor = self._conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accumulation (
                file_hash TEXT PRIMARY KEY,
                level INTEGER DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.commit()

    def _get_hash(self, content: bytes) -> str:
        """计算内容的SHA256哈希（取前16位十六进制）"""
        sha256 = hashlib.sha256(content)
        return sha256.hexdigest()[:16]

    def get_level(self, file_hash: str) -> int:
        """获取文件的当前压缩次数，不存在返回0
        
        Args:
            file_hash: 文件哈希
            
        Returns:
            压缩次数，不存在返回0
        """
        if file_hash is None:
            file_hash = "None"
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT level FROM accumulation WHERE file_hash = ?",
                (file_hash,)
            )
            result = cursor.fetchone()
            return result[0] if result else 0

    def increment_level(self, file_hash: str) -> int:
        """增加压缩次数
        
        Args:
            file_hash: 文件哈希
            
        Returns:
            新的压缩次数
        """
        with self._lock:
            current_level = self.get_level(file_hash)
            new_level = current_level + 1
            
            cursor = self._conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO accumulation 
                (file_hash, level, last_updated) 
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (file_hash, new_level)
            )
            self._conn.commit()
            return new_level

    def reset_level(self, file_hash: str) -> None:
        """重置压缩次数为0
        
        Args:
            file_hash: 文件哈希
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "DELETE FROM accumulation WHERE file_hash = ?",
                (file_hash,)
            )
            self._conn.commit()

    def get_threshold(self) -> int:
        """获取空文件阈值"""
        return 10

    def close(self):
        """关闭数据库连接"""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None


def apply_accumulation(path: str, content: bytes, tracker: AccumulationTracker) -> bytes:
    """
    应用压缩累积效果
    
    Args:
        path: 文件路径
        content: 文件内容
        tracker: 累积器实例
        
    Returns:
        简化后的内容（可能为空）
    """
    # 1. 计算哈希
    file_hash = tracker._get_hash(content)
    
    # 2. 增加压缩次数
    level = tracker.increment_level(file_hash)
    
    # 3. 检查阈值
    if level >= tracker.get_threshold():
        return b""
    
    # 4. 根据level进行简化
    if level == 1:
        # level 1: 去除空行
        try:
            text = content.decode("utf-8", errors="replace")
            text = "\n".join(line for line in text.split("\n") if line.strip())
            return text.encode("utf-8", errors="replace")
        except UnicodeDecodeError:
            # 非文本文件，使用字节兜底
            return simplify_bytes(content, level)
    
    elif level == 2:
        # level 2: 去除标点
        try:
            text = content.decode("utf-8", errors="replace")
            # 简单去除常见标点
            import string
            translator = str.maketrans("", "", string.punctuation)
            text = text.translate(translator)
            return text.encode("utf-8", errors="replace")
        except UnicodeDecodeError:
            return simplify_bytes(content, level)
    
    else:
        # level >= 3: 使用简化函数
        return simplify_file(path, level)