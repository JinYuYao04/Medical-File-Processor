# -*- coding: utf-8 -*-
"""
视频采帧模块
"""

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from PIL import Image
except ImportError:
    Image = None


# ========== 配置和工具函数 ==========

@dataclass
class TimeSegment:
    """时间段数据类，用于视频采帧功能"""
    id: int
    start_sec: float
    end_sec: float
    mode: str
    fps: int
    interval: int


VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm'}


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
    
    # 优先查找项目根目录（modules的上级目录）
    root_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    root_path = _os.path.join(root_dir, name + '.exe')
    if _os.path.exists(root_path):
        return root_path
    
    # 查找当前模块目录
    local = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), name + '.exe')
    if _os.path.exists(local):
        return local
    
    # 查找系统PATH
    system_path = _shutil.which(name)
    if system_path:
        return system_path
    
    return name


def probe_video_with_ffmpeg(video_path: str):
    """快速探测视频 fps/总时长/总帧数
    
    返回 (fps, total_frames, duration) 或 None
    """
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


# ========== 视频采帧核心函数 ==========

def extract_frames_opencv(video_path: str, save_folder: str, segments: List[TimeSegment],
                          video_fps: float, total_frames: int, duration: float,
                          progress_callback = None, cancel_event = None, pause_event = None) -> int:
    """使用OpenCV提取视频帧"""
    if cv2 is None:
        return 0
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return 0
    
    saved_count = 0
    total_segments = len(segments)
    
    for seg_idx, segment in enumerate(segments):
        if cancel_event and cancel_event.is_set():
            break
        
        start_sec = segment.start_sec if segment.start_sec else 0
        end_sec = segment.end_sec if segment.end_sec else duration
        
        start_frame = int(start_sec * video_fps)
        end_frame = int(end_sec * video_fps)
        start_frame = max(0, min(start_frame, total_frames - 1))
        end_frame = max(0, min(end_frame, total_frames - 1))
        
        if segment.mode == 'fps':
            target_fps = segment.fps if segment.fps > 0 else 1
            frame_interval = max(1, int(video_fps / target_fps))
        else:
            interval_sec = segment.interval if segment.interval > 0 else 5
            frame_interval = max(1, int(video_fps * interval_sec))
        
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        seg_total = end_frame - start_frame + 1
        
        for curr_f in range(start_frame, end_frame + 1):
            if cancel_event and cancel_event.is_set():
                break
            if pause_event and pause_event.is_set():
                pause_event.wait()
                continue
            
            ret, frame = cap.read()
            if not ret:
                break
            
            local_frame = curr_f - start_frame
            if local_frame % frame_interval == 0:
                time_sec = curr_f / video_fps
                h = int(time_sec // 3600)
                m = int(time_sec % 3600 // 60)
                s = int(time_sec % 60)
                ms = int((time_sec % 1) * 100)
                file_time_str = f"{h:02d}-{m:02d}-{s:02d}-{ms:02d}"
                filename = os.path.join(save_folder, f"frame_{file_time_str}.jpg")
                is_success, im_buf_arr = cv2.imencode(".jpg", frame)
                if is_success:
                    im_buf_arr.tofile(filename)
                    saved_count += 1
            
            if curr_f % 10 == 0 or curr_f == end_frame:
                overall_progress = ((seg_idx + local_frame / seg_total) / total_segments) * 100
                if progress_callback:
                    progress_callback(overall_progress, saved_count)
    
    cap.release()
    return saved_count


def extract_frames_ffmpeg(video_path: str, save_folder: str, segments: List[TimeSegment],
                          video_fps: float, duration: float,
                          progress_callback = None, cancel_event = None, pause_event = None) -> int:
    """使用FFmpeg提取视频帧（OpenCV不可用时的备选方案）"""
    if video_fps <= 0 or not video_path:
        return 0
    
    saved_count = 0
    total_segments = len(segments)
    ffmpeg_exe = _get_ffmpeg_path("ffmpeg")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    
    for seg_idx, segment in enumerate(segments):
        if cancel_event and cancel_event.is_set():
            break
        
        start_sec = segment.start_sec if segment.start_sec else 0
        end_sec = segment.end_sec if segment.end_sec else duration
        
        if segment.mode == 'fps':
            target_fps = segment.fps if segment.fps > 0 else 1
            step_sec = 1.0 / target_fps
        else:
            step_sec = segment.interval if segment.interval > 0 else 5
        
        t = start_sec
        seg_total = max(1, int((end_sec - start_sec) / step_sec))
        
        while t <= end_sec:
            if cancel_event and cancel_event.is_set():
                break
            if pause_event and pause_event.is_set():
                pause_event.wait()
            
            filename = os.path.join(
                save_folder,
                f"frame_{int(t // 3600):02d}-{int(t % 3600 // 60):02d}-{int(t % 60):02d}-{int((t % 1) * 100):02d}.jpg"
            )
            out_path = os.path.join(tempfile.gettempdir(), f"_frame_{int(t * 1000)}.png")
            cmd = [
                ffmpeg_exe, "-y", "-ss", f"{t:.3f}",
                "-i", video_path,
                "-vframes", "1",
                "-vcodec", "png",
                "-hide_banner", "-loglevel", "error",
                out_path
            ]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=15, creationflags=creationflags)
                if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    if Image is not None:
                        img = Image.open(out_path)
                        img = img.convert('RGB')
                        img.save(filename, 'JPEG', quality=95)
                    else:
                        cmd2 = [
                            ffmpeg_exe, "-y", "-i", out_path,
                            "-q:v", "2", filename
                        ]
                        subprocess.run(cmd2, capture_output=True, timeout=10, creationflags=creationflags)
                    saved_count += 1
                    os.remove(out_path)
            except Exception:
                pass
            
            t += step_sec
            overall_progress = ((seg_idx + saved_count / max(1, seg_total)) / total_segments) * 100
            if progress_callback:
                progress_callback(overall_progress, saved_count)
    
    return saved_count
