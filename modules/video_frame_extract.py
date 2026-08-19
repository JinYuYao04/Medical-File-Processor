# -*- coding: utf-8 -*-
"""
视频采帧模块
"""

import os
import subprocess
import tempfile
from typing import List

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from PIL import Image
except ImportError:
    Image = None

from config import TimeSegment
from utils import _get_ffmpeg_path


def extract_frames_opencv(video_path: str, save_folder: str, segments: List[TimeSegment],
                          video_fps: float, total_frames: int, duration: float,
                          progress_callback=None, cancel_event=None, pause_event=None) -> int:
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
                          progress_callback=None, cancel_event=None, pause_event=None) -> int:
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


class VideoFrameExtractModule:
    """视频采帧模块类，用于导出"""
    
    @staticmethod
    def extract_frames(*args, **kwargs):
        return extract_frames(*args, **kwargs)
