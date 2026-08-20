# -*- coding: utf-8 -*-
"""医疗文件处理工具 - 程序入口"""

import tkinter as tk
import sys
import os

# 将项目根目录添加到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from GUI.gui import MedicalFileProcessorApp


def main():
    """主函数 - 启动应用程序"""
    root = tk.Tk()
    app = MedicalFileProcessorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
