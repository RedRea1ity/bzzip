#!/usr/bin/env python3
"""bzzip.exe 全流程自动化测试

模拟真实用户使用场景：
1. 创建各种类型的测试文件
2. 干净压缩流程
3. 蜜蜂内容检测 + 异常触发流程
4. 累积压缩（多次压缩同一内容）
5. 解压流程
6. 压缩包完整性测试
7. 历史记录验证
"""

import sys
import shutil
from pathlib import Path

# 确保能导入项目模块
sys.path.insert(0, str(Path(__file__).parent.parent))

from bzzip_integrator import BzzipIntegrator, ArchiveFormat

WORKSPACE = Path(__file__).parent
SOURCE_DIR = WORKSPACE / "source"
OUTPUT_DIR = WORKSPACE / "output"
EXTRACT_DIR = WORKSPACE / "extracted"

PASSED = []
FAILED = []


def check(name: str, condition: bool, detail: str = ""):
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  [FAIL] {name} -- {detail}")


def make_base_config(**overrides):
    config = {
        "anomaly_enabled": True,
        "bee_detection_enabled": True,
        "simplify_enabled": True,
        "accumulation_enabled": True,
        "dialogue_enabled": True,
        "sound_enabled": False,
        "sound_path": "",
        "empty_threshold": 10,
        "corrupt_probability": 0.5,
        "temp_dir": str(WORKSPACE / ".tmp"),
    }
    config.update(overrides)
    return config


def create_test_files():
    """创建各类测试文件"""
    print("\n[1] 创建测试文件")
    print("-" * 50)

    # 普通中文文本
    normal_txt = SOURCE_DIR / "notes.txt"
    normal_txt.write_text(
        "会议纪要\n\n2026年9月30日，团队讨论了压缩工具的迭代计划。\n"
        "主要结论：\n1. 保留增量压缩功能\n2. 优化解压速度\n3. 完善日志记录\n",
        encoding="utf-8",
    )
    check("创建 notes.txt", normal_txt.exists())

    # 英文长文本（可压缩）
    readme = SOURCE_DIR / "readme.txt"
    readme.write_text("bzzip is a fun compression tool. " * 200, encoding="utf-8")
    check("创建 readme.txt", readme.exists())

    # 蜜蜂敏感内容（中英混合）
    bee_txt = SOURCE_DIR / "field_notes.txt"
    bee_txt.write_text(
        "Field observation:\nThe bees are dancing near the hive.\n"
        "蜂蜜产量比去年增加了15%，蜂群状态健康。\n"
        "The buzz of honey bees fills the meadow at dawn.\n",
        encoding="utf-8",
    )
    check("创建 field_notes.txt（含蜜蜂内容）", bee_txt.exists())

    # 文件名含蜜蜂关键词
    bee_named = SOURCE_DIR / "honey_bee_photo.txt"
    bee_named.write_text("This is just a photo description, no bees mentioned inside.", encoding="utf-8")
    check("创建 honey_bee_photo.txt（文件名命中）", bee_named.exists())

    # JSON 数据文件
    data_json = SOURCE_DIR / "config.json"
    data_json.write_text('{"name": "bzzip", "version": "1.0", "bees_protected": true}', encoding="utf-8")
    check("创建 config.json", data_json.exists())

    # 二进制文件
    binary = SOURCE_DIR / "data.bin"
    binary.write_bytes(bytes(range(256)) * 40)
    check("创建 data.bin", binary.exists())

    total = len(list(SOURCE_DIR.iterdir()))
    check(f"源目录共 {total} 个文件", total == 6, f"实际 {total}")


def test_clean_compression():
    """测试2：干净压缩（全功能关闭）"""
    print("\n[2] 干净压缩测试（异常全关）")
    print("-" * 50)

    integrator = BzzipIntegrator(make_base_config(
        anomaly_enabled=False,
        bee_detection_enabled=False,
        simplify_enabled=False,
        accumulation_enabled=False,
        dialogue_enabled=False,
    ))
    try:
        files = [str(SOURCE_DIR / "notes.txt"), str(SOURCE_DIR / "readme.txt")]
        out = str(OUTPUT_DIR / "clean.zip")
        result = integrator.compress_files(files, out, ArchiveFormat.ZIP, continue_on_anomaly=True)

        check("压缩成功", result.success, result.error or "")
        check("输出文件存在", Path(out).exists())
        check("未触发异常", not result.anomaly_triggered)
        check("处理2个文件", result.files_processed == 2, f"实际 {result.files_processed}")
        check("无跳过文件", result.files_skipped == 0, f"实际 {result.files_skipped}")

        # 验证解压后内容一致
        target = EXTRACT_DIR / "clean"
        er = integrator.extract_archive(out, str(target))
        check("解压成功", er.success, er.error or "")
        check("解压出2个文件", len(er.extracted_files) == 2, f"实际 {len(er.extracted_files)}")

        extracted_notes = target / "notes.txt"
        if extracted_notes.exists():
            original = (SOURCE_DIR / "notes.txt").read_text(encoding="utf-8")
            extracted = extracted_notes.read_text(encoding="utf-8")
            check("解压内容与原文件一致", original == extracted)
        else:
            check("解压内容与原文件一致", False, "notes.txt 未找到")
    finally:
        integrator.cleanup()


def test_bee_detection_and_anomaly():
    """测试3：蜜蜂检测 + 异常触发（完整异常链）"""
    print("\n[3] 蜜蜂检测 + 异常触发测试")
    print("-" * 50)

    integrator = BzzipIntegrator(make_base_config())
    try:
        # 内容命中
        files = [str(SOURCE_DIR / "field_notes.txt"), str(SOURCE_DIR / "notes.txt")]
        out = str(OUTPUT_DIR / "anomaly.zip")
        result = integrator.compress_files(files, out, ArchiveFormat.ZIP, continue_on_anomaly=True)

        check("压缩流程完成", result.success, result.error or "")
        check("触发异常效果", result.anomaly_triggered)

        bee_msgs = [m for m in result.messages if "蜜蜂" in m]
        check("日志报告蜜蜂检测", len(bee_msgs) > 0, f"消息: {result.messages}")

        # 文件名命中（文件名 honey_bee_photo.txt 应该被检出）
        out2 = str(OUTPUT_DIR / "anomaly_name.zip")
        result2 = integrator.compress_files(
            [str(SOURCE_DIR / "honey_bee_photo.txt"), str(SOURCE_DIR / "notes.txt")],
            out2, ArchiveFormat.ZIP, continue_on_anomaly=True)
        check("文件名命中也触发异常", result2.anomaly_triggered)

        # 蜜蜂检测开、异常注入关 → 不应触发异常
        integrator_off = BzzipIntegrator(make_base_config(anomaly_enabled=False))
        try:
            out3 = str(OUTPUT_DIR / "detect_only.zip")
            result3 = integrator_off.compress_files(
                [str(SOURCE_DIR / "field_notes.txt"), str(SOURCE_DIR / "notes.txt")],
                out3, ArchiveFormat.ZIP, continue_on_anomaly=True)
            check("仅检测不触发异常", result3.success and not result3.anomaly_triggered,
                  f"success={result3.success} anomaly={result3.anomaly_triggered}")
            check("检测日志仍然报告蜜蜂", any("蜜蜂" in m for m in result3.messages))
        finally:
            integrator_off.cleanup()

        # 检测到异常时停止压缩
        out4 = str(OUTPUT_DIR / "stopped.zip")
        result4 = integrator.compress_files(
            [str(SOURCE_DIR / "field_notes.txt"), str(SOURCE_DIR / "notes.txt")],
            out4, ArchiveFormat.ZIP, continue_on_anomaly=False)
        check("拒绝继续时压缩中止", not result4.success)
        check("中止后不产生压缩包", not Path(out4).exists())
    finally:
        integrator.cleanup()


def test_accumulation():
    """测试4：累积压缩（阈值触发清空 + 内容逐渐退化）"""
    print("\n[4] 累积压缩测试")
    print("-" * 50)

    integrator = BzzipIntegrator(make_base_config(
        anomaly_enabled=False,
        bee_detection_enabled=False,
        simplify_enabled=True,
        accumulation_enabled=True,
        dialogue_enabled=False,
        empty_threshold=6,
    ))
    try:
        src = SOURCE_DIR / "readme.txt"

        # --- 阶段A：同一内容反复压缩，达到阈值(6)后应清空 ---
        empty_at = None
        for i in range(1, 9):
            out = str(OUTPUT_DIR / f"thresh_{i}.zip")
            r = integrator.compress_files([str(src)], out, ArchiveFormat.ZIP, continue_on_anomaly=True)
            if not r.success:
                check(f"阶段A第{i}轮压缩成功", False, r.error or "")
                break

            target = EXTRACT_DIR / f"thresh_{i}"
            er = integrator.extract_archive(out, str(target))
            inner = target / src.name
            if not (er.success and inner.exists()):
                check(f"阶段A第{i}轮解压成功", False, er.error or f"{inner} 未找到")
                break

            size = inner.stat().st_size
            print(f"    阈值轮 {i}: 解出 {size} 字节")
            if size == 0 and empty_at is None:
                empty_at = i
                break

        check("同一内容压缩至阈值(6)后内容清空", empty_at == 6, f"清空轮次: {empty_at}")

        # --- 阶段B：反馈退化（每轮压缩上一轮产物，内容单调不增）---
        current = str(src)
        levels = []
        for i in range(1, 5):
            out = str(OUTPUT_DIR / f"degrade_{i}.zip")
            r = integrator.compress_files([current], out, ArchiveFormat.ZIP, continue_on_anomaly=True)
            if not r.success:
                check(f"阶段B第{i}轮压缩成功", False, r.error or "")
                break
            target = EXTRACT_DIR / f"degrade_{i}"
            er = integrator.extract_archive(out, str(target))
            inner = target / src.name
            if not (er.success and inner.exists()):
                check(f"阶段B第{i}轮解压成功", False, er.error or f"{inner} 未找到")
                break
            levels.append(inner.stat().st_size)
            check(f"阶段B第{i}轮压缩+解压成功", True)
            current = str(inner)

        print(f"    退化序列: {levels}")
        check("内容长度单调不增（退化棘轮）",
              all(levels[i] >= levels[i+1] for i in range(len(levels)-1)),
              f"长度序列: {levels}")
    finally:
        integrator.cleanup()


def test_simplify():
    """测试5：简化层单独工作"""
    print("\n[5] 简化层测试")
    print("-" * 50)

    integrator = BzzipIntegrator(make_base_config(
        anomaly_enabled=False,
        bee_detection_enabled=False,
        simplify_enabled=True,
        accumulation_enabled=False,
        dialogue_enabled=False,
    ))
    try:
        out = str(OUTPUT_DIR / "simplified.zip")
        r = integrator.compress_files(
            [str(SOURCE_DIR / "readme.txt"), str(SOURCE_DIR / "notes.txt")],
            out, ArchiveFormat.ZIP, continue_on_anomaly=True)
        check("简化压缩成功", r.success, r.error or "")
        check("压缩后体积存在", r.compressed_size >= 0)
    finally:
        integrator.cleanup()


def test_archive_test_and_history():
    """测试6：压缩包完整性测试 + 历史记录"""
    print("\n[6] 完整性测试 + 历史记录")
    print("-" * 50)

    integrator = BzzipIntegrator(make_base_config(
        anomaly_enabled=False, bee_detection_enabled=False,
        simplify_enabled=False, accumulation_enabled=False,
        dialogue_enabled=False,
    ))
    try:
        out = str(OUTPUT_DIR / "verify.zip")
        cr = integrator.compress_files([str(SOURCE_DIR / "notes.txt")], out, ArchiveFormat.ZIP)

        tr = integrator.test_archive(out)
        check("完整性测试通过", tr.success and tr.is_valid, tr.error or "")

        # 损坏测试：篡改压缩包字节后应当失败或无效
        bad = OUTPUT_DIR / "corrupted.zip"
        data = Path(out).read_bytes()
        bad.write_bytes(data[:len(data)//2])  # 截断一半
        tr_bad = integrator.test_archive(str(bad))
        check("损坏压缩包被识别", (not tr_bad.success) or (not tr_bad.is_valid),
              f"success={tr_bad.success} valid={tr_bad.is_valid}")

        history = integrator.get_history()
        check("历史记录存在", len(history) >= 3, f"实际 {len(history)} 条")
        op_types = {rec.op_type for rec in history}
        check("历史包含 compress", "compress" in op_types)
        check("历史包含 test", "test" in op_types)

        statuses = [rec.status for rec in history]
        check("有失败状态被记录", any("fail" in s.lower() for s in statuses),
              f"状态列表: {statuses}")

        # 清空历史
        integrator.clear_history()
        check("清空历史后为0条", len(integrator.get_history()) == 0)
    finally:
        integrator.cleanup()


def test_dialogue_and_sandbox():
    """测试7：对话系统 + 沙箱隔离"""
    print("\n[7] 对话系统 + 沙箱隔离")
    print("-" * 50)

    status_log = []
    integrator = BzzipIntegrator(make_base_config(
        dialogue_enabled=True,
        temp_dir=str(WORKSPACE / ".tmp"),
    ), status_callback=status_log.append)
    try:
        r = integrator.compress_files(
            [str(SOURCE_DIR / "field_notes.txt"), str(SOURCE_DIR / "notes.txt")],
            str(OUTPUT_DIR / "dialogue.zip"), ArchiveFormat.ZIP, continue_on_anomaly=True)

        check("压缩成功", r.success, r.error or "")
        check("状态回调被调用", len(status_log) > 0)
        bz_msgs = [m for m in r.messages if "bzzip" in m]
        check("对话消息进入日志", len(bz_msgs) > 0, f"消息: {r.messages}")

        # 沙箱验证：源文件不应被修改（异常效果只作用于沙箱/临时内容）
        original = (SOURCE_DIR / "field_notes.txt").read_text(encoding="utf-8")
        check("源文件未被异常效果破坏", "bees are dancing" in original)

        # 临时目录被隔离在 workspace 内
        check("临时目录在测试沙箱内", str(integrator.temp_dir).startswith(str(WORKSPACE)))
    finally:
        integrator.cleanup()


def test_gui_import():
    """测试8：GUI 模块可导入（不启动界面）"""
    print("\n[8] GUI 模块导入检查")
    print("-" * 50)
    try:
        import PySide6  # noqa: F401
        check("PySide6 已安装", True)
    except ImportError:
        check("PySide6 已安装", False, "未安装，GUI 无法启动，但核心功能不受影响")
        return

    try:
        import bzzip_gui  # noqa: F401
        check("bzzip_gui 可导入", True)
    except Exception as e:
        check("bzzip_gui 可导入", False, str(e))


def main():
    print("=" * 50)
    print("bzzip.exe 全流程自动化测试")
    print("=" * 50)

    # 清空工作区
    for d in (SOURCE_DIR, OUTPUT_DIR, EXTRACT_DIR, WORKSPACE / ".tmp"):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    create_test_files()
    test_clean_compression()
    test_bee_detection_and_anomaly()
    test_accumulation()
    test_simplify()
    test_archive_test_and_history()
    test_dialogue_and_sandbox()
    test_gui_import()

    # 清理中间临时目录
    tmp = WORKSPACE / ".tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 50)
    print(f"结果: {len(PASSED)} 通过, {len(FAILED)} 失败")
    if FAILED:
        print("\n失败项:")
        for f in FAILED:
            print(f"  - {f}")
    print("=" * 50)

    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
