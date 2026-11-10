"""GUI 冒烟测试：离屏实例化 7-Zip 风格窗口，验证核心交互路径

不弹出任何对话框：压缩/解压/测试通过窗口内部 _run_* 方法直连线程，
模拟真实点击后的执行链路（线程、回调、日志、状态栏、历史）。
"""
import os
import sys
import time
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"D:\bzzip")

from PySide6.QtWidgets import QApplication

import bzzip_gui
from bzzip_gui import BzzipMainWindow, SettingsDialog, HistoryDialog, load_config

# 离屏模式下模态对话框会永远阻塞：把所有弹窗打桩为自动应答并记录调用
_POPUPS = []
bzzip_gui.QMessageBox.warning = staticmethod(
    lambda *a, **k: _POPUPS.append(("warning", a)) or bzzip_gui.QMessageBox.StandardButton.Ok)
bzzip_gui.QMessageBox.information = staticmethod(
    lambda *a, **k: _POPUPS.append(("information", a)) or bzzip_gui.QMessageBox.StandardButton.Ok)
bzzip_gui.QMessageBox.question = staticmethod(
    lambda *a, **k: _POPUPS.append(("question", a)) or bzzip_gui.QMessageBox.StandardButton.Yes)
bzzip_gui.QMessageBox.about = staticmethod(lambda *a, **k: _POPUPS.append(("about", a)))

WS = Path(r"D:\bzzip\test_workspace")
SRC = WS / "source"
OUT = WS / "output"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


def wait_thread(app, th, timeout=30):
    if th is None:
        return
    t0 = time.time()
    while th.isRunning() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def main():
    app = QApplication(sys.argv)

    print("\n[1] 窗口构建")
    print("-" * 50)
    w = BzzipMainWindow()
    w.show()
    app.processEvents()

    check("窗口标题为 bzzip", w.windowTitle() == "bzzip", w.windowTitle())
    check("窗口图标已加载", not w.windowIcon().pixmap(64, 64).isNull())
    check("工具栏存在（添加/解压/测试）", all(a.isEnabled() for a in (w.act_add, w.act_extract, w.act_test)))
    check("菜单栏 5 个菜单", w.menuBar().actions().__len__() == 5,
          f"实际 {len(w.menuBar().actions())}")
    check("日志面板默认收起", not w.log_dock.isVisible())

    print("\n[2] 文件浏览")
    print("-" * 50)
    w.navigate_to(SRC)
    app.processEvents()
    check("源目录列出 6 个文件", w.file_table.rowCount() == 6, f"实际 {w.file_table.rowCount()}")
    check("地址栏已更新", w.path_edit.text() == str(SRC))
    check("对象计数", "6" in w.count_label.text(), w.count_label.text())

    # 目录树
    check("目录树有根节点", w.dir_tree.topLevelItemCount() > 0)

    # 双击进入目录 + 返回上级
    w.navigate_to(WS)
    app.processEvents()
    check("父目录列出子目录", w.file_table.rowCount() >= 3)
    w.go_up()
    app.processEvents()
    check("上级导航正常", w.path_edit.text() == str(WS.parent))

    print("\n[3] 压缩流程（走窗口线程链路）")
    print("-" * 50)
    w.navigate_to(SRC)
    app.processEvents()
    files = [str(SRC / "notes.txt"), str(SRC / "readme.txt")]
    out_zip = str(OUT / "gui_smoke.zip")
    w._run_compression(files, out_zip, "zip")
    wait_thread(app, w.compression_thread)

    if not Path(out_zip).exists():
        print("  [DEBUG] 日志内容：")
        for line in w.log_text.toPlainText().splitlines()[-8:]:
            print("    ", line)
    check("压缩包已生成", Path(out_zip).exists())
    log_text = w.log_text.toPlainText()
    check("日志包含压缩记录", "压缩" in log_text)
    check("状态栏显示完成", "压缩完成" in w.status_label.text(), w.status_label.text())
    check("进度条已隐藏", not w.progress_bar.isVisible())
    check("按钮恢复可用", w.act_add.isEnabled())

    # 蜜蜂内容压缩（触发对话输出 → 日志面板应弹出）
    w.log_dock.hide()
    bee_files = [str(SRC / "field_notes.txt"), str(SRC / "notes.txt")]
    out_bee = str(OUT / "gui_smoke_bee.zip")
    w._run_compression(bee_files, out_bee, "zip")
    wait_thread(app, w.compression_thread)
    check("蜜蜂文件压缩完成", Path(out_bee).exists())
    check("对话触发日志面板弹出", w.log_dock.isVisible())
    check("日志含 bzzip 对话", "bzzip:" in w.log_text.toPlainText())

    print("\n[4] 解压流程")
    print("-" * 50)
    target = WS / "extracted" / "gui_smoke"
    w._run_extraction(Path(out_zip), str(target))
    wait_thread(app, w.extraction_thread)
    check("解压出 2 个文件", (target / "notes.txt").exists() and (target / "readme.txt").exists())

    print("\n[5] 完整性测试")
    print("-" * 50)
    w._run_test(Path(out_zip))
    wait_thread(app, w.test_thread)
    check("测试通过", "测试通过" in w.status_label.text(), w.status_label.text())

    bad = OUT / "gui_bad.zip"
    data = Path(out_zip).read_bytes()
    bad.write_bytes(data[: len(data) // 2])
    w._run_test(bad)
    wait_thread(app, w.test_thread)
    check("损坏包测试失败", "测试失败" in w.status_label.text(), w.status_label.text())
    check("失败弹窗已出现（而非挂起）", any(p[0] == "warning" for p in _POPUPS))

    print("\n[6] 历史 + 对话框构建")
    print("-" * 50)
    records = w.integrator.get_history(limit=200)
    check("历史记录非空", len(records) >= 4, f"实际 {len(records)}")
    hd = HistoryDialog(records)
    check("历史对话框可构建", hd.windowTitle() == "bzzip 历史记录")
    hd.deleteLater()

    cfg2 = dict(load_config())
    sd = SettingsDialog(cfg2)
    new_cfg = sd.get_config()
    check("设置对话框字段回读一致",
          new_cfg.get("empty_threshold") == cfg2.get("empty_threshold")
          and new_cfg.get("anomaly_enabled") == cfg2.get("anomaly_enabled"))
    sd.deleteLater()

    print("\n[7] 配置持久化")
    print("-" * 50)
    from bzzip_gui import CONFIG_PATH
    w.close()
    check("关闭窗口时配置已写盘", CONFIG_PATH.exists())

    print("\n" + "=" * 50)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = [(n, d) for n, ok, d in results if not ok]
    print(f"结果: {passed} 通过, {len(failed)} 失败")
    for n, d in failed:
        print(f"  - {n}: {d}")
    print("=" * 50)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(Path(os.environ.get("TMP", "/tmp")) / "bzzip_sandbox*".replace("*", ""), ignore_errors=True)
