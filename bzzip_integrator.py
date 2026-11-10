"""bzzip 整合层 - 将所有模块串起来

核心流程：
- 压缩流程：文件选择 → 蜜蜂检测 → 异常触发 → 内容简化 → 压缩 → 记录历史 → 对话响应
- 解压流程：文件选择 → 解压 → 记录历史 → 对话响应
- 测试流程：压缩包测试 → 记录结果

依赖注入设计：
- 所有模块通过构造函数传入，便于测试和替换
- 配置对象控制各功能模块的启用/禁用
- GUI 通过回调函数接收状态更新

特点：
- 所有操作在本地执行，不联网
- 异常效果在沙箱内，不碰真实系统文件
- 可逐步启用各功能模块
- 对话系统输出到GUI状态栏
"""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from accumulation_tracker import AccumulationTracker
from anomaly_trigger import AnomalyTrigger, AnomalyResult
from bee_detector import BeeDetector, BeeScanResult
from compress_core import ArchiveFormat, ArchiveResult, compress, extract, test_archive
from dialogue_system import DialogueSystem
from history_db import HistoryRecord, HistoryDB
from simplify_file import simplify_file
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

class ConfigDict(TypedDict):
    """配置字典"""
    anomaly_enabled: bool          # 异常注入总开关
    bee_detection_enabled: bool     # 蜜蜂检测开关
    simplify_enabled: bool         # 简化层开关
    accumulation_enabled: bool     # 累积层开关
    dialogue_enabled: bool         # 对话系统开关
    sound_enabled: bool            # 音效开关
    sound_path: str               # 音效文件路径
    empty_threshold: int          # 空文件阈值，默认10
    corrupt_probability: float     # 损坏概率，默认0.5
    temp_dir: Optional[str]       # 临时目录路径，None则自动创建


# ---------------------------------------------------------------------------
# 结果对象
# ---------------------------------------------------------------------------

@dataclass
class CompressionResult:
    """压缩结果"""
    success: bool
    error: Optional[str] = None
    archive_path: Optional[str] = None
    compressed_size: int = 0
    original_size: int = 0
    compression_ratio: float = 0.0
    processing_time: float = 0.0
    anomaly_triggered: bool = False
    files_processed: int = 0
    files_skipped: int = 0
    messages: List[str] = None
    
    def __post_init__(self):
        if self.messages is None:
            self.messages = []


@dataclass
class ExtractionResult:
    """解压结果"""
    success: bool
    error: Optional[str] = None
    extracted_files: List[str] = None
    processing_time: float = 0.0
    messages: List[str] = None
    
    def __post_init__(self):
        if self.extracted_files is None:
            self.extracted_files = []
        if self.messages is None:
            self.messages = []


@dataclass
class TestResult:
    """测试结果"""
    success: bool
    error: Optional[str] = None
    is_valid: bool = False
    processing_time: float = 0.0
    messages: List[str] = None
    
    def __post_init__(self):
        if self.messages is None:
            self.messages = []


# ---------------------------------------------------------------------------
# 整合器
# ---------------------------------------------------------------------------

class BzzipIntegrator:
    """bzzip 整合器
    
    将所有功能模块串起来，提供统一的接口。
    """
    
    def __init__(
        self,
        config: ConfigDict,
        history_manager: Optional[HistoryDB] = None,
        bee_detector: Optional[BeeDetector] = None,
        anomaly_trigger: Optional[AnomalyTrigger] = None,
        accumulation_tracker: Optional[AccumulationTracker] = None,
        dialogue_system: Optional[DialogueSystem] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ):
        """初始化整合器
        
        Args:
            config: 配置字典
            history_manager: 历史记录管理器
            bee_detector: 蜜蜂检测器
            anomaly_trigger: 异常触发器
            accumulation_tracker: 累积追踪器
            dialogue_system: 对话系统
            status_callback: 状态更新回调
            progress_callback: 进度更新回调
        """
        self.config = config
        self.status_callback = status_callback
        self.progress_callback = progress_callback
        
        # 先创建沙箱目录（数据库 / 异常日志 / 临时文件都放在这里），
        # 使用独立子目录，cleanup 只删除自己创建的部分，不碰用户目录。
        configured_dir = config.get("temp_dir")
        if configured_dir:
            base = Path(configured_dir)
            self.temp_dir = base / f"bzzip_sandbox_{os.getpid()}_{time.strftime('%H%M%S')}"
        else:
            self.temp_dir = Path(tempfile.mkdtemp(prefix="bzzip_sandbox_"))
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        # 初始化历史记录管理器（依赖沙箱目录，必须在目录创建之后）
        if history_manager is None:
            db_path = str(self.temp_dir / "bzzip_history.db")
            self.history_manager = HistoryDB(db_path)
        else:
            self.history_manager = history_manager
        
        # 初始化功能模块
        self.bee_detector = bee_detector or BeeDetector()
        self.anomaly_trigger = anomaly_trigger or AnomalyTrigger(
            corruption_strength=3,
            garbage_length=config.get("garbage_length", 4096) if config.get("garbage_length") else 4096,
            dialogue_count=config.get("dialogue_count", 3) if config.get("dialogue_count") else 3,
            log_dir=str(self.temp_dir)
        )
        self.accumulation_tracker = accumulation_tracker or AccumulationTracker()
        self.dialogue_system = dialogue_system or DialogueSystem(self.anomaly_trigger)
        
        # 空文件阈值
        self.empty_threshold = config.get("empty_threshold", 10)
        
        self._update_status("整合器初始化完成")
    
    def _update_status(self, message: str) -> None:
        """更新状态"""
        if self.status_callback:
            self.status_callback(message)
    
    def _update_progress(self, current: int, total: int) -> None:
        """更新进度"""
        if self.progress_callback:
            self.progress_callback(current, total)
    
    def _add_message(self, messages: List[str], message: str) -> None:
        """添加消息到消息列表"""
        messages.append(message)
        self._update_status(message)
    
    # ---------------------------------------------------------------------------
    # 压缩流程
    # ---------------------------------------------------------------------------
    
    def compress_files(
        self,
        files: List[str],
        output_path: str,
        archive_format: ArchiveFormat = ArchiveFormat.ZIP,
        continue_on_anomaly: bool = True,
    ) -> CompressionResult:
        """压缩文件
        
        Args:
            files: 要压缩的文件列表
            output_path: 输出压缩包路径
            archive_format: 压缩格式
            continue_on_anomaly: 检测到异常时是否继续压缩
            
        Returns:
            CompressionResult
        """
        start_time = time.time()
        result = CompressionResult(
            success=False,
            original_size=0,
            compressed_size=0,
            files_processed=0,
            files_skipped=0
        )
        
        try:
            self._update_status(f"开始压缩 {len(files)} 个文件")
            self._update_progress(0, len(files))
            
            # 1. 蜜蜂检测（压缩前执行；状态栏文本保持中性，不提示真实机制）
            bee_hits = []
            if self.config.get("bee_detection_enabled", True):
                self._update_status("正在预检内容...")
                for i, file_path in enumerate(files):
                    self._update_progress(i, len(files))
                    try:
                        scan_result = self.bee_detector.scan_file(file_path)
                        if scan_result.hit:
                            bee_hits.append(file_path)
                            self._add_message(
                                result.messages,
                                f"检测到蜜蜂内容: {file_path} (匹配: {scan_result.matched_terms})"
                            )
                    except Exception as e:
                        self._add_message(
                            result.messages,
                            f"检测文件 {file_path} 时出错: {str(e)}"
                        )
                        result.files_skipped += 1
                        continue
            
            # 2. 异常触发（仅对命中的文件）
            if bee_hits and self.config.get("anomaly_enabled", True):
                self._update_status("正在处理异常效果...")
                for file_path in bee_hits:
                    try:
                        with open(file_path, 'rb') as f:
                            content = f.read()
                        
                        anomaly_result = self.anomaly_trigger.trigger(
                            file_path,
                            content=content,
                            reason=f"bee content detected in {file_path}",
                            seed=int(time.time())
                        )
                        
                        if anomaly_result.has_anomaly:
                            result.anomaly_triggered = True
                            self._add_message(
                                result.messages,
                                f"已触发异常效果: {file_path}"
                            )
                            
                            if not continue_on_anomaly:
                                self._add_message(
                                    result.messages,
                                    "检测到异常，停止压缩"
                                )
                                return result
                    except Exception as e:
                        self._add_message(
                            result.messages,
                            f"处理异常时出错: {str(e)}"
                        )
            
            # 3. 内容处理（简化/累积）— 无论蜜蜂检测开关如何都执行
            self._update_progress(0, len(files))
            if self.config.get("simplify_enabled", True) or self.config.get("accumulation_enabled", True):
                self._update_status("正在处理文件内容...")
                temp_files = []
                
                for i, file_path in enumerate(files):
                    self._update_progress(i, len(files))
                    
                    try:
                        # 读取原始内容
                        with open(file_path, 'rb') as f:
                            original_content = f.read()
                        
                        # 应用累积效果
                        if self.config.get("accumulation_enabled", True):
                            file_hash = self.accumulation_tracker._get_hash(original_content)
                            level = self.accumulation_tracker.increment_level(file_hash)
                            
                            if level >= self.empty_threshold:
                                # 达到阈值，创建空文件
                                processed_content = b""
                            else:
                                # 应用简化
                                if self.config.get("simplify_enabled", True):
                                    processed_content = simplify_file(file_path, level)
                                else:
                                    processed_content = original_content
                        else:
                            # 不使用累积层，直接简化
                            if self.config.get("simplify_enabled", True):
                                processed_content = simplify_file(file_path, 1)
                            else:
                                processed_content = original_content
                        
                        # 创建临时文件（独立子目录 + 原始文件名，
                        # 保证归档内的成员名不带 temp_ 前缀）
                        temp_file = self.temp_dir / f"job_{i}" / os.path.basename(file_path)
                        temp_file.parent.mkdir(parents=True, exist_ok=True)
                        with open(temp_file, 'wb') as f:
                            f.write(processed_content)
                        
                        temp_files.append((file_path, temp_file))
                        result.files_processed += 1
                        result.original_size += len(original_content)
                        
                    except Exception as e:
                        self._add_message(
                            result.messages,
                            f"处理文件 {file_path} 时出错: {str(e)}"
                        )
                        result.files_skipped += 1
                        continue
            else:
                # 不使用简化层，直接使用原始文件
                temp_files = [(f, Path(f)) for f in files]
                for file_path in files:
                    try:
                        with open(file_path, 'rb') as f:
                            result.original_size += len(f.read())
                        result.files_processed += 1
                    except Exception:
                        result.files_skipped += 1
                        continue
            
            # 4. 执行压缩
            self._update_status("正在压缩文件...")
            archive_result = compress(
                [path for _, path in temp_files],
                Path(output_path),
                archive_format
            )
            
            if archive_result.success:
                result.success = True
                result.compressed_size = archive_result.compressed_size
                result.compression_ratio = archive_result.compression_ratio
                result.archive_path = output_path
                
                self._add_message(
                    result.messages,
                    f"压缩成功: {archive_result.file_count} 个文件, "
                    f"压缩率: {result.compression_ratio:.2%}"
                )
            else:
                result.error = archive_result.error
                self._add_message(
                    result.messages,
                    f"压缩失败: {archive_result.error}"
                )
            
            # 5. 清理临时文件（只删沙箱内生成的，不碰用户原始文件）
            for _, temp_file in temp_files:
                try:
                    if temp_file.resolve().is_relative_to(self.temp_dir.resolve()):
                        temp_file.unlink()
                except Exception:
                    pass
            
            # 6. 记录历史
            processing_time = time.time() - start_time
            result.processing_time = processing_time
            
            self.history_manager.add_record(
                op_type="compress",
                source=", ".join(files),
                target=output_path,
                original_size=result.original_size,
                compressed_size=result.compressed_size,
                compression_ratio=result.compression_ratio,
                status="success" if result.success else f"failed: {result.error}"
            )
            
            # 7. 对话响应
            if self.config.get("dialogue_enabled", True):
                dialogue_message = self._generate_compression_dialogue(result)
                self._add_message(result.messages, dialogue_message)
            
            self._update_status(f"压缩完成，耗时 {processing_time:.2f} 秒")
            
        except Exception as e:
            result.error = str(e)
            self._add_message(result.messages, f"压缩过程中发生错误: {str(e)}")
            
            # 记录失败历史
            self.history_manager.add_record(
                op_type="compress",
                source=", ".join(files),
                target=output_path,
                original_size=result.original_size,
                compressed_size=result.compressed_size,
                compression_ratio=result.compression_ratio,
                status=f"failed: {str(e)}"
            )
        
        return result
    
    # ---------------------------------------------------------------------------
    # 解压流程
    # ---------------------------------------------------------------------------
    
    def extract_archive(
        self,
        archive_path: str,
        output_dir: Optional[str] = None,
    ) -> ExtractionResult:
        """解压压缩包
        
        Args:
            archive_path: 压缩包路径
            output_dir: 输出目录，None则自动创建
            
        Returns:
            ExtractionResult
        """
        start_time = time.time()
        result = ExtractionResult(success=False)
        
        try:
            self._update_status(f"开始解压: {archive_path}")
            
            # 创建输出目录
            if output_dir is None:
                output_dir = self.temp_dir / "extracted"
                output_dir.mkdir(exist_ok=True)
            else:
                output_dir = Path(output_dir)
                output_dir.mkdir(exist_ok=True)
            
            # 执行解压
            extract_result = extract(Path(archive_path), output_dir)
            
            if extract_result.success:
                result.success = True
                result.extracted_files = extract_result.files
                result.processing_time = time.time() - start_time
                
                self._add_message(
                    result.messages,
                    f"解压成功: {len(extract_result.files)} 个文件"
                )
                
                # 记录历史
                self.history_manager.add_record(
                    op_type="extract",
                    source=archive_path,
                    target=str(output_dir),
                    original_size=extract_result.original_size,
                    compressed_size=extract_result.compressed_size,
                    compression_ratio=extract_result.compression_ratio,
                    status="success"
                )
                
                # 对话响应
                if self.config.get("dialogue_enabled", True):
                    dialogue_message = self._generate_extraction_dialogue(result)
                    self._add_message(result.messages, dialogue_message)
                
            else:
                result.error = extract_result.error
                self._add_message(
                    result.messages,
                    f"解压失败: {extract_result.error}"
                )
                
                # 记录失败历史
                self.history_manager.add_record(
                    op_type="extract",
                    source=archive_path,
                    target=str(output_dir),
                    status=f"failed: {extract_result.error}"
                )
            
            self._update_status(f"解压完成，耗时 {result.processing_time:.2f} 秒")
            
        except Exception as e:
            result.error = str(e)
            self._add_message(result.messages, f"解压过程中发生错误: {str(e)}")
            
            # 记录失败历史
            history_record = HistoryRecord(
                op_type="extract",
                source=archive_path,
                target=str(output_dir) if output_dir else "",
                status=f"failed: {str(e)}"
            )
            self.history_manager.add_record(history_record)
        
        return result
    
    # ---------------------------------------------------------------------------
    # 测试流程
    # ---------------------------------------------------------------------------
    
    def test_archive(self, archive_path: str) -> TestResult:
        """测试压缩包完整性
        
        Args:
            archive_path: 压缩包路径
            
        Returns:
            TestResult
        """
        start_time = time.time()
        result = TestResult(success=False)
        
        try:
            self._update_status(f"开始测试压缩包: {archive_path}")
            
            # 执行测试
            test_result = test_archive(Path(archive_path))
            
            if test_result.success:
                result.success = True
                result.is_valid = True
                result.processing_time = time.time() - start_time
                
                self._add_message(
                    result.messages,
                    f"压缩包测试通过: {test_result.file_count} 个文件"
                )
                
                # 记录历史
                self.history_manager.add_record(
                    op_type="test",
                    source=archive_path,
                    target="",
                    status="success"
                )
                
            else:
                result.error = test_result.error
                result.is_valid = False
                self._add_message(
                    result.messages,
                    f"压缩包测试失败: {test_result.error}"
                )
                
                # 记录失败历史
                self.history_manager.add_record(
                    op_type="test",
                    source=archive_path,
                    target="",
                    status=f"failed: {test_result.error}"
                )
            
            self._update_status(f"测试完成，耗时 {result.processing_time:.2f} 秒")
            
        except Exception as e:
            result.error = str(e)
            result.is_valid = False
            self._add_message(result.messages, f"测试过程中发生错误: {str(e)}")
            
            # 记录失败历史
            history_record = HistoryRecord(
                op_type="test",
                source=archive_path,
                target="",
                status=f"failed: {str(e)}"
            )
            self.history_manager.add_record(history_record)
        
        return result
    
    # ---------------------------------------------------------------------------
    # 历史记录
    # ---------------------------------------------------------------------------
    
    def get_history(
        self,
        limit: int = 50,
        op_type: Optional[str] = None
    ) -> List[HistoryRecord]:
        """获取历史记录
        
        Args:
            limit: 最大记录数
            op_type: 操作类型过滤
            
        Returns:
            历史记录列表
        """
        return self.history_manager.query_records(limit=limit, op_type=op_type)
    
    def clear_history(self) -> None:
        """清空历史记录"""
        self.history_manager.clear_history()
        self._update_status("历史记录已清空")
    
    # ---------------------------------------------------------------------------
    # 对话生成
    # ---------------------------------------------------------------------------
    
    def _generate_compression_dialogue(self, result: CompressionResult) -> str:
        """生成压缩过程的对话"""
        if result.success:
            if result.anomaly_triggered:
                return "bzzip: 压缩完成，但检测到蜜蜂内容并触发了异常效果。"
            else:
                return "bzzip: 压缩完成！文件已安全保存。"
        else:
            return "bzzip: 压缩失败，请检查错误信息。"
    
    def _generate_extraction_dialogue(self, result: ExtractionResult) -> str:
        """生成解压过程的对话"""
        if result.success:
            return "bzzip: 解压完成！文件已恢复。"
        else:
            return "bzzip: 解压失败，请检查错误信息。"
    
    # ---------------------------------------------------------------------------
    # 清理
    # ---------------------------------------------------------------------------
    
    def cleanup(self) -> None:
        """清理资源"""
        try:
            # 关闭数据库连接
            if hasattr(self, 'history_manager'):
                self.history_manager.close()
        except Exception:
            pass
        
        # 只删除沙箱子目录，不碰用户配置的 temp_dir 本身
        try:
            if hasattr(self, 'temp_dir') and self.temp_dir.exists():
                import shutil
                shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass
        
        if hasattr(self, 'dialogue_system'):
            self.dialogue_system.clear_conversation()
    
    def __del__(self):
        """析构时清理资源"""
        self.cleanup()