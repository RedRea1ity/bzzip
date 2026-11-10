#!/usr/bin/env python3
"""
bzzip 系统启动脚本

这个脚本启动 bzzip 的图形界面。
"""

import sys
import os

# 添加当前目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from bzzip_gui import main
    if __name__ == "__main__":
        main()
except ImportError as e:
    print(f"导入错误: {e}")
    print("请确保已安装 PySide6:")
    print("pip install PySide6")
    sys.exit(1)
except Exception as e:
    print(f"启动错误: {e}")
    sys.exit(1)