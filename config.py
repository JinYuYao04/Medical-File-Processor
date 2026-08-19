# -*- coding: utf-8 -*-
"""
医疗文件处理工具 - 配置文件
"""

from dataclasses import dataclass

# 界面颜色配置
COLORS = {
    'bg_primary': '#f5f5f5',
    'bg_secondary': '#e8e8e8',
    'bg_card': '#ffffff',
    'accent': '#202124',
    'accent_hover': '#000000',
    'success': '#34a853',
    'text_primary': '#000000',
    'text_secondary': '#5f6368',
    'border': '#dadce0',
    'hover': '#e8f0fe',
    'disabled': '#9aa0a6',
}

# 支持的视频格式
VIDEO_EXTENSIONS = {'.mp4'}


@dataclass
class TimeSegment:
    """时间段数据类，用于视频采帧功能"""
    id: int
    start_sec: float
    end_sec: float
    mode: str
    fps: int
    interval: int
