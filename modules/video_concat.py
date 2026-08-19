# -*- coding: utf-8 -*-
"""
视频拼接模块
"""

import os
import shutil
import subprocess
import tempfile
import time
from typing import List

from utils import _get_ffmpeg_path


def run_ffmpeg_concat(video_files: List[str], output_path: str, progress_callback=None, 
                      result_callback=None, cancel_event=None, process_ref=None, 
                      pause_flag=None) -> bool:
    """使用FFmpeg拼接多个视频文件"""
    if len(video_files) < 2:
        if result_callback:
            result_callback(False)
        return False
    temp_dir = tempfile.mkdtemp(prefix="video_concat_")
    temp_list_file = os.path.join(temp_dir, "concat_list.txt")
    try:
        list_content = ""
        for video in video_files:
            norm_path = os.path.normpath(video)
            list_content += f"file '{norm_path}'\n"
        with open(temp_list_file, 'w', encoding='utf-8') as f:
            f.write(list_content)
        cmd = [
            _get_ffmpeg_path("ffmpeg"),
            "-f", "concat",
            "-safe", "0",
            "-i", temp_list_file,
            "-c", "copy",
            "-y",
            output_path
        ]
        if progress_callback:
            progress_callback("正在拼接视频...")
        creationflags = 0
        if os.name == 'nt':
            creationflags = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags
        )
        if process_ref is not None:
            process_ref['process'] = process
        
        total_duration = 0
        for video in video_files:
            try:
                probe = subprocess.run(
                    [_get_ffmpeg_path("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", video],
                    capture_output=True, timeout=5,
                    creationflags=creationflags
                )
                if probe.returncode == 0:
                    total_duration += float(probe.stdout.decode('utf-8', errors='replace').strip())
            except:
                pass
        
        last_progress = 0
        stderr_buffer = []
        while True:
            if cancel_event and cancel_event.is_set():
                try:
                    process.terminate()
                except:
                    pass
                time.sleep(0.3)
                if progress_callback:
                    progress_callback("拼接已取消")
                if result_callback:
                    result_callback(False)
                return False
            
            ret = process.poll()
            if ret is not None:
                try:
                    remaining = process.stderr.read()
                    if remaining:
                        stderr_buffer.append(remaining)
                except:
                    pass
                break
            
            try:
                chunk = process.stderr.read(4096)
                if chunk:
                    stderr_buffer.append(chunk)
                    try:
                        text = chunk.decode('utf-8', errors='replace')
                    except:
                        text = chunk.decode('gbk', errors='replace')
                    if "time=" in text and total_duration > 0:
                        try:
                            time_str = text.split("time=")[1].split()[0]
                            parts = time_str.split(":")
                            if len(parts) == 3:
                                current_time = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                                progress = int(current_time / total_duration * 100)
                                if progress > last_progress:
                                    last_progress = progress
                                    if progress_callback:
                                        progress_callback(f"进度: {progress}%")
                        except:
                            pass
                else:
                    time.sleep(0.1)
            except Exception:
                time.sleep(0.1)
                if process.poll() is not None:
                    try:
                        remaining = process.stderr.read()
                        if remaining:
                            stderr_buffer.append(remaining)
                    except:
                        pass
                    break
        
        stderr_text = b''.join(stderr_buffer).decode('utf-8', errors='replace')
        
        if total_duration > 0:
            for line in stderr_text.split('\n'):
                if 'time=' in line:
                    try:
                        time_str = line.split('time=')[1].split()[0]
                        parts = time_str.split(":")
                        if len(parts) == 3:
                            current_time = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                            progress = int(current_time / total_duration * 100)
                            if progress > last_progress:
                                last_progress = progress
                                if progress_callback:
                                    progress_callback(f"进度: {progress}%")
                    except:
                        pass
        
        if process.returncode != 0:
            if progress_callback:
                progress_callback(f"错误: {stderr_text[-500:]}")
            if result_callback:
                result_callback(False)
            return False
        if progress_callback:
            progress_callback(f"完成！输出: {output_path}")
        if result_callback:
            result_callback(True)
        return True
    except Exception as e:
        if progress_callback:
            progress_callback(f"错误: {str(e)}")
        if result_callback:
            result_callback(False)
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


class VideoConcatModule:
    """视频拼接模块类，用于导出"""
    
    @staticmethod
    def run_ffmpeg_concat(*args, **kwargs):
        return run_ffmpeg_concat(*args, **kwargs)
