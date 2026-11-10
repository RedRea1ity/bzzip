"""bzzip GUI — 7-Zip 风格文件管理器界面 (PySide6)

布局照搬 7-Zip 文件管理器：
- 菜单栏：文件 / 编辑 / 查看 / 工具 / 帮助
- 工具栏：添加（压缩） / 解压 / 测试 / 历史 / 设置（图标+文字，文字在图标下方）
- 地址栏：上级按钮 + 路径输入框 + 刷新
- 主区域：左侧目录树 + 右侧文件列表（名称 / 修改日期 / 大小 / 类型）
- 底部：bzzip 日志面板（对话输出）+ 状态栏（消息 / 进度条 / 对象计数）

窗口标题为 "bzzip"，图标为蜜蜂吉祥物+压缩包（bzzip.svg）。
所有操作在本地执行，不联网。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QDir, Qt, QThread, Signal
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QDockWidget,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSpinBox,
    QSplitter, QStyle, QTableWidget, QTableWidgetItem, QTextEdit, QToolBar,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from bzzip_integrator import BzzipIntegrator, ConfigDict

ICON_PATH = Path(__file__).parent / "bzzip.svg"
CONFIG_PATH = Path(__file__).parent / "bzzip_config.json"

ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".tar.gz", ".bz2"}

_EXT_TYPE = {
    ".txt": "文本文档", ".md": "Markdown", ".log": "日志", ".csv": "CSV",
    ".json": "JSON", ".xml": "XML", ".yaml": "YAML", ".yml": "YAML",
    ".py": "Python 脚本", ".js": "JavaScript", ".html": "HTML", ".css": "CSS",
    ".docx": "Word 文档", ".xlsx": "Excel 工作簿", ".pptx": "PPT 演示",
    ".pdf": "PDF 文档",
    ".jpg": "JPEG 图像", ".jpeg": "JPEG 图像", ".png": "PNG 图像",
    ".gif": "GIF 图像", ".bmp": "位图", ".webp": "WebP 图像", ".svg": "SVG 图像",
    ".mp3": "音频", ".wav": "音频", ".flac": "音频", ".ogg": "音频",
    ".mp4": "视频", ".avi": "视频", ".mkv": "视频", ".mov": "视频",
    ".zip": "ZIP 压缩包", ".tar": "TAR 压缩包", ".gz": "GZ 压缩包",
    ".tgz": "TGZ 压缩包", ".tar.gz": "TAR.GZ 压缩包", ".bz2": "BZ2 压缩包",
    ".exe": "应用程序", ".dll": "库文件", ".bin": "二进制文件",
}

DEFAULT_CONFIG: ConfigDict = {
    "anomaly_enabled": True,
    "bee_detection_enabled": True,
    "simplify_enabled": True,
    "accumulation_enabled": True,
    "dialogue_enabled": True,
    "sound_enabled": False,
    "sound_path": "",
    "empty_threshold": 10,
    "corrupt_probability": 0.5,
    "temp_dir": "",
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def app_icon() -> QIcon:
    """加载窗口图标（SVG 渲染为 pixmap，保证可用）"""
    if not ICON_PATH.exists():
        return QIcon()
    try:
        renderer = QSvgRenderer(str(ICON_PATH))
        if not renderer.isValid():
            return QIcon(str(ICON_PATH))
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        return QIcon(pm)
    except Exception:
        return QIcon(str(ICON_PATH))


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B" if n else ""
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} PB"


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def file_type(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".tar.gz"):
        return _EXT_TYPE[".tar.gz"]
    return _EXT_TYPE.get(Path(lower).suffix, "文件")


def is_archive(name: str) -> bool:
    lower = name.lower()
    if lower.endswith(".tar.gz"):
        return True
    return Path(lower).suffix in ARCHIVE_EXTS


def load_config() -> ConfigDict:
    config = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.exists():
            config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return config


def save_config(config: ConfigDict) -> None:
    try:
        CONFIG_PATH.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 工作线程
# ---------------------------------------------------------------------------

class CompressionThread(QThread):
    """压缩线程"""
    result_ready = Signal(object)

    def __init__(self, integrator, files, output_path, fmt_key, continue_on_anomaly):
        super().__init__()
        self.integrator = integrator
        self.files = files
        self.output_path = output_path
        self.fmt_key = fmt_key
        self.continue_on_anomaly = continue_on_anomaly

    def run(self):
        try:
            from bzzip_integrator import ArchiveFormat
            fmt = {
                "zip": ArchiveFormat.ZIP,
                "tar": ArchiveFormat.TAR,
                "gz": ArchiveFormat.GZ,
                "tar.gz": ArchiveFormat.TAR_GZ,
            }.get(self.fmt_key, ArchiveFormat.ZIP)
            result = self.integrator.compress_files(
                self.files, self.output_path, fmt, self.continue_on_anomaly
            )
            self.result_ready.emit(result)
        except Exception as e:
            from bzzip_integrator import CompressionResult
            self.result_ready.emit(CompressionResult(success=False, error=str(e)))


class ExtractionThread(QThread):
    """解压线程"""
    result_ready = Signal(object)

    def __init__(self, integrator, archive_path, output_dir):
        super().__init__()
        self.integrator = integrator
        self.archive_path = archive_path
        self.output_dir = output_dir

    def run(self):
        try:
            result = self.integrator.extract_archive(self.archive_path, self.output_dir)
            self.result_ready.emit(result)
        except Exception as e:
            from bzzip_integrator import ExtractionResult
            self.result_ready.emit(ExtractionResult(success=False, error=str(e)))


class TestThread(QThread):
    """测试线程"""
    result_ready = Signal(object)

    def __init__(self, integrator, archive_path):
        super().__init__()
        self.integrator = integrator
        self.archive_path = archive_path

    def run(self):
        try:
            result = self.integrator.test_archive(self.archive_path)
            self.result_ready.emit(result)
        except Exception as e:
            from bzzip_integrator import TestResult
            self.result_ready.emit(TestResult(success=False, error=str(e)))


# ---------------------------------------------------------------------------
# 对话框
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """设置对话框（对应 config 字段）"""

    def __init__(self, config: ConfigDict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("bzzip 设置")
        self.setMinimumWidth(420)
        self._cfg = dict(config)

        layout = QVBoxLayout(self)

        # 功能开关（标签为普通压缩选项的伪装说法，真实机制见 README）
        switch_group = QGroupBox("压缩选项")
        form = QFormLayout(switch_group)
        self.cb_anomaly = QCheckBox("实验性压缩算法")
        self.cb_anomaly.setChecked(self._cfg.get("anomaly_enabled", True))
        self.cb_bee = QCheckBox("智能内容预检")
        self.cb_bee.setChecked(self._cfg.get("bee_detection_enabled", True))
        self.cb_simplify = QCheckBox("深度体积优化")
        self.cb_simplify.setChecked(self._cfg.get("simplify_enabled", True))
        self.cb_accum = QCheckBox("增量压缩优化")
        self.cb_accum.setChecked(self._cfg.get("accumulation_enabled", True))
        self.cb_dialogue = QCheckBox("操作提示消息")
        self.cb_dialogue.setChecked(self._cfg.get("dialogue_enabled", True))
        self.cb_sound = QCheckBox("提示音效")
        self.cb_sound.setChecked(self._cfg.get("sound_enabled", False))
        for cb in (self.cb_anomaly, self.cb_bee, self.cb_simplify,
                   self.cb_accum, self.cb_dialogue, self.cb_sound):
            form.addRow("", cb)
        layout.addWidget(switch_group)

        # 参数
        param_group = QGroupBox("高级参数")
        pform = QFormLayout(param_group)
        self.sp_threshold = QSpinBox()
        self.sp_threshold.setRange(1, 100)
        self.sp_threshold.setValue(int(self._cfg.get("empty_threshold", 10)))
        pform.addRow("优化强度阈值:", self.sp_threshold)

        self.sp_corrupt = QSpinBox()
        self.sp_corrupt.setRange(0, 100)
        self.sp_corrupt.setSuffix(" %")
        self.sp_corrupt.setValue(int(float(self._cfg.get("corrupt_probability", 0.5)) * 100))
        pform.addRow("激进优化比率:", self.sp_corrupt)
        layout.addWidget(param_group)

        # 路径
        path_group = QGroupBox("路径")
        lform = QFormLayout(path_group)
        self.ed_sound = QLineEdit(str(self._cfg.get("sound_path", "")))
        sound_row = QHBoxLayout()
        sound_row.addWidget(self.ed_sound)
        btn_sound = QPushButton("浏览")
        btn_sound.clicked.connect(self._browse_sound)
        sound_row.addWidget(btn_sound)
        lform.addRow("音效文件:", sound_row)

        self.ed_temp = QLineEdit(str(self._cfg.get("temp_dir", "")))
        self.ed_temp.setPlaceholderText("留空则使用系统临时目录")
        temp_row = QHBoxLayout()
        temp_row.addWidget(self.ed_temp)
        btn_temp = QPushButton("浏览")
        btn_temp.clicked.connect(self._browse_temp)
        temp_row.addWidget(btn_temp)
        lform.addRow("临时目录:", temp_row)
        layout.addWidget(path_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_sound(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择音效文件", "", "音频 (*.wav *.mp3 *.ogg);;所有文件 (*.*)")
        if f:
            self.ed_sound.setText(f)

    def _browse_temp(self):
        d = QFileDialog.getExistingDirectory(self, "选择临时目录")
        if d:
            self.ed_temp.setText(d)

    def get_config(self) -> ConfigDict:
        self._cfg.update({
            "anomaly_enabled": self.cb_anomaly.isChecked(),
            "bee_detection_enabled": self.cb_bee.isChecked(),
            "simplify_enabled": self.cb_simplify.isChecked(),
            "accumulation_enabled": self.cb_accum.isChecked(),
            "dialogue_enabled": self.cb_dialogue.isChecked(),
            "sound_enabled": self.cb_sound.isChecked(),
            "sound_path": self.ed_sound.text().strip(),
            "empty_threshold": self.sp_threshold.value(),
            "corrupt_probability": self.sp_corrupt.value() / 100.0,
            "temp_dir": self.ed_temp.text().strip(),
        })
        return self._cfg


class HistoryDialog(QDialog):
    """历史记录对话框"""

    def __init__(self, records, parent=None):
        super().__init__(parent)
        self.setWindowTitle("bzzip 历史记录")
        self.resize(860, 420)
        layout = QVBoxLayout(self)

        table = QTableWidget(len(records), 7)
        table.setHorizontalHeaderLabels(
            ["ID", "操作", "源", "目标", "时间", "状态", "压缩率"])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)

        for i, rec in enumerate(records):
            ratio = f"{rec.compression_ratio:.1%}" if rec.compression_ratio else "-"
            for col, text in enumerate([
                str(rec.id or ""), rec.op_type, rec.source, rec.target,
                rec.timestamp or "", rec.status, ratio,
            ]):
                table.setItem(i, col, QTableWidgetItem(str(text)))

        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(lambda *_: self.accept())
        layout.addWidget(buttons)


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class BzzipMainWindow(QMainWindow):
    """bzzip 主窗口（7-Zip 风格）"""

    status_updated = Signal(str)
    progress_updated = Signal(int, int)

    def __init__(self, config: Optional[ConfigDict] = None):
        super().__init__()
        self.config = config or load_config()
        self.integrator = None
        self.compression_thread = None
        self.extraction_thread = None
        self.test_thread = None
        self.current_path = Path.home()

        self.setWindowTitle("bzzip")
        self.setWindowIcon(app_icon())
        self.resize(980, 620)

        self._setup_actions()
        self._setup_ui()
        self._setup_integrator()
        self.navigate_to(Path.home())

    # ---- UI 构建 ----------------------------------------------------------

    def _setup_actions(self):
        st = self.style()
        self.act_add = QAction(st.standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder),
                               "添加", self)
        self.act_add.setToolTip("压缩所选文件")
        self.act_add.triggered.connect(self.on_add)

        self.act_extract = QAction(st.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon),
                                   "解压", self)
        self.act_extract.setToolTip("解压所选压缩包")
        self.act_extract.triggered.connect(self.on_extract)

        self.act_test = QAction(st.standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton),
                                "测试", self)
        self.act_test.setToolTip("测试所选压缩包的完整性")
        self.act_test.triggered.connect(self.on_test)

        self.act_history = QAction(st.standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
                                   "历史", self)
        self.act_history.setToolTip("查看操作历史记录")
        self.act_history.triggered.connect(self.on_history)

        self.act_settings = QAction(st.standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView),
                                    "设置", self)
        self.act_settings.setToolTip("bzzip 设置")
        self.act_settings.triggered.connect(self.on_settings)

    def _setup_ui(self):
        # --- 菜单栏 ---
        m_file = self.menuBar().addMenu("文件(&F)")
        m_file.addAction(self.act_add)
        m_file.addAction(self.act_extract)
        m_file.addAction(self.act_test)
        m_file.addSeparator()
        act_quit = QAction("退出(&X)", self)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_edit = self.menuBar().addMenu("编辑(&E)")
        act_sel_all = QAction("全选(&A)", self)
        act_sel_all.setShortcut("Ctrl+A")
        act_sel_all.triggered.connect(lambda: self.file_table.selectAll())
        act_sel_inv = QAction("反选(&I)", self)
        act_sel_inv.triggered.connect(self._invert_selection)
        act_sel_none = QAction("取消选择(&N)", self)
        act_sel_none.triggered.connect(lambda: self.file_table.clearSelection())
        for a in (act_sel_all, act_sel_inv, act_sel_none):
            m_edit.addAction(a)

        m_view = self.menuBar().addMenu("查看(&V)")
        act_refresh = QAction("刷新(&R)", self)
        act_refresh.setShortcut("F5")
        act_refresh.triggered.connect(self.refresh_view)
        m_view.addAction(act_refresh)

        m_tools = self.menuBar().addMenu("工具(&T)")
        m_tools.addAction(self.act_history)
        act_clear_hist = QAction("清空历史(&C)", self)
        act_clear_hist.triggered.connect(self.on_clear_history)
        m_tools.addAction(act_clear_hist)
        m_tools.addSeparator()
        m_tools.addAction(self.act_settings)

        m_help = self.menuBar().addMenu("帮助(&H)")
        act_about = QAction("关于 bzzip(&A)", self)
        act_about.triggered.connect(self.on_about)
        m_help.addAction(act_about)

        # --- 工具栏（7-Zip 风格：图标上、文字下）---
        toolbar = self.addToolBar("main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        for a in (self.act_add, self.act_extract, self.act_test):
            toolbar.addAction(a)
        toolbar.addSeparator()
        for a in (self.act_history, self.act_settings):
            toolbar.addAction(a)

        # --- 地址栏 ---
        st = self.style()
        addr_bar = QWidget()
        addr_layout = QHBoxLayout(addr_bar)
        addr_layout.setContentsMargins(4, 2, 4, 2)
        self.btn_up = QPushButton()
        self.btn_up.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
        self.btn_up.setFixedWidth(32)
        self.btn_up.setToolTip("转到上一级")
        self.btn_up.clicked.connect(self.go_up)
        self.path_edit = QLineEdit()
        self.path_edit.returnPressed.connect(self._on_path_entered)
        self.btn_refresh = QPushButton()
        self.btn_refresh.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.btn_refresh.setFixedWidth(32)
        self.btn_refresh.setToolTip("刷新")
        self.btn_refresh.clicked.connect(self.refresh_view)
        addr_layout.addWidget(self.btn_up)
        addr_layout.addWidget(self.path_edit, 1)
        addr_layout.addWidget(self.btn_refresh)

        self.addr_toolbar = QToolBar("address")
        self.addr_toolbar.setMovable(False)
        self.addr_toolbar.addWidget(addr_bar)
        self.addToolBar(self.addr_toolbar)

        # --- 主区域：左目录树 + 右文件列表 ---
        splitter = QSplitter(Qt.Horizontal)

        self.dir_tree = QTreeWidget()
        self.dir_tree.setHeaderHidden(True)
        self.dir_tree.setMaximumWidth(260)
        self.dir_tree.itemClicked.connect(self._on_tree_clicked)
        self.dir_tree.itemExpanded.connect(self._on_tree_expanded)
        self._populate_tree_roots()
        splitter.addWidget(self.dir_tree)

        self.file_table = QTableWidget(0, 4)
        self.file_table.setHorizontalHeaderLabels(["名称", "修改日期", "大小", "类型"])
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.file_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.file_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.file_table.setAlternatingRowColors(True)
        self.file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.file_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.file_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.file_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.file_table.doubleClicked.connect(self._on_table_double_clicked)
        splitter.addWidget(self.file_table)
        splitter.setSizes([240, 740])
        self.setCentralWidget(splitter)

        # --- 日志面板（bzzip 对话输出）---
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_dock = QDockWidget("bzzip 日志", self)
        self.log_dock.setWidget(self.log_text)
        self.log_dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.log_dock.hide()  # 默认收起，像 7-Zip 一样干净
        act_log = self.log_dock.toggleViewAction()
        act_log.setText("日志面板(&L)")
        m_view.addAction(act_log)

        # --- 状态栏 ---
        self.status_label = QLabel("就绪")
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(180)
        self.progress_bar.setVisible(False)
        self.count_label = QLabel("0 个对象")
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress_bar)
        self.statusBar().addPermanentWidget(self.count_label)

        # 信号
        self.status_updated.connect(self.status_label.setText)
        self.progress_updated.connect(self._on_progress)

    def _setup_integrator(self):
        if self.integrator is not None:
            try:
                self.integrator.cleanup()
            except Exception:
                pass
        self.integrator = BzzipIntegrator(
            self.config,
            status_callback=self.status_updated.emit,
            progress_callback=self.progress_updated.emit,
        )

    # ---- 目录树 -----------------------------------------------------------

    def _populate_tree_roots(self):
        self.dir_tree.clear()
        for drive in QDir.drives():
            path = drive.absoluteFilePath()
            item = QTreeWidgetItem([path.rstrip("/\\") or path])
            item.setData(0, Qt.UserRole, path)
            if os.path.isdir(path):
                item.addChild(QTreeWidgetItem(["..."]))  # 占位
            self.dir_tree.addTopLevelItem(item)

    def _populate_tree_item(self, item):
        """展开时填充真实子目录（替换占位符）"""
        if item.childCount() != 1 or item.child(0).text(0) != "...":
            return
        item.takeChildren()
        path = item.data(0, Qt.UserRole)
        try:
            for entry in sorted(Path(path).iterdir(), key=lambda p: p.name.lower()):
                if entry.is_dir():
                    child = QTreeWidgetItem([entry.name])
                    child.setData(0, Qt.UserRole, str(entry))
                    try:
                        next(entry.iterdir())
                        child.addChild(QTreeWidgetItem(["..."]))
                    except (StopIteration, PermissionError, OSError):
                        pass
                    item.addChild(child)
        except (PermissionError, OSError):
            pass

    def _on_tree_expanded(self, item):
        self._populate_tree_item(item)

    def _on_tree_clicked(self, item, column):
        path = item.data(0, Qt.UserRole)
        if path and os.path.isdir(path):
            self.navigate_to(Path(path))

    # ---- 文件列表 ---------------------------------------------------------

    def navigate_to(self, path: Path):
        path = Path(path)
        if path.is_file():
            path = path.parent
        if not path.exists():
            self.status_label.setText(f"路径不存在: {path}")
            return
        self.current_path = path
        self.path_edit.setText(str(path))
        self.refresh_view()

    def refresh_view(self):
        table = self.file_table
        table.setSortingEnabled(False)
        table.setRowCount(0)

        dirs, files = [], []
        try:
            for entry in self.current_path.iterdir():
                (dirs if entry.is_dir() else files).append(entry)
        except (PermissionError, OSError) as e:
            self.status_label.setText(f"无法读取目录: {e}")

        dirs.sort(key=lambda p: p.name.lower())
        files.sort(key=lambda p: p.name.lower())

        for entry, is_dir in [(e, True) for e in dirs] + [(e, False) for e in files]:
            row = table.rowCount()
            table.insertRow(row)
            try:
                stat = entry.stat()
                mtime = fmt_time(stat.st_mtime)
                size = "" if is_dir else human_size(stat.st_size)
            except OSError:
                mtime, size = "", ""

            name_item = QTableWidgetItem(entry.name + ("/" if is_dir else ""))
            name_item.setData(Qt.UserRole, str(entry))
            name_item.setData(Qt.UserRole + 1, is_dir)
            try:
                icon = self.style().standardIcon(
                    QStyle.StandardPixmap.SP_DirIcon if is_dir else SP_FILE_ICON())
                name_item.setIcon(icon)
            except Exception:
                pass
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, QTableWidgetItem(mtime))
            table.setItem(row, 2, QTableWidgetItem(size))
            table.setItem(row, 3, QTableWidgetItem("文件夹" if is_dir else file_type(entry.name)))

        table.setSortingEnabled(True)
        self.count_label.setText(f"{len(dirs) + len(files)} 个对象")

    def _on_table_double_clicked(self, index):
        item = self.file_table.item(index.row(), 0)
        if not item:
            return
        path = Path(item.data(Qt.UserRole))
        if item.data(Qt.UserRole + 1):
            self.navigate_to(path)

    def _on_path_entered(self):
        self.navigate_to(Path(self.path_edit.text().strip()))

    def go_up(self):
        parent = self.current_path.parent
        if parent != self.current_path:
            self.navigate_to(parent)

    # ---- 选择 --------------------------------------------------------------

    def _selected_paths(self) -> List[Path]:
        rows = {i.row() for i in self.file_table.selectionModel().selectedRows()}
        out = []
        for r in rows:
            item = self.file_table.item(r, 0)
            if item:
                out.append(Path(item.data(Qt.UserRole)))
        return out

    def _selected_files(self) -> List[str]:
        return [str(p) for p in self._selected_paths() if p.is_file()]

    def _selected_archives(self) -> List[Path]:
        return [p for p in self._selected_paths() if p.is_file() and is_archive(p.name)]

    def _invert_selection(self):
        model = self.file_table.selectionModel()
        for row in range(self.file_table.rowCount()):
            model.select(
                self.file_table.model().index(row, 0),
                model.SelectionFlag.Toggle | model.SelectionFlag.Rows,
            )

    # ---- 操作 --------------------------------------------------------------

    def on_add(self):
        files = self._selected_files()
        if not files:
            QMessageBox.information(self, "bzzip", "请先在文件列表中选择要压缩的文件。")
            return
        suggested = self.current_path / "archive.zip"
        out, _ = QFileDialog.getSaveFileName(
            self, "压缩到", str(suggested),
            "ZIP (*.zip);;TAR (*.tar);;TAR.GZ (*.tar.gz);;GZ (*.gz)")
        if not out:
            return
        lower = out.lower()
        fmt = "tar.gz" if lower.endswith(".tar.gz") else Path(lower).suffix.lstrip(".")
        if fmt not in ("zip", "tar", "gz", "tar.gz"):
            fmt = "zip"
        self._run_compression(files, out, fmt)

    def _run_compression(self, files: List[str], output: str, fmt_key: str):
        self._set_busy(True)
        self.status_label.setText(f"正在压缩 {len(files)} 个文件...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(files))
        self.progress_bar.setValue(0)
        self.log(f">> 压缩 {len(files)} 个文件 → {output}")
        self.compression_thread = CompressionThread(
            self.integrator, files, output, fmt_key,
            bool(self.config.get("anomaly_enabled", True)))
        self.compression_thread.result_ready.connect(self._on_compress_done)
        self.compression_thread.start()

    def _on_compress_done(self, result):
        self._set_busy(False)
        self.progress_bar.setVisible(False)
        for msg in result.messages:
            self.log(msg)
        if result.success:
            self.status_label.setText(
                f"压缩完成: {result.compression_ratio:.1%} | 用时 {result.processing_time:.2f}s")
            self.log(f"<< 压缩完成，压缩率 {result.compression_ratio:.1%}")
        else:
            self.status_label.setText("压缩失败")
            self.log(f"<< 压缩失败: {result.error}")
            QMessageBox.warning(self, "bzzip", f"压缩失败：\n{result.error}")
        self.refresh_view()

    def on_extract(self):
        archives = self._selected_archives()
        if not archives:
            QMessageBox.information(self, "bzzip", "请先选择一个压缩包（zip/tar/gz 等）。")
            return
        if len(archives) > 1:
            QMessageBox.information(self, "bzzip", "一次只支持解压一个压缩包，已选用第一个。")
        out_dir = QFileDialog.getExistingDirectory(self, "解压到", str(self.current_path))
        if not out_dir:
            return
        self._run_extraction(archives[0], out_dir)

    def _run_extraction(self, archive: Path, out_dir: str):
        self._set_busy(True)
        self.status_label.setText("正在解压...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # 忙碌指示
        self.log(f">> 解压 {archive} → {out_dir}")
        self.extraction_thread = ExtractionThread(self.integrator, str(archive), out_dir)
        self.extraction_thread.result_ready.connect(self._on_extract_done)
        self.extraction_thread.start()

    def _on_extract_done(self, result):
        self._set_busy(False)
        self.progress_bar.setVisible(False)
        for msg in result.messages:
            self.log(msg)
        if result.success:
            self.status_label.setText(f"解压完成: {len(result.extracted_files)} 个文件")
            self.log(f"<< 解压完成，共 {len(result.extracted_files)} 个文件")
        else:
            self.status_label.setText("解压失败")
            self.log(f"<< 解压失败: {result.error}")
            QMessageBox.warning(self, "bzzip", f"解压失败：\n{result.error}")
        self.refresh_view()

    def on_test(self):
        archives = self._selected_archives()
        if not archives:
            QMessageBox.information(self, "bzzip", "请先选择一个压缩包进行测试。")
            return
        self._run_test(archives[0])

    def _run_test(self, archive: Path):
        self._set_busy(True)
        self.status_label.setText("正在测试压缩包...")
        self.log(f">> 测试 {archive}")
        self.test_thread = TestThread(self.integrator, str(archive))
        self.test_thread.result_ready.connect(self._on_test_done)
        self.test_thread.start()

    def _on_test_done(self, result):
        self._set_busy(False)
        for msg in result.messages:
            self.log(msg)
        if result.success and result.is_valid:
            self.status_label.setText("测试通过：压缩包完好")
            self.log("<< 测试通过：压缩包完好")
        else:
            self.status_label.setText("测试失败：压缩包已损坏")
            self.log(f"<< 测试失败: {result.error or '数据损坏'}")
            QMessageBox.warning(self, "bzzip", f"测试失败：\n{result.error or '压缩包数据损坏'}")

    def on_history(self):
        records = self.integrator.get_history(limit=200) if self.integrator else []
        HistoryDialog(records, self).exec()

    def on_clear_history(self):
        ret = QMessageBox.question(self, "bzzip", "确定要清空所有历史记录吗？",
                                   QMessageBox.Yes | QMessageBox.No)
        if ret == QMessageBox.Yes and self.integrator:
            self.integrator.clear_history()
            self.status_label.setText("历史记录已清空")
            self.log("<< 历史记录已清空")

    def on_settings(self):
        dlg = SettingsDialog(self.config, self)
        if dlg.exec() == QDialog.Accepted:
            self.config = dlg.get_config()
            save_config(self.config)
            self._setup_integrator()
            self.status_label.setText("设置已保存")
            self.log("<< 设置已保存并生效")

    def on_about(self):
        QMessageBox.about(
            self, "关于 bzzip",
            "<b>bzzip</b> — 轻量级本地压缩工具<br><br>"
            "支持 ZIP / TAR / TAR.GZ / GZ 格式的压缩、解压与完整性测试；<br>"
            "内置智能内容预检、深度体积优化与增量压缩优化，<br>"
            "可在设置中按需调整。<br><br>"
            "所有操作均在本地完成，不联网。")

    # ---- 状态 / 日志 --------------------------------------------------------

    def _set_busy(self, busy: bool):
        for a in (self.act_add, self.act_extract, self.act_test,
                  self.act_history, self.act_settings):
            a.setEnabled(not busy)

    def _on_progress(self, current: int, total: int):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)

    def log(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{ts}] {message}")
        # 含对话的行让日志面板自动弹出一次
        if "bzzip:" in message and not self.log_dock.isVisible():
            self.log_dock.show()

    # ---- 生命周期 -----------------------------------------------------------

    def closeEvent(self, event):
        save_config(self.config)
        if self.integrator:
            self.integrator.cleanup()
        event.accept()


def SP_FILE_ICON():
    """文件图标（独立函数避免常量书写问题）"""
    return QStyle.StandardPixmap.SP_FileIcon


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("bzzip")
    app.setWindowIcon(app_icon())
    window = BzzipMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
