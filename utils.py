# -*- coding: utf-8 -*-
"""
医疗文件处理工具 - 工具函数模块
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Tuple

try:
    import cv2
except ImportError:
    cv2 = None

from config import VIDEO_EXTENSIONS


def _get_ffmpeg_path(name: str) -> str:
    """获取FFmpeg工具路径（支持PyInstaller打包）"""
    import sys as _sys
    if getattr(_sys, '_MEIPASS', None):
        import os as _os
        path = _os.path.join(_sys._MEIPASS, name + '.exe')
        if _os.path.exists(path):
            return path
    import os as _os
    import shutil as _shutil
    local = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), name + '.exe')
    if _os.path.exists(local):
        return local
    return _shutil.which(name) or name


def probe_video_with_ffmpeg(video_path: str):
    """快速探测视频 fps/总时长/总帧数"""
    ffprobe_exe = _get_ffmpeg_path("ffprobe")
    try:
        proc = subprocess.run(
            [ffprobe_exe, "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate,nb_frames,duration",
             "-show_entries", "format=duration",
             "-of", "json", video_path],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        if proc.returncode != 0:
            return None
        import json as _json
        data = _json.loads(proc.stdout.decode('utf-8', errors='replace'))
        streams = data.get('streams', [])
        fmt = data.get('format', {})
        if not streams:
            return None
        s = streams[0]
        fps = 25.0
        if 'r_frame_rate' in s:
            fps_str = s['r_frame_rate']
            if '/' in fps_str:
                num, den = fps_str.split('/')
                if float(den) != 0:
                    fps = float(num) / float(den)
            else:
                try:
                    fps = float(fps_str)
                except Exception:
                    fps = 25.0
        total_frames = 0
        if 'nb_frames' in s:
            try:
                total_frames = int(s['nb_frames'])
            except Exception:
                total_frames = 0
        duration = 0.0
        if 'duration' in fmt and fmt['duration'] not in (None, 'N/A'):
            try:
                duration = float(fmt['duration'])
            except Exception:
                duration = 0.0
        elif 'duration' in s and s['duration'] not in (None, 'N/A'):
            try:
                duration = float(s['duration'])
            except Exception:
                duration = 0.0
        if total_frames <= 0 and duration > 0 and fps > 0:
            total_frames = int(duration * fps)
        if fps > 0 and (total_frames > 0 or duration > 0):
            if total_frames <= 0:
                total_frames = int(duration * fps)
            return fps, total_frames, duration
        return None
    except Exception as e:
        print(f"probe_video_with_ffmpeg error: {e}")
        if cv2:
            try:
                cap = cv2.VideoCapture(video_path)
                if cap.isOpened():
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    duration = total_frames / fps if fps > 0 else 0
                    cap.release()
                    if fps > 0 and total_frames > 0:
                        return fps, total_frames, duration
            except Exception:
                pass
        return None


def is_video(path: str) -> bool:
    """判断文件是否为视频"""
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def extract_numbers(filename: str) -> Tuple[int, ...]:
    """从文件名中提取数字"""
    stem = Path(filename).stem
    numbers = re.findall(r'\d+', stem)
    return tuple(int(n) for n in numbers)


def get_file_size(path: str) -> str:
    """获取文件大小的人类可读格式"""
    size = os.path.getsize(path)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
