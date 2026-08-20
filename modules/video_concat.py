# -*- coding: utf-8 -*-
"""
视频拼接模块
"""

import os
import subprocess
import time
from typing import List, Callable, Optional


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


def run_ffmpeg_concat(video_files: List[str], output_path: str, progress_callback: Optional[Callable] = None, 
                      result_callback: Optional[Callable] = None, cancel_event = None, process_ref: Optional[dict] = None, 
                      pause_flag = None) -> bool:
    """使用FFmpeg拼接多个视频文件（使用concat protocol避免中文路径问题）"""
    if len(video_files) < 2:
        if result_callback:
            result_callback(False)
        return False
    
    try:
        # 使用concat protocol：concat:file1.mp4|file2.mp4|file3.mp4
        # 这种方法直接在命令行指定文件，避免了文本文件的编码问题
        concat_input = "concat:" + "|".join(video_files)
        
        cmd = [
            _get_ffmpeg_path("ffmpeg"),
            "-i", concat_input,
            "-c", "copy",
            "-y",
            output_path
        ]
        
        if progress_callback:
            progress_callback("正在拼接视频...")
        
        creationflags = 0
        if os.name == 'nt':
            creationflags = subprocess.CREATE_NO_WINDOW
        
        # 先计算总时长（用于进度显示）
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
                    duration = float(probe.stdout.decode('utf-8', errors='replace').strip())
                    total_duration += duration
            except Exception:
                pass
        
        # 启动FFmpeg进程
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags
        )
        
        # 存储进程引用供取消使用
        if process_ref is not None:
            process_ref['process'] = process
        
        last_progress = 0
        stderr_buffer = []
        
        # 读取输出并显示进度
        while True:
            # 检查取消
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
            
            # 检查进程是否结束
            ret = process.poll()
            if ret is not None:
                try:
                    remaining = process.stderr.read()
                    if remaining:
                        stderr_buffer.append(remaining)
                except:
                    pass
                break
            
            # 读取stderr
            try:
                chunk = process.stderr.read(4096)
                if chunk:
                    stderr_buffer.append(chunk)
                    # 解析进度
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
        
        # 检查结果
        stderr_text = b''.join(stderr_buffer).decode('utf-8', errors='replace')
        
        # 最终进度更新
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
            # 提取更详细的错误信息
            error_msg = "拼接失败"
            if stderr_text:
                # 查找关键错误行
                error_lines = []
                for line in stderr_text.split('\n'):
                    line_lower = line.lower()
                    if any(keyword in line_lower for keyword in ['error', 'invalid', 'failed', 'could not', 'unable']):
                        error_lines.append(line.strip())
                
                if error_lines:
                    error_msg = '\n'.join(error_lines[-3:])  # 最后3行错误
                else:
                    error_msg = stderr_text[-500:].strip() if len(stderr_text) > 500 else stderr_text.strip()
            
            if progress_callback:
                progress_callback(f"错误: {error_msg}")
            if result_callback:
                result_callback(False)
            return False
        
        # 检查输出文件
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            if progress_callback:
                progress_callback("错误: 输出文件无效")
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
