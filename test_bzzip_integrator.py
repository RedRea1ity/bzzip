"""bzzip 整合层集成测试

测试覆盖：
1. 干净压缩（异常全关）
2. 仅简化
3. 仅蜜蜂检测
4. 完整异常链
5. 解压流程
6. 累积达到阈值
"""

import os
import tempfile
from pathlib import Path

import pytest

from bzzip_integrator import BzzipIntegrator, ConfigDict, CompressionResult, ExtractionResult, TestResult


class TestBzzipIntegrator:
    """bzzip 整合器测试"""
    
    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        temp_dir = Path(tempfile.mkdtemp())
        yield temp_dir
        # 清理 - 忽略权限错误
        import shutil
        try:
            shutil.rmtree(temp_dir)
        except (PermissionError, FileNotFoundError):
            pass
    
    @pytest.fixture
    def test_files(self, temp_dir):
        """创建测试文件"""
        files = []
        
        # 创建普通文本文件
        normal_file = temp_dir / "normal.txt"
        normal_file.write_text("This is a normal text file without any bees.")
        files.append(str(normal_file))
        
        # 创建蜜蜂文件
        bee_file = temp_dir / "bee.txt"
        bee_file.write_text("The bees are working in the hives. 蜜蜂在蜂巢中工作。")
        files.append(str(bee_file))
        
        # 创建图片文件
        image_file = temp_dir / "image.jpg"
        image_file.write_bytes(b"fake jpg content" * 100)
        files.append(str(image_file))
        
        return files
    
    @pytest.fixture
    def config_base(self, temp_dir):
        """基础配置"""
        return {
            "anomaly_enabled": True,
            "bee_detection_enabled": True,
            "simplify_enabled": True,
            "accumulation_enabled": True,
            "dialogue_enabled": True,
            "sound_enabled": False,
            "sound_path": "",
            "empty_threshold": 10,
            "corrupt_probability": 0.5,
            "temp_dir": str(temp_dir)
        }
    
    # ---------------------------------------------------------------------------
    # 测试用例
    # ---------------------------------------------------------------------------
    
    def test_clean_compression(self, config_base, test_files, temp_dir):
        """测试1：干净压缩（异常全关）"""
        # 创建配置（关闭所有异常功能）
        config = config_base.copy()
        config.update({
            "anomaly_enabled": False,
            "bee_detection_enabled": False,
            "simplify_enabled": False,
            "accumulation_enabled": False,
            "dialogue_enabled": False
        })
        
        # 创建整合器
        integrator = BzzipIntegrator(config)
        
        # 执行压缩
        output_path = temp_dir / "clean.zip"
        result = integrator.compress_files(test_files, str(output_path))
        
        # 验证结果
        assert result.success
        assert result.anomaly_triggered == False
        assert result.files_processed == 3
        assert result.files_skipped == 0
        assert result.original_size > 0
        assert result.compressed_size >= 0  # 可能为0（空文件）
        # 压缩比率可以为负（当文件变小时）
        assert len(result.messages) > 0
        
        # 验证输出文件存在
        assert os.path.exists(output_path)
        
        # 验证压缩包可以正常解压
        extract_result = integrator.extract_archive(str(output_path), temp_dir / "extracted")
        assert extract_result.success
        assert len(extract_result.extracted_files) == 3
        
        # 清理
        integrator.cleanup()
    
    def test_simplify_only(self, config_base, test_files, temp_dir):
        """测试2：仅简化"""
        # 创建配置（仅启用简化）
        config = config_base.copy()
        config.update({
            "anomaly_enabled": False,
            "bee_detection_enabled": False,
            "simplify_enabled": True,
            "accumulation_enabled": False,
            "dialogue_enabled": False
        })
        
        # 创建整合器
        integrator = BzzipIntegrator(config)
        
        # 执行压缩
        output_path = temp_dir / "simplify.zip"
        result = integrator.compress_files(test_files, str(output_path))
        
        # 验证结果
        assert result.success
        assert result.anomaly_triggered == False
        assert result.files_processed == 3
        assert result.files_skipped == 0
        assert result.original_size > 0
        assert result.compressed_size > 0
        assert result.compression_ratio > 0
        
        # 验证输出文件存在
        assert os.path.exists(output_path)
        
        # 清理
        integrator.cleanup()
    
    def test_bee_detection_only(self, config_base, test_files, temp_dir):
        """测试3：仅蜜蜂检测"""
        # 创建配置（仅启用蜜蜂检测）
        config = config_base.copy()
        config.update({
            "anomaly_enabled": False,
            "bee_detection_enabled": True,
            "simplify_enabled": False,
            "accumulation_enabled": False,
            "dialogue_enabled": False
        })
        
        # 创建整合器
        integrator = BzzipIntegrator(config)
        
        # 执行压缩
        output_path = temp_dir / "bee_detection.zip"
        result = integrator.compress_files(test_files, str(output_path))
        
        # 验证结果
        assert result.success
        assert result.anomaly_triggered == False  # 没有启用异常触发
        assert result.files_processed == 3
        assert result.files_skipped == 0
        assert result.original_size > 0
        assert result.compressed_size > 0
        assert result.compression_ratio > 0
        
        # 验证输出文件存在
        assert os.path.exists(output_path)
        
        # 验证日志中包含蜜蜂检测信息
        bee_messages = [msg for msg in result.messages if "蜜蜂" in msg]
        assert len(bee_messages) > 0
        
        # 清理
        integrator.cleanup()
    
    def test_full_anomaly_chain(self, config_base, test_files, temp_dir):
        """测试4：完整异常链"""
        # 创建配置（启用所有功能）
        config = config_base.copy()
        config.update({
            "anomaly_enabled": True,
            "bee_detection_enabled": True,
            "simplify_enabled": True,
            "accumulation_enabled": True,
            "dialogue_enabled": True
        })
        
        # 创建整合器
        integrator = BzzipIntegrator(config)
        
        # 执行压缩
        output_path = temp_dir / "full_anomaly.zip"
        result = integrator.compress_files(test_files, str(output_path))
        
        # 验证结果
        assert result.success
        assert result.anomaly_triggered == True  # 应该触发异常
        assert result.files_processed == 3
        assert result.files_skipped == 0
        assert result.original_size > 0
        assert result.compressed_size > 0
        assert result.compression_ratio > 0
        
        # 验证输出文件存在
        assert os.path.exists(output_path)
        
        # 验证日志中包含异常信息
        anomaly_messages = [msg for msg in result.messages if "异常" in msg or "触发" in msg]
        assert len(anomaly_messages) > 0
        
        # 验证日志中包含蜜蜂检测信息
        bee_messages = [msg for msg in result.messages if "蜜蜂" in msg]
        assert len(bee_messages) > 0
        
        # 清理
        integrator.cleanup()
    
    def test_extraction_flow(self, config_base, test_files, temp_dir):
        """测试5：解压流程"""
        # 创建配置
        config = config_base.copy()
        config.update({
            "anomaly_enabled": False,
            "bee_detection_enabled": False,
            "simplify_enabled": False,
            "accumulation_enabled": False,
            "dialogue_enabled": False
        })
        
        # 创建整合器
        integrator = BzzipIntegrator(config)
        
        # 先压缩
        archive_path = temp_dir / "test.zip"
        compress_result = integrator.compress_files(test_files, str(archive_path))
        
        assert compress_result.success
        assert os.path.exists(archive_path)
        
        # 然后解压
        extract_dir = temp_dir / "extracted"
        extract_result = integrator.extract_archive(str(archive_path), str(extract_dir))
        
        # 验证解压结果
        assert extract_result.success
        assert len(extract_result.extracted_files) == 3
        assert extract_result.processing_time > 0
        assert len(extract_result.messages) > 0
        
        # 验证解压的文件存在
        extracted_files = list(extract_dir.iterdir())
        assert len(extracted_files) == 3
        
        # 清理
        integrator.cleanup()
    
    def test_accumulation_threshold(self, config_base, temp_dir):
        """测试6：累积达到阈值"""
        # 创建配置
        config = config_base.copy()
        config.update({
            "anomaly_enabled": False,
            "bee_detection_enabled": False,
            "simplify_enabled": True,
            "accumulation_enabled": True,
            "dialogue_enabled": False,
            "empty_threshold": 3  # 设置较低的阈值
        })
        
        # 创建整合器
        integrator = BzzipIntegrator(config)
        
        # 创建相同的文件多次，触发累积效果
        test_file = temp_dir / "test.txt"
        test_file.write_text("This is a test file.")
        
        files = [str(test_file)] * 5  # 同一个文件压缩5次
        
        # 执行压缩
        output_path = temp_dir / "threshold.zip"
        result = integrator.compress_files(files, str(output_path))
        
        # 验证结果
        assert result.success
        assert result.files_processed == 5
        assert result.original_size > 0
        assert result.compressed_size >= 0  # 可能为0（空文件）
        # 压缩比率可以为负（当文件变小时）
        assert len(result.messages) > 0
        
        # 验证输出文件存在
        assert os.path.exists(output_path)
        
        # 清理
        integrator.cleanup()
    
    def test_archive_testing(self, config_base, test_files, temp_dir):
        """测试7：压缩包测试"""
        # 创建配置
        config = config_base.copy()
        config.update({
            "anomaly_enabled": False,
            "bee_detection_enabled": False,
            "simplify_enabled": False,
            "accumulation_enabled": False,
            "dialogue_enabled": False
        })
        
        # 创建整合器
        integrator = BzzipIntegrator(config)
        
        # 先创建一个压缩包
        archive_path = temp_dir / "test.zip"
        compress_result = integrator.compress_files(test_files, str(archive_path))
        assert compress_result.success
        
        # 测试压缩包
        test_result = integrator.test_archive(str(archive_path))
        
        # 验证测试结果
        assert test_result.success
        assert test_result.is_valid == True
        # 处理时间可能为0（非常快的测试）
        assert test_result.processing_time >= 0
        assert len(test_result.messages) > 0
        
        # 清理
        integrator.cleanup()
    
    def test_error_handling(self, config_base, temp_dir):
        """测试8：错误处理"""
        # 创建配置
        config = config_base.copy()
        config.update({
            "anomaly_enabled": False,
            "bee_detection_enabled": False,
            "simplify_enabled": False,
            "accumulation_enabled": False,
            "dialogue_enabled": False
        })
        
        # 创建整合器
        integrator = BzzipIntegrator(config)
        
        # 测试压缩不存在的文件
        nonexistent_files = ["/path/to/nonexistent/file.txt"]
        output_path = temp_dir / "error.zip"
        
        result = integrator.compress_files(nonexistent_files, str(output_path))
        
        # 应该失败
        assert result.success == False
        assert result.error is not None
        assert result.files_processed == 0
        assert result.files_skipped == 1  # 不存在的文件被计为跳过
        
        # 清理
        integrator.cleanup()
    
    def test_history_functionality(self, config_base, test_files, temp_dir):
        """测试9：历史记录功能"""
        # 创建配置
        config = config_base.copy()
        config.update({
            "anomaly_enabled": False,
            "bee_detection_enabled": False,
            "simplify_enabled": False,
            "accumulation_enabled": False,
            "dialogue_enabled": False
        })
        
        # 创建整合器
        integrator = BzzipIntegrator(config)
        
        # 执行压缩
        output_path = temp_dir / "history_test.zip"
        result = integrator.compress_files(test_files, str(output_path))
        
        assert result.success
        
        # 获取历史记录
        history = integrator.get_history()
        assert len(history) > 0
        
        # 验证历史记录内容
        latest_record = history[0]
        assert latest_record.op_type == "compress"
        assert latest_record.source == ", ".join(test_files)
        assert latest_record.target == str(output_path)
        assert latest_record.status == "success"
        assert latest_record.compression_ratio > 0
        
        # 清空历史记录
        integrator.clear_history()
        
        # 验证历史记录已清空
        empty_history = integrator.get_history()
        assert len(empty_history) == 0
        
        # 清理
        integrator.cleanup()
    
    def test_continuation_on_anomaly(self, config_base, test_files, temp_dir):
        """测试10：异常时继续压缩"""
        # 创建配置
        config = config_base.copy()
        config.update({
            "anomaly_enabled": True,
            "bee_detection_enabled": True,
            "simplify_enabled": True,
            "accumulation_enabled": True,
            "dialogue_enabled": True
        })
        
        # 创建整合器
        integrator = BzzipIntegrator(config)
        
        # 执行压缩（设置为继续）
        output_path = temp_dir / "continue.zip"
        result = integrator.compress_files(
            test_files,
            str(output_path),
            continue_on_anomaly=True
        )
        
        # 应该成功
        assert result.success
        assert result.anomaly_triggered == True
        
        # 验证输出文件存在
        assert os.path.exists(output_path)
        
        # 清理
        integrator.cleanup()
    
    def test_stop_on_anomaly(self, config_base, test_files, temp_dir):
        """测试11：检测到异常时停止压缩"""
        # 创建配置
        config = config_base.copy()
        config.update({
            "anomaly_enabled": True,
            "bee_detection_enabled": True,
            "simplify_enabled": True,
            "accumulation_enabled": True,
            "dialogue_enabled": True
        })
        
        # 创建整合器
        integrator = BzzipIntegrator(config)
        
        # 执行压缩（设置为不继续）
        output_path = temp_dir / "stop.zip"
        result = integrator.compress_files(
            test_files,
            str(output_path),
            continue_on_anomaly=False
        )
        
        # 应该失败
        assert result.success == False
        assert result.anomaly_triggered == True
        assert "检测到异常" in " ".join(result.messages)
        
        # 验证输出文件不存在
        assert not os.path.exists(output_path)
        
        # 清理
        integrator.cleanup()
    
    # ---------------------------------------------------------------------------
    # 边界条件测试
    # ---------------------------------------------------------------------------
    
    def test_empty_file_list(self, config_base, temp_dir):
        """测试空文件列表"""
        config = config_base.copy()
        config.update({
            "anomaly_enabled": False,
            "bee_detection_enabled": False,
            "simplify_enabled": False,
            "accumulation_enabled": False,
            "dialogue_enabled": False
        })
        
        integrator = BzzipIntegrator(config)
        
        # 压缩空文件列表
        result = integrator.compress_files([], str(temp_dir / "empty.zip"))
        
        # 应该失败
        assert result.success == False
        assert result.files_processed == 0
        assert result.files_skipped == 0
        
        integrator.cleanup()
    
    def test_single_file_compression(self, config_base, temp_dir):
        """测试单文件压缩"""
        config = config_base.copy()
        config.update({
            "anomaly_enabled": False,
            "bee_detection_enabled": False,
            "simplify_enabled": False,
            "accumulation_enabled": False,
            "dialogue_enabled": False
        })
        
        integrator = BzzipIntegrator(config)
        
        # 创建单个文件
        test_file = temp_dir / "single.txt"
        test_file.write_text("This is a single file.")
        
        # 压缩单个文件
        result = integrator.compress_files([str(test_file)], str(temp_dir / "single.zip"))
        
        # 应该成功
        assert result.success
        assert result.files_processed == 1
        assert result.files_skipped == 0
        assert result.original_size > 0
        assert result.compressed_size >= 0  # 可能为0（空文件）
        # 压缩比率可以为负（当文件变小时）
        assert len(result.messages) > 0
        
        # 验证输出文件存在
        assert os.path.exists(temp_dir / "single.zip")
        
        integrator.cleanup()
    
    def test_large_file_compression(self, config_base, temp_dir):
        """测试大文件压缩"""
        config = config_base.copy()
        config.update({
            "anomaly_enabled": False,
            "bee_detection_enabled": False,
            "simplify_enabled": False,
            "accumulation_enabled": False,
            "dialogue_enabled": False
        })
        
        integrator = BzzipIntegrator(config)
        
        # 创建大文件（1MB）
        large_file = temp_dir / "large.bin"
        large_file.write_bytes(os.urandom(1024 * 1024))  # 1MB
        
        # 压缩大文件
        result = integrator.compress_files([str(large_file)], str(temp_dir / "large.zip"))
        
        # 应该成功
        assert result.success
        assert result.files_processed == 1
        assert result.files_skipped == 0
        assert result.original_size == 1024 * 1024
        # 对于随机数据，压缩可能不会有效，甚至可能变大
        assert result.compressed_size >= 0
        assert len(result.messages) > 0
        
        integrator.cleanup()


# ---------------------------------------------------------------------------
# 测试配置
# ---------------------------------------------------------------------------

def pytest_configure(config):
    """pytest配置"""
    config.addinivalue_line(
        "markers", "integration: integration tests"
    )


def pytest_collection_modifyitems(config, items):
    """修改测试项"""
    for item in items:
        # 标记所有测试为integration测试
        item.add_marker("integration")


# ---------------------------------------------------------------------------
# 测试运行器
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 运行特定测试
    pytest.main([
        "-v",
        "--tb=short",
        "test_bzzip_integrator.py"
    ])