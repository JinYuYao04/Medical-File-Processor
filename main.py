# -*- coding: utf-8 -*-
import os
from io import BytesIO
import re
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
try:
    import cv2
except ImportError:
    cv2 = None
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None
from pathlib import Path
from typing import List, Tuple
# 导入各个功能模块
from modules.video_concat import VideoConcatModule
from modules.video_frame_extract import VideoFrameExtractModule
from modules.file_rename import FileRenameModule
from modules.file_cleanup import FileCleanupModule

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
from dataclasses import dataclass
@dataclass
class TimeSegment:
    id: int
    start_sec: float
    end_sec: float
    mode: str
    fps: int
    interval: int
def _get_ffmpeg_path(name: str) -> str:
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
    """快速探测视频 fps/总时长/总帧数。先不扫描所有 packet，从 metadata 读取。

    返回 (fps, total_frames, duration) 或 None。
    """
    ffprobe_exe = _get_ffmpeg_path("ffprobe")
    try:
        # 第一次：仅从容器元数据读取（极快，不解码）
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
        # 解析 fps
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
        # 解析总帧数（优先 nb_frames 字段，几乎所有容器都有）
        total_frames = 0
        if 'nb_frames' in s:
            try:
                total_frames = int(s['nb_frames'])
            except Exception:
                total_frames = 0
        # 解析时长
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
        # 兜底：fps*duration 估算总帧数
        if total_frames <= 0 and duration > 0 and fps > 0:
            total_frames = int(duration * fps)
        if fps > 0 and (total_frames > 0 or duration > 0):
            if total_frames <= 0:
                total_frames = int(duration * fps)
            return fps, total_frames, duration
        return None
    except Exception as e:
        print(f"probe_video_with_ffmpeg error: {e}")
        # 备用方案：使用OpenCV读取视频信息
        if cv2:
            try:
                cap = cv2.VideoCapture(video_path)
                if cap.isOpened():
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    duration = total_frames / fps if fps > 0 else 0
                    cap.release()
                    if fps > 0 and total_frames > 0:
                        print(f"OpenCV fallback success: fps={fps}, frames={total_frames}")
                        return fps, total_frames, duration
            except Exception as cv_e:
                print(f"OpenCV fallback error: {cv_e}")
        return None


VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm'}
def is_video(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS
def extract_numbers(filename: str) -> Tuple[int, ...]:
    stem = Path(filename).stem
    numbers = re.findall(r'\d+', stem)
    return tuple(int(n) for n in numbers)
def get_file_size(path: str) -> str:
    size = os.path.getsize(path)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
def run_ffmpeg_concat(video_files: List[str], output_path: str, progress_callback=None, result_callback=None, cancel_event=None, process_ref=None, pause_flag=None) -> bool:
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
        # Store process reference for cancellation
        if process_ref is not None:
            process_ref['process'] = process
        
        # Calculate total duration
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
        
        import time
        last_progress = 0
        last_time = time.time()
        progress_update_interval = 0.5  # Update progress every 0.5 seconds
        
        # Read stderr in a loop with timeout
        stderr_buffer = []
        while True:
            # Check for cancellation
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
            
            # Check if process finished
            ret = process.poll()
            if ret is not None:
                # Process finished, read remaining stderr
                try:
                    remaining = process.stderr.read()
                    if remaining:
                        stderr_buffer.append(remaining)
                except:
                    pass
                break
            
            # Read stderr with timeout
            try:
                import struct
                # Use a non-blocking approach
                chunk = process.stderr.read(4096)
                if chunk:
                    stderr_buffer.append(chunk)
                    # Update progress from chunk
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
                    # No data available, wait a bit
                    time.sleep(0.1)
            except Exception as e:
                # Read might block, wait and try again
                time.sleep(0.1)
                # Check if process still running
                if process.poll() is not None:
                    try:
                        remaining = process.stderr.read()
                        if remaining:
                            stderr_buffer.append(remaining)
                    except:
                        pass
                    break
        
        # Combine stderr
        stderr_text = b''.join(stderr_buffer).decode('utf-8', errors='replace')
        
        # Final progress update from full stderr
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
def rename_files_by_number(folder_path: str, output_folder: str = None, digit_count: int = 1, start_index: int = 1, end_index: int = 0, start_number: int = 1, file_extensions: str = "", progress_callback=None, result_callback=None, cancel_event=None) -> int:
    import re
    folder = Path(folder_path)
    output_dir = Path(output_folder) if output_folder else folder
    try:
        copy_mode = output_dir.resolve() != folder.resolve()
    except Exception:
        copy_mode = str(output_dir) != str(folder)
    try:
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)
        if not output_dir.is_dir():
            if progress_callback:
                progress_callback(('error', 0, 0, '', f'输出路径不是文件夹: {output_dir}'))
            if result_callback:
                result_callback(0)
            return 0
    except Exception as e:
        if progress_callback:
            progress_callback(('error', 0, 0, '', f'无法创建输出文件夹: {e}'))
        if result_callback:
            result_callback(0)
        return 0
    temp_prefix = "__temp_ren_"
    
    # 文件扩展名过滤
    if not file_extensions:
        if progress_callback:
            progress_callback(('error', 0, 0, '', '请输入文件类型（如.jpg）'))
        if result_callback:
            result_callback(0)
        return 0
    
    # 只支持单个文件类型
    ext = file_extensions.strip().lower()
    if not ext.startswith('.'):
        ext = f'.{ext}'
    file_suffixes = [ext]
    
    # Natural sort
    def natural_sort_key(path):
        name = path.name.lower()
        parts = re.split(r'(\d+)', name)
        return [(int(p) if p.isdigit() else p) for p in parts]
    
    # 按文件类型分组并独立编号
    renamed_count = 0
    total_processed = 0
    
    if progress_callback and copy_mode:
        progress_callback(('info', 0, 0, '',
                           f'复制模式：源文件夹将保持原样，文件复制到 "{output_dir}"'))
    
    # 针对每个文件类型独立操作
    for ext in file_suffixes:
        # Check for cancellation
        if cancel_event and cancel_event.is_set():
            if progress_callback:
                progress_callback(('info', 0, 0, '', '操作已取消'))
            break
        
        # 获取该类型的所有文件
        type_files = [
            f for f in folder.iterdir()
            if f.is_file()
            and not f.name.startswith(temp_prefix)
            and f.suffix.lower() == ext
        ]
        
        if not type_files:
            continue
        
        # 对该类型的文件进行自然排序
        type_files.sort(key=natural_sort_key)
        
        # 应用文件范围
        s_idx = max(1, start_index) - 1
        e_idx = end_index if end_index and end_index > 0 else len(type_files)
        e_idx = min(e_idx, len(type_files))
        s_idx = min(s_idx, e_idx)
        selected_files = type_files[s_idx:e_idx]
        
        if not selected_files:
            continue
        
        # 对该类型的文件独立编号
        for i, file_path in enumerate(selected_files):
            # Check for cancellation
            if cancel_event and cancel_event.is_set():
                if progress_callback:
                    progress_callback(('info', 0, 0, '', '操作已取消'))
                break
            
            seq = start_number + i  # 每个类型独立从起始序号开始
            suffix = file_path.suffix
            new_name = f"{str(seq).zfill(digit_count)}{suffix}"
            new_path = output_dir / new_name
            original = file_path.name
            total_processed += 1
            
            if progress_callback:
                progress_callback(('phase1', total_processed, len(type_files), original, new_name))
            
            if new_path.exists():
                try:
                    if new_path.resolve() == file_path.resolve():
                        if progress_callback:
                            progress_callback(('phase2', total_processed, len(type_files), original, new_name))
                        renamed_count += 1
                        continue
                except Exception:
                    pass
                if progress_callback:
                    progress_callback(('skipped', total_processed, len(type_files), original, new_name))
                continue
            
            try:
                if copy_mode:
                    shutil.copy2(file_path, new_path)
                else:
                    temp_name = f"{temp_prefix}{seq}_{ext.replace('.', '')}{suffix}"
                    temp_path = folder / temp_name
                    if temp_path.resolve() != file_path.resolve():
                        file_path.rename(temp_path)
                        temp_path.rename(new_path)
                if progress_callback:
                    progress_callback(('phase2', total_processed, len(type_files), original, new_name))
                renamed_count += 1
            except Exception as e:
                if progress_callback:
                    progress_callback(('phase2_error', total_processed, len(type_files), original, str(e)))
    
    if renamed_count == 0 and total_processed == 0:
        if progress_callback:
            progress_callback(('empty', 0, 0, '', '该文件夹中没有可处理的文件！'))
    
    if result_callback:
        result_callback(renamed_count)
    return renamed_count
class MedicalFileProcessorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("医疗文件处理工具")
        self.root.geometry("1200x1000")
        self.root.configure(bg=COLORS['bg_primary'])
        self.video_folder_path = tk.StringVar()
        self.video_output_folder = tk.StringVar()
        self.rename_folder_path = tk.StringVar()
        self.rename_output_folder = tk.StringVar()
        self.clean_folder_path = tk.StringVar()
        self.clean_output_folder = tk.StringVar()
        self.video_files: List[str] = []
        self.selected_videos: List[str] = []
        self.is_processing = False
        self.current_tab = tk.StringVar(value="video")
        self.frame_video_path = ""
        self.frame_save_folder = ""
        self.frame_cap = None
        self.frame_total_frames = 0
        self.frame_fps = 0
        self.frame_duration = 0
        self.frame_start_frame = 0
        self.frame_end_frame = 0
        self.frame_current_frame_idx = 0
        self.frame_scale_var = tk.DoubleVar()
        self.frame_segments = []
        self.frame_segment_widgets = {}
        self.frame_segment_counter = 0
        self.segments_row_widgets = []
        self.frame_is_processing = False
        self.frame_is_paused = False
        self.frame_pause_event = None
        self.rename_digit_count = tk.IntVar(value=1)
        self.rename_start_index = tk.IntVar(value=1)  # 文件范围起始位置
        self.rename_end_index = tk.IntVar(value=0)     # 文件范围结束位置（0表示全部）
        self.rename_start_number = tk.IntVar(value=1)  # 起始序号
        self.rename_file_extensions = tk.StringVar(value="")  # 文件扩展名过滤（例如：.jpg,.png）
        self.setup_ui()
    def setup_ui(self):
        main_frame = tk.Frame(self.root, bg=COLORS['bg_primary'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tab_frame = tk.Frame(main_frame, bg=COLORS['bg_primary'])
        tab_frame.pack(fill=tk.X, pady=(0, 16))
        self.tab_video_btn = tk.Button(
            tab_frame,
            text="视频拼接",
            font=("Microsoft YaHei UI", 14),
            width=12,
            bg=COLORS['accent'],
            fg=COLORS['bg_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=lambda: self.switch_tab("video")
        )
        self.tab_video_btn.pack(side=tk.LEFT, padx=4)
        self.tab_video_btn.bind('<Enter>', lambda e: self.tab_video_btn.configure(bg=COLORS['accent_hover']))
        self.tab_video_btn.bind('<Leave>', lambda e: self.tab_video_btn.configure(
            bg=COLORS['accent'] if self.current_tab.get() == "video" else COLORS['bg_card']))
        self.tab_frame_btn = tk.Button(
            tab_frame,
            text="视频采帧",
            font=("Microsoft YaHei UI", 14),
            width=12,
            bg=COLORS['bg_card'],
            fg=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=lambda: self.switch_tab("frame")
        )
        self.tab_frame_btn.pack(side=tk.LEFT, padx=4)
        self.tab_frame_btn.bind('<Enter>', lambda e: self.tab_frame_btn.configure(
            bg=COLORS['accent_hover'] if self.current_tab.get() == "frame" else COLORS['hover']))
        self.tab_frame_btn.bind('<Leave>', lambda e: self.tab_frame_btn.configure(
            bg=COLORS['accent'] if self.current_tab.get() == "frame" else COLORS['bg_card']))
        self.tab_clean_btn = tk.Button(
            tab_frame,
            text="文件清洗",
            font=("Microsoft YaHei UI", 14),
            width=12,
            bg=COLORS['bg_card'],
            fg=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=lambda: self.switch_tab("clean")
        )
        self.tab_clean_btn.pack(side=tk.LEFT, padx=4)
        self.tab_clean_btn.bind('<Enter>', lambda e: self.tab_clean_btn.configure(
            bg=COLORS['accent_hover'] if self.current_tab.get() == "clean" else COLORS['hover']))
        self.tab_clean_btn.bind('<Leave>', lambda e: self.tab_clean_btn.configure(
            bg=COLORS['accent'] if self.current_tab.get() == "clean" else COLORS['bg_card']))
        self.tab_rename_btn = tk.Button(
            tab_frame,
            text="文件重命名",
            font=("Microsoft YaHei UI", 14),
            width=12,
            bg=COLORS['bg_card'],
            fg=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=lambda: self.switch_tab("rename")
        )
        self.tab_rename_btn.pack(side=tk.LEFT, padx=4)
        self.tab_rename_btn.bind('<Enter>', lambda e: self.tab_rename_btn.configure(
            bg=COLORS['accent_hover'] if self.current_tab.get() == "rename" else COLORS['hover']))
        self.tab_rename_btn.bind('<Leave>', lambda e: self.tab_rename_btn.configure(
            bg=COLORS['accent'] if self.current_tab.get() == "rename" else COLORS['bg_card']))
        self.content_frame = tk.Frame(main_frame, bg=COLORS['bg_primary'])
        self.content_frame.pack(fill=tk.BOTH, expand=True)
        self.video_tab = self.create_video_tab()
        self.rename_tab = self.create_rename_tab()
        self.frame_tab = self.create_frame_tab()
        self.clean_tab = self.create_clean_tab()
        self.video_tab.pack(fill=tk.BOTH, expand=True)
        self.rename_tab.pack_forget()
        self.frame_tab.pack_forget()
        self.clean_tab.pack_forget()
    def switch_tab(self, tab_name: str):
        if self.is_processing:
            messagebox.showwarning("提示", "正在进行操作，请等待完成")
            return
        self.current_tab.set(tab_name)
        if tab_name == "video":
            self.tab_video_btn.configure(bg=COLORS['accent'], fg=COLORS['bg_primary'])
            self.tab_frame_btn.configure(bg=COLORS['bg_card'], fg=COLORS['text_primary'])
            self.tab_rename_btn.configure(bg=COLORS['bg_card'], fg=COLORS['text_primary'])
            self.tab_clean_btn.configure(bg=COLORS['bg_card'], fg=COLORS['text_primary'])
            self.frame_tab.pack_forget()
            self.rename_tab.pack_forget()
            self.clean_tab.pack_forget()
            self.video_tab.pack(fill=tk.BOTH, expand=True)
        elif tab_name == "frame":
            self.tab_video_btn.configure(bg=COLORS['bg_card'], fg=COLORS['text_primary'])
            self.tab_frame_btn.configure(bg=COLORS['accent'], fg=COLORS['bg_primary'])
            self.tab_rename_btn.configure(bg=COLORS['bg_card'], fg=COLORS['text_primary'])
            self.tab_clean_btn.configure(bg=COLORS['bg_card'], fg=COLORS['text_primary'])
            self.video_tab.pack_forget()
            self.rename_tab.pack_forget()
            self.clean_tab.pack_forget()
            self.frame_tab.pack(fill=tk.BOTH, expand=True)
        elif tab_name == "clean":
            self.tab_video_btn.configure(bg=COLORS['bg_card'], fg=COLORS['text_primary'])
            self.tab_frame_btn.configure(bg=COLORS['bg_card'], fg=COLORS['text_primary'])
            self.tab_rename_btn.configure(bg=COLORS['bg_card'], fg=COLORS['text_primary'])
            self.tab_clean_btn.configure(bg=COLORS['accent'], fg=COLORS['bg_primary'])
            self.video_tab.pack_forget()
            self.frame_tab.pack_forget()
            self.rename_tab.pack_forget()
            self.clean_tab.pack(fill=tk.BOTH, expand=True)
        else:
            self.tab_video_btn.configure(bg=COLORS['bg_card'], fg=COLORS['text_primary'])
            self.tab_frame_btn.configure(bg=COLORS['bg_card'], fg=COLORS['text_primary'])
            self.tab_rename_btn.configure(bg=COLORS['accent'], fg=COLORS['bg_primary'])
            self.tab_clean_btn.configure(bg=COLORS['bg_card'], fg=COLORS['text_primary'])
            self.video_tab.pack_forget()
            self.frame_tab.pack_forget()
            self.rename_tab.pack_forget()
            self.clean_tab.pack_forget()
            self.rename_tab.pack(fill=tk.BOTH, expand=True)
            self.rename_tab.after(100, self._update_rename_layout)
    def _update_rename_layout(self):
        if hasattr(self, 'rename_left_frame') and hasattr(self, 'rename_right_frame'):
            self.rename_tab.update_idletasks()
            total_w = self.rename_tab.winfo_width()
            total_h = self.rename_tab.winfo_height()
            if total_w > 0 and total_h > 0:
                half = total_w // 2
                self.rename_left_frame.place(x=0, y=0, width=half, height=total_h)
                self.rename_right_frame.place(x=half, y=0, width=total_w - half, height=total_h)
    def create_video_tab(self) -> tk.Frame:
        tab = tk.Frame(self.content_frame, bg=COLORS['bg_primary'])
        
        folder_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=12, pady=12)
        folder_frame.pack(fill=tk.X, pady=(12, 0), padx=16)
        tk.Label(
            folder_frame,
            text="选择包含视频的文件夹：",
            font=("Microsoft YaHei UI", 12),
            fg=COLORS['text_primary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W)
        path_frame = tk.Frame(folder_frame, bg=COLORS['bg_card'])
        path_frame.pack(fill=tk.X, pady=(8, 0))
        self.video_path_entry = tk.Entry(
            path_frame,
            textvariable=self.video_folder_path,
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary']
        )
        self.video_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        browse_video_btn = tk.Button(
            path_frame,
            text="浏览",
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            activebackground=COLORS['border'],
            activeforeground=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=self.browse_video_folder
        )
        browse_video_btn.pack(side=tk.LEFT)
        browse_video_btn.bind('<Enter>', lambda e: browse_video_btn.configure(bg=COLORS['border']))
        browse_video_btn.bind('<Leave>', lambda e: browse_video_btn.configure(bg=COLORS['bg_secondary']))
        list_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=8, pady=8)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0), padx=16)
        header_frame = tk.Frame(list_frame, bg=COLORS['bg_card'])
        header_frame.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            header_frame,
            text="视频文件列表",
            font=("Microsoft YaHei UI", 12, "bold"),
            fg=COLORS['text_primary'],
            bg=COLORS['bg_card']
        ).pack(side=tk.LEFT)
        btn_frame = tk.Frame(header_frame, bg=COLORS['bg_card'])
        btn_frame.pack(side=tk.RIGHT)
        btn_select_all = tk.Button(
            btn_frame,
            text="全选",
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            activebackground=COLORS['border'],
            activeforeground=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=self.select_all_videos
        )
        btn_select_all.pack(side=tk.LEFT, padx=2)
        btn_select_all.bind('<Enter>', lambda e: btn_select_all.configure(bg=COLORS['border']))
        btn_select_all.bind('<Leave>', lambda e: btn_select_all.configure(bg=COLORS['bg_secondary']))
        btn_deselect_all = tk.Button(
            btn_frame,
            text="取消",
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            activebackground=COLORS['border'],
            activeforeground=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=self.deselect_all_videos
        )
        btn_deselect_all.pack(side=tk.LEFT, padx=2)
        btn_deselect_all.bind('<Enter>', lambda e: btn_deselect_all.configure(bg=COLORS['border']))
        btn_deselect_all.bind('<Leave>', lambda e: btn_deselect_all.configure(bg=COLORS['bg_secondary']))
        btn_move_up = tk.Button(
            btn_frame,
            text="上移",
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            activebackground=COLORS['border'],
            activeforeground=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=lambda: self.move_video(-1)
        )
        btn_move_up.pack(side=tk.LEFT, padx=2)
        btn_move_up.bind('<Enter>', lambda e: btn_move_up.configure(bg=COLORS['border']))
        btn_move_up.bind('<Leave>', lambda e: btn_move_up.configure(bg=COLORS['bg_secondary']))
        btn_move_down = tk.Button(
            btn_frame,
            text="下移",
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            activebackground=COLORS['border'],
            activeforeground=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=lambda: self.move_video(1)
        )
        btn_move_down.pack(side=tk.LEFT, padx=2)
        btn_move_down.bind('<Enter>', lambda e: btn_move_down.configure(bg=COLORS['border']))
        btn_move_down.bind('<Leave>', lambda e: btn_move_down.configure(bg=COLORS['bg_secondary']))
        list_container = tk.Frame(list_frame, bg=COLORS['bg_card'])
        list_container.pack(fill=tk.BOTH, expand=True)
        self.video_listbox = tk.Listbox(
            list_container,
            font=("Consolas", 11),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            selectbackground=COLORS['accent'],
            selectforeground=COLORS['bg_primary'],
            relief=tk.FLAT,
            highlightthickness=0,
            selectmode=tk.EXTENDED
        )
        self.video_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.video_listbox.bind('<<ListboxSelect>>', self.on_video_select)
        self.video_listbox.bind('<Button-1>', self.on_video_click)
        scrollbar = tk.Scrollbar(list_container, orient=tk.VERTICAL)
        scrollbar.config(command=self.video_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.video_listbox.config(yscrollcommand=scrollbar.set)
        
        settings_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=12, pady=12)
        settings_frame.pack(fill=tk.X, pady=(12, 0), padx=16)
        tk.Label(
            settings_frame,
            text="输出设置",
            font=("Microsoft YaHei UI", 12, "bold"),
            fg=COLORS['text_primary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W)
        tk.Label(
            settings_frame,
            text="输出文件夹：",
            font=("Microsoft YaHei UI", 10),
            fg=COLORS['text_secondary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W, pady=(12, 0))
        output_folder_frame = tk.Frame(settings_frame, bg=COLORS['bg_card'])
        output_folder_frame.pack(fill=tk.X)
        self.video_output_entry = tk.Entry(
            output_folder_frame,
            textvariable=self.video_output_folder,
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary']
        )
        self.video_output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        select_video_output_btn = tk.Button(
            output_folder_frame,
            text="选择",
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=self.browse_video_output_folder
        )
        select_video_output_btn.pack(side=tk.LEFT)
        select_video_output_btn.bind('<Enter>', lambda e: select_video_output_btn.configure(bg=COLORS['border']))
        select_video_output_btn.bind('<Leave>', lambda e: select_video_output_btn.configure(bg=COLORS['bg_secondary']))
        tk.Label(
            settings_frame,
            text="输出文件名：",
            font=("Microsoft YaHei UI", 10),
            fg=COLORS['text_secondary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W, pady=(12, 0))
        self.output_name_entry = tk.Entry(
            settings_frame,
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary']
        )
        self.output_name_entry.pack(fill=tk.X, pady=(4, 0))
        self.output_name_entry.insert(0, "output.mp4")
        action_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=12, pady=12)
        action_frame.pack(fill=tk.X, pady=(12, 0), padx=16)
        # 进度条
        self.concat_progress = tk.DoubleVar(value=0)
        self.concat_progress_bar = tk.ttk.Progressbar(action_frame, variable=self.concat_progress, maximum=100, length=200, mode='determinate')
        self.concat_progress_bar.pack(fill=tk.X, pady=(0, 8))
        
        # 按钮行
        concat_btn_row = tk.Frame(action_frame, bg=COLORS['bg_card'])
        concat_btn_row.pack(fill=tk.X)
        self.concat_btn = tk.Button(
            concat_btn_row,
            text="开始拼接",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            activebackground=COLORS['border'],
            activeforeground=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=self.start_concat
        )
        self.concat_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.concat_btn.config(state=tk.DISABLED)
        self.concat_btn.bind('<Enter>', lambda e: self.concat_btn.configure(bg=COLORS['border']))
        self.concat_btn.bind('<Leave>', lambda e: self.concat_btn.configure(bg=COLORS['bg_secondary']))
        self.concat_cancel_btn = tk.Button(concat_btn_row, text="取消", font=("Microsoft YaHei UI", 12),
            command=self.cancel_concat, state=tk.DISABLED, bg=COLORS['bg_secondary'], fg=COLORS['text_primary'])
        self.concat_cancel_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        tk.Label(
            action_frame,
            text="提示：至少选择2个视频才能拼接",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS['text_secondary'],
            bg=COLORS['bg_card'],
            wraplength=400
        ).pack(pady=(8, 0))
        
        # 结果显示标签
        self.concat_result_label = tk.Label(
            action_frame,
            text="",
            font=("Microsoft YaHei UI", 11),
            fg='#27ae60',
            bg=COLORS['bg_card']
        )
        self.concat_result_label.pack(pady=(8, 0))
        
        return tab
    def create_rename_tab(self) -> tk.Frame:
        tab = tk.Frame(self.content_frame, bg=COLORS['bg_primary'])
        folder_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=12, pady=12)
        folder_frame.pack(fill=tk.X, pady=(12, 0), padx=16)
        tk.Label(
            folder_frame,
            text="选择要重命名的文件夹：",
            font=("Microsoft YaHei UI", 12),
            fg=COLORS['text_primary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W)
        path_frame = tk.Frame(folder_frame, bg=COLORS['bg_card'])
        path_frame.pack(fill=tk.X, pady=(8, 0))
        self.rename_path_entry = tk.Entry(
            path_frame,
            textvariable=self.rename_folder_path,
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary']
        )
        self.rename_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        browse_rename_btn = tk.Button(
            path_frame,
            text="浏览",
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            activebackground=COLORS['border'],
            activeforeground=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=self.browse_rename_folder
        )
        browse_rename_btn.pack(side=tk.LEFT)
        browse_rename_btn.bind('<Enter>', lambda e: browse_rename_btn.configure(bg=COLORS['border']))
        browse_rename_btn.bind('<Leave>', lambda e: browse_rename_btn.configure(bg=COLORS['bg_secondary']))
        preview_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=8, pady=8)
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0), padx=16)
        tk.Label(
            preview_frame,
            text="重命名预览（原名 → 新名）",
            font=("Microsoft YaHei UI", 8, "bold"),
            fg=COLORS['text_primary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W)
        
        # 两列布局容器
        content_container = tk.Frame(preview_frame, bg=COLORS['bg_card'])
        content_container.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        
        # 左侧：表格
        left_frame = tk.Frame(content_container, bg=COLORS['bg_card'], width=550)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, expand=False, padx=(0, 20))
        left_frame.pack_propagate(False)
        
        # 使用Treeview实现表格
        columns = ('seq', 'old_name', 'arrow', 'new_name')
        self.rename_tree = ttk.Treeview(left_frame, columns=columns, show='headings', height=25)
        self.rename_tree.heading('seq', text='序号')
        self.rename_tree.heading('old_name', text='原名称')
        self.rename_tree.heading('arrow', text='→')
        self.rename_tree.heading('new_name', text='新名称')
        self.rename_tree.column('seq', width=50, minwidth=50, anchor='center', stretch=False)
        self.rename_tree.column('old_name', width=220, minwidth=220, anchor='w', stretch=False)
        self.rename_tree.column('arrow', width=30, minwidth=30, anchor='center', stretch=False)
        self.rename_tree.column('new_name', width=200, minwidth=200, anchor='w', stretch=False)
        self.rename_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 滚动条
        preview_scroll = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.rename_tree.yview)
        preview_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.rename_tree.configure(yscrollcommand=preview_scroll.set)
        self.rename_preview_text = self.rename_tree
        
        # 右侧：控制选项（横向排列）
        right_frame = tk.Frame(content_container, bg=COLORS['bg_card'])
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        # 显示数量 - 横向
        display_count_row = tk.Frame(right_frame, bg=COLORS['bg_card'])
        display_count_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(display_count_row, text="显示数量:", font=("Microsoft YaHei UI", 9, "bold"),
            fg=COLORS['text_primary'], bg=COLORS['bg_card'], width=10, anchor='w').pack(side=tk.LEFT)
        self.rename_display_count = tk.IntVar(value=50)
        for val, label in [(50, "前50个"), (100, "前100个"), (200, "前200个")]:
            tk.Radiobutton(display_count_row, text=label, variable=self.rename_display_count,
                value=val, font=("Microsoft YaHei UI", 8), fg=COLORS['text_primary'],
                bg=COLORS['bg_card'], indicatoron=1,
                command=self.on_rename_count_change).pack(side=tk.LEFT, padx=4)
        
        # 文件类型 - 横向
        file_type_row = tk.Frame(right_frame, bg=COLORS['bg_card'])
        file_type_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(file_type_row, text="文件类型:", font=("Microsoft YaHei UI", 9, "bold"),
            fg=COLORS['text_primary'], bg=COLORS['bg_card'], width=10, anchor='w').pack(side=tk.LEFT)
        rename_ext_entry = tk.Entry(file_type_row, textvariable=self.rename_file_extensions,
            font=("Microsoft YaHei UI", 9), width=15,
            bg=COLORS['bg_secondary'], fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary'])
        rename_ext_entry.pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(file_type_row, text="（如.jpg，留空则全部）", font=("Microsoft YaHei UI", 7),
            fg=COLORS['text_secondary'], bg=COLORS['bg_card']).pack(side=tk.LEFT)
        rename_ext_entry.bind('<FocusOut>', lambda e: self.on_rename_count_change())
        rename_ext_entry.bind('<Return>', lambda e: self.on_rename_count_change())
        
        # 文件范围 - 横向
        self.file_range_row = tk.Frame(right_frame, bg=COLORS['bg_card'])
        self.file_range_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(self.file_range_row, text="文件范围:", font=("Microsoft YaHei UI", 9, "bold"),
            fg=COLORS['text_primary'], bg=COLORS['bg_card'], width=10, anchor='w').pack(side=tk.LEFT)
        self.rename_range_mode = tk.StringVar(value="index")
        tk.Radiobutton(self.file_range_row, text="按索引", variable=self.rename_range_mode,
            value="index", font=("Microsoft YaHei UI", 8), fg=COLORS['text_primary'],
            bg=COLORS['bg_card'], indicatoron=1,
            command=self.on_rename_range_mode_change).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(self.file_range_row, text="按文件名", variable=self.rename_range_mode,
            value="name", font=("Microsoft YaHei UI", 8), fg=COLORS['text_primary'],
            bg=COLORS['bg_card'], indicatoron=1,
            command=self.on_rename_range_mode_change).pack(side=tk.LEFT, padx=4)
        
        # 按索引选择的输入框 - 横向（显示在文件范围下方）
        self.index_range_row = tk.Frame(right_frame, bg=COLORS['bg_card'])
        self.index_range_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(self.index_range_row, text="", width=10, bg=COLORS['bg_card']).pack(side=tk.LEFT)
        self.rename_index_range_frame = tk.Frame(self.index_range_row, bg=COLORS['bg_card'])
        self.rename_index_range_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        tk.Label(self.rename_index_range_frame, text="从第", font=("Microsoft YaHei UI", 8),
            fg=COLORS['text_primary'], bg=COLORS['bg_card']).pack(side=tk.LEFT)
        rename_start_idx_entry = tk.Entry(self.rename_index_range_frame, textvariable=self.rename_start_index,
            font=("Microsoft YaHei UI", 8), width=6,
            bg=COLORS['bg_secondary'], fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary'])
        rename_start_idx_entry.pack(side=tk.LEFT, padx=2)
        tk.Label(self.rename_index_range_frame, text="个  到第", font=("Microsoft YaHei UI", 8),
            fg=COLORS['text_primary'], bg=COLORS['bg_card']).pack(side=tk.LEFT)
        rename_end_idx_entry = tk.Entry(self.rename_index_range_frame, textvariable=self.rename_end_index,
            font=("Microsoft YaHei UI", 8), width=6,
            bg=COLORS['bg_secondary'], fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary'])
        rename_end_idx_entry.pack(side=tk.LEFT, padx=2)
        tk.Label(self.rename_index_range_frame, text="个", font=("Microsoft YaHei UI", 8),
            fg=COLORS['text_primary'], bg=COLORS['bg_card']).pack(side=tk.LEFT)
        self.rename_range_hint_label = tk.Label(self.rename_index_range_frame, 
            text="（共0个文件）", font=("Microsoft YaHei UI", 7),
            fg=COLORS['text_secondary'], bg=COLORS['bg_card'])
        self.rename_range_hint_label.pack(side=tk.LEFT, padx=(4, 0))
        
        rename_start_idx_entry.bind('<FocusOut>', lambda e: self.on_rename_count_change())
        rename_end_idx_entry.bind('<FocusOut>', lambda e: self.on_rename_count_change())
        rename_start_idx_entry.bind('<Return>', lambda e: self.on_rename_count_change())
        rename_end_idx_entry.bind('<Return>', lambda e: self.on_rename_count_change())
        
        # 按文件名选择的输入框（初始隐藏，显示在文件范围下方）
        self.name_range_row = tk.Frame(right_frame, bg=COLORS['bg_card'])
        tk.Label(self.name_range_row, text="", width=10, bg=COLORS['bg_card']).pack(side=tk.LEFT)
        self.rename_name_range_frame = tk.Frame(self.name_range_row, bg=COLORS['bg_card'])
        self.rename_name_range_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        tk.Label(self.rename_name_range_frame, text="起始:", font=("Microsoft YaHei UI", 8),
            fg=COLORS['text_primary'], bg=COLORS['bg_card']).pack(side=tk.LEFT)
        self.rename_start_filename = tk.StringVar()
        rename_start_name_entry = tk.Entry(self.rename_name_range_frame, textvariable=self.rename_start_filename,
            font=("Microsoft YaHei UI", 8), width=12,
            bg=COLORS['bg_secondary'], fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary'])
        rename_start_name_entry.pack(side=tk.LEFT, padx=(2, 8))
        
        tk.Label(self.rename_name_range_frame, text="结束:", font=("Microsoft YaHei UI", 8),
            fg=COLORS['text_primary'], bg=COLORS['bg_card']).pack(side=tk.LEFT)
        self.rename_end_filename = tk.StringVar()
        rename_end_name_entry = tk.Entry(self.rename_name_range_frame, textvariable=self.rename_end_filename,
            font=("Microsoft YaHei UI", 8), width=12,
            bg=COLORS['bg_secondary'], fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary'])
        rename_end_name_entry.pack(side=tk.LEFT, padx=2)
        
        tk.Label(self.rename_name_range_frame, text="（留空则全选）", font=("Microsoft YaHei UI", 7),
            fg=COLORS['text_secondary'], bg=COLORS['bg_card']).pack(side=tk.LEFT, padx=(4, 0))
        
        rename_start_name_entry.bind('<FocusOut>', lambda e: self.on_rename_count_change())
        rename_end_name_entry.bind('<FocusOut>', lambda e: self.on_rename_count_change())
        rename_start_name_entry.bind('<Return>', lambda e: self.on_rename_count_change())
        rename_end_name_entry.bind('<Return>', lambda e: self.on_rename_count_change())
        
        # 编号位数 - 横向
        digit_count_row = tk.Frame(right_frame, bg=COLORS['bg_card'])
        digit_count_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(digit_count_row, text="编号位数:", font=("Microsoft YaHei UI", 9, "bold"),
            fg=COLORS['text_primary'], bg=COLORS['bg_card'], width=10, anchor='w').pack(side=tk.LEFT)
        for val, label in [(1, "1位"), (2, "2位"), (3, "3位"), (4, "4位"), (5, "5位")]:
            tk.Radiobutton(digit_count_row, text=label, variable=self.rename_digit_count,
                value=val, font=("Microsoft YaHei UI", 8), fg=COLORS['text_primary'],
                bg=COLORS['bg_card'], indicatoron=1,
                command=self.on_rename_count_change).pack(side=tk.LEFT, padx=4)
        
        # 起始序号 - 横向
        start_number_row = tk.Frame(right_frame, bg=COLORS['bg_card'])
        start_number_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(start_number_row, text="起始序号:", font=("Microsoft YaHei UI", 9, "bold"),
            fg=COLORS['text_primary'], bg=COLORS['bg_card'], width=10, anchor='w').pack(side=tk.LEFT)
        rename_start_num_entry = tk.Entry(start_number_row, textvariable=self.rename_start_number,
            font=("Microsoft YaHei UI", 9), width=10,
            bg=COLORS['bg_secondary'], fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary'])
        rename_start_num_entry.pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(start_number_row, text="（第一个文件的序号）", font=("Microsoft YaHei UI", 7),
            fg=COLORS['text_secondary'], bg=COLORS['bg_card']).pack(side=tk.LEFT)
        rename_start_num_entry.bind('<FocusOut>', lambda e: self.on_rename_count_change())
        rename_start_num_entry.bind('<Return>', lambda e: self.on_rename_count_change())
        
        # 输出设置卡片
        settings_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=12, pady=8)
        settings_frame.pack(fill=tk.X, pady=(12, 0), padx=16)
        tk.Label(
            settings_frame,
            text="输出设置",
            font=("Microsoft YaHei UI", 12, "bold"),
            fg=COLORS['text_primary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W, pady=(0, 8))
        tk.Label(
            settings_frame,
            text="输出文件夹：",
            font=("Microsoft YaHei UI", 10),
            fg=COLORS['text_secondary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W)
        output_folder_frame = tk.Frame(settings_frame, bg=COLORS['bg_card'])
        output_folder_frame.pack(fill=tk.X, pady=(4, 0))
        self.rename_output_entry = tk.Entry(
            output_folder_frame,
            textvariable=self.rename_output_folder,
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary']
        )
        self.rename_output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        select_rename_btn = tk.Button(
            output_folder_frame,
            text="选择",
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            activebackground=COLORS['border'],
            activeforeground=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=self.browse_rename_output_folder
        )
        select_rename_btn.pack(side=tk.LEFT)
        select_rename_btn.bind('<Enter>', lambda e: select_rename_btn.configure(bg=COLORS['border']))
        select_rename_btn.bind('<Leave>', lambda e: select_rename_btn.configure(bg=COLORS['bg_secondary']))
        tk.Label(
            settings_frame,
            text="（留空则在原文件夹操作）",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS['text_secondary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W)
        
        # 操作卡片
        action_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=12, pady=8)
        action_frame.pack(fill=tk.X, pady=(12, 0), padx=16)
        tk.Label(
            action_frame,
            text="操作",
            font=("Microsoft YaHei UI", 12, "bold"),
            fg=COLORS['text_primary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W, pady=(0, 6))
        
        # 进度条
        self.rename_progress_var = tk.DoubleVar()
        self.rename_progress_bar = ttk.Progressbar(
            action_frame,
            variable=self.rename_progress_var,
            maximum=100,
            mode='determinate'
        )
        self.rename_progress_bar.pack(fill=tk.X, pady=(0, 2))
        self.rename_progress_label = tk.Label(
            action_frame,
            text="",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS['text_secondary'],
            bg=COLORS['bg_card'],
            anchor=tk.W
        )
        self.rename_progress_label.pack(fill=tk.X, pady=(0, 2))
        
        # 按钮行
        rename_btn_row = tk.Frame(action_frame, bg=COLORS['bg_card'])
        rename_btn_row.pack(fill=tk.X)
        self.rename_btn = tk.Button(
            rename_btn_row,
            text="开始重命名",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            activebackground=COLORS['border'],
            activeforeground=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=self.start_rename
        )
        self.rename_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.rename_btn.config(state=tk.DISABLED)
        self.rename_btn.bind('<Enter>', lambda e: self.rename_btn.configure(bg=COLORS['border']))
        self.rename_btn.bind('<Leave>', lambda e: self.rename_btn.configure(bg=COLORS['bg_secondary']))
        self.rename_cancel_btn = tk.Button(rename_btn_row, text="取消", font=("Microsoft YaHei UI", 12),
            command=self.cancel_rename, state=tk.DISABLED, bg=COLORS['bg_secondary'], fg=COLORS['text_primary'])
        self.rename_cancel_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        tk.Label(
            action_frame,
            text="提示：文件将按数字从小到大排序，根据设置的范围和起始序号重命名",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS['text_secondary'],
            bg=COLORS['bg_card'],
            wraplength=400
        ).pack(pady=(8, 0))
        
        # 结果显示标签
        self.rename_result_label = tk.Label(
            action_frame,
            text="",
            font=("Microsoft YaHei UI", 11),
            fg='#27ae60',
            bg=COLORS['bg_card']
        )
        self.rename_result_label.pack(pady=(12, 0))
        
        return tab
    def log_message(self, message: str):
        self.concat_result_label.config(text=message)
    
    def rename_log(self, message: str):
        self.rename_result_label.config(text=message)
    
    def clean_log(self, message: str):
        self.clean_result_label.config(text=message)
    def browse_video_folder(self):
        folder = filedialog.askdirectory(title="选择包含视频的文件夹", initialdir=os.path.expanduser("~"))
        if folder:
            self.video_folder_path.set(folder)
            self.video_output_folder.set(folder)
            self.load_video_files()
    def browse_video_output_folder(self):
        folder = filedialog.askdirectory(title="选择输出文件夹", initialdir=os.path.expanduser("~"))
        if folder:
            self.video_output_folder.set(folder)
    def browse_rename_folder(self):
        folder = filedialog.askdirectory(title="选择要重命名的文件夹", initialdir=os.path.expanduser("~"))
        if folder:
            self.rename_folder_path.set(folder)
            self.rename_output_folder.set(folder)
            self.load_rename_preview()
    def browse_rename_output_folder(self):
        folder = filedialog.askdirectory(title="选择输出文件夹", initialdir=os.path.expanduser("~"))
        if folder:
            self.rename_output_folder.set(folder)
    def load_video_files(self):
        folder = self.video_folder_path.get()
        if not folder or not os.path.isdir(folder):
            self.log_message("请选择有效的文件夹")
            return
        self.video_files = []
        self.selected_videos = []
        try:
            files = os.listdir(folder)
            video_files = [f for f in files if is_video(f)]
            if not video_files:
                self.log_message("文件夹中没有视频文件")
                return
            video_files.sort()
            self.video_files = [os.path.join(folder, f) for f in video_files]
            self.video_listbox.delete(0, tk.END)
            for vf in self.video_files:
                filename = os.path.basename(vf)
                size = get_file_size(vf)
                self.video_listbox.insert(tk.END, f"[ ] {filename}  ({size})")
            self.log_message(f"找到 {len(self.video_files)} 个视频文件")
        except Exception as e:
            self.log_message(f"加载失败: {str(e)}")
    def on_video_select(self, event):
        self.update_selected_videos()
        self.concat_btn.config(state=tk.NORMAL if len(self.selected_videos) >= 2 else tk.DISABLED)
    def on_video_click(self, event):
        import tkinter.font as tkfont
        listbox = event.widget
        y = event.y
        font = tkfont.Font(font=listbox.cget('font'))
        line_height = font.metrics('linespace')
        index = int(y / line_height)
        if 0 <= index < listbox.size():
            item = listbox.get(index)
            if item.startswith("[ ]"):
                listbox.delete(index)
                listbox.insert(index, "[√]" + item[3:])
            elif item.startswith("[√]"):
                listbox.delete(index)
                listbox.insert(index, "[ ]" + item[3:])
            self.update_selected_videos()
            self.concat_btn.config(state=tk.NORMAL if len(self.selected_videos) >= 2 else tk.DISABLED)
        listbox.selection_clear(0, tk.END)
    def update_selected_videos(self):
        self.selected_videos = []
        for idx in range(self.video_listbox.size()):
            item = self.video_listbox.get(idx)
            if item.startswith("[√]"):
                self.selected_videos.append(self.video_files[idx])
    def select_all_videos(self):
        for i in range(self.video_listbox.size()):
            item = self.video_listbox.get(i)
            self.video_listbox.delete(i)
            self.video_listbox.insert(i, "[√]" + item[3:])
        self.update_selected_videos()
        if len(self.selected_videos) >= 2:
            self.concat_btn.config(state=tk.NORMAL)
        self.log_message(f"已选择 {len(self.selected_videos)} 个视频")
    def deselect_all_videos(self):
        for i in range(self.video_listbox.size()):
            item = self.video_listbox.get(i)
            self.video_listbox.delete(i)
            self.video_listbox.insert(i, "[ ]" + item[3:])
        self.selected_videos = []
        self.concat_btn.config(state=tk.DISABLED)
    def move_video(self, direction: int):
        selection = self.video_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= self.video_listbox.size():
            return
        items = list(self.video_listbox.get(0, tk.END))
        items[idx], items[new_idx] = items[new_idx], items[idx]
        self.video_files[idx], self.video_files[new_idx] = self.video_files[new_idx], self.video_files[idx]
        self.video_listbox.delete(0, tk.END)
        for item in items:
            self.video_listbox.insert(tk.END, item)
        self.video_listbox.selection_set(new_idx)
        if items[idx].startswith("[√]"):
            self.selected_videos.append(self.video_files[idx])
        if items[new_idx].startswith("[√]"):
            self.selected_videos.append(self.video_files[new_idx])
    def start_concat(self):
        if len(self.selected_videos) < 2:
            messagebox.showwarning("提示", "请至少选择2个视频文件")
            return
        output_folder = self.video_output_folder.get().strip()
        if not output_folder:
            output_folder = self.video_folder_path.get()
        if not os.path.isdir(output_folder):
            messagebox.showerror("错误", "输出文件夹无效")
            return
        output_name = self.output_name_entry.get().strip()
        if not output_name:
            output_name = "output.mp4"
        output_path = os.path.join(output_folder, output_name)
        self.is_processing = True
        self.concat_btn.config(state=tk.DISABLED, text="拼接中...")
        self.concat_cancel_btn.config(state=tk.NORMAL)
        self.concat_progress.set(0)
        self.log_message(f"开始拼接 {len(self.selected_videos)} 个视频...")
        self.log_message(f"输出到: {output_path}")
        self.concat_cancel_event = threading.Event()
        self.concat_process_ref = {}
        def run_in_thread():
            run_ffmpeg_concat(
                self.selected_videos,
                output_path,
                progress_callback=lambda msg: self.root.after(0, lambda m=msg: self._update_concat_progress(m)),
                result_callback=lambda success: self.root.after(0, lambda s=success: self.on_concat_complete(s, output_path)),
                cancel_event=self.concat_cancel_event,
                process_ref=self.concat_process_ref
            )
        threading.Thread(target=run_in_thread, daemon=True).start()
    
    def _update_concat_progress(self, msg):
        self.log_message(msg)
        if "进度:" in msg:
            try:
                pct = int(msg.split("进度:")[1].replace("%", "").strip())
                self.concat_progress.set(pct)
            except:
                pass
        elif "完成" in msg:
            self.concat_progress.set(100)
        elif "取消" in msg or "错误" in msg:
            self.concat_progress.set(0)
    
    def cancel_concat(self):
        self._concat_cancelled = True
        if hasattr(self, 'concat_cancel_event') and self.concat_cancel_event:
            self.concat_cancel_event.set()
        if hasattr(self, 'concat_process_ref') and 'process' in self.concat_process_ref:
            try:
                self.concat_process_ref['process'].terminate()
            except:
                pass
        self.is_processing = False
        self.concat_progress.set(0)
        self.concat_result_label.config(text="拼接已取消", fg='#e74c3c')
        self.concat_btn.config(state=tk.NORMAL, text="开始拼接")
        self.concat_cancel_btn.config(state=tk.DISABLED)
    
    def on_concat_complete(self, success: bool, output_path: str):
        self.is_processing = False
        self.concat_btn.config(state=tk.NORMAL, text="开始拼接")
        self.concat_cancel_btn.config(state=tk.DISABLED)
        if success:
            self.concat_progress.set(100)
            self.concat_result_label.config(text=f"拼接完成！已保存到: {os.path.basename(output_path)}", fg='#27ae60')
            messagebox.showinfo("完成", f"视频拼接完成！\n输出文件: {output_path}")
        else:
            # Check if it was cancelled (don't show error dialog for cancellation)
            if hasattr(self, '_concat_cancelled') and self._concat_cancelled:
                self._concat_cancelled = False
            else:
                self.concat_progress.set(0)
                self.concat_result_label.config(text="拼接失败，请检查视频文件是否正常", fg='#e74c3c')
    def load_rename_preview(self):
        folder = self.rename_folder_path.get()
        if not folder or not os.path.isdir(folder):
            self.rename_log("请选择有效的文件夹")
            return
        # 清空表格
        for item in self.rename_tree.get_children():
            self.rename_tree.delete(item)
        try:
            all_files = [f for f in Path(folder).iterdir() if f.is_file()]
            
            # 文件扩展名过滤
            ext_filter = self.rename_file_extensions.get().strip()
            if ext_filter:
                # 只支持单个文件类型
                ext = ext_filter.lower()
                if not ext.startswith('.'):
                    ext = f'.{ext}'
                all_files = [f for f in all_files if f.suffix.lower() == ext]
            
            self.rename_total_files = len(all_files)
            if not all_files:
                self.rename_tree.insert('', 0, values=('', '提示', '→', '文件夹中没有文件'))
                self.rename_btn.config(state=tk.DISABLED)
                return
            
            # 更新文件范围的默认值和提示文字
            self.rename_end_index.set(self.rename_total_files)
            self.rename_range_hint_label.config(text=f"个（共{self.rename_total_files}个文件）")
            
            # 自然排序（按文件名中的数字顺序）
            import re
            def natural_sort_key(path):
                name = path.name.lower()
                parts = re.split(r'(\d+)', name)
                return [(int(p) if p.isdigit() else p) for p in parts]
            
            all_files_sorted = sorted(all_files, key=natural_sort_key)
            
            # 根据选择模式获取文件范围
            mode = self.rename_range_mode.get()
            if mode == "index":
                # 按索引选择
                start_idx = max(1, self.rename_start_index.get())
                end_idx = self.rename_end_index.get()
                if end_idx <= 0 or end_idx > len(all_files_sorted):
                    end_idx = len(all_files_sorted)
                start_idx = min(start_idx, end_idx)
                selected_files = all_files_sorted[start_idx-1:end_idx]
            else:
                # 按文件名选择
                start_name = self.rename_start_filename.get().strip()
                end_name = self.rename_end_filename.get().strip()
                
                if not start_name and not end_name:
                    # 两个都为空，选择全部
                    selected_files = all_files_sorted
                else:
                    # 找到起始和结束文件的索引
                    start_idx = 0
                    end_idx = len(all_files_sorted)
                    
                    if start_name:
                        for i, f in enumerate(all_files_sorted):
                            if f.name == start_name:
                                start_idx = i
                                break
                    
                    if end_name:
                        for i, f in enumerate(all_files_sorted):
                            if f.name == end_name:
                                end_idx = i + 1
                                break
                    
                    selected_files = all_files_sorted[start_idx:end_idx]
            
            display_count = self.rename_display_count.get()
            # 显示文件预览
            self.rename_files_list = selected_files[:display_count]
            digit_count = self.rename_digit_count.get()
            start_number = self.rename_start_number.get()
            
            for i, f in enumerate(self.rename_files_list):
                seq = start_number + i
                old_name = f.name
                suffix = f.suffix
                new_name = f"{str(seq).zfill(digit_count)}{suffix}"
                self.rename_tree.insert('', 'end', values=(i+1, old_name, '→', new_name))
            self.rename_btn.config(state=tk.NORMAL)
        except Exception as e:
            self.rename_log(f"加载失败: {str(e)}")
            self.rename_btn.config(state=tk.DISABLED)
    
    def on_rename_range_mode_change(self):
        """切换文件范围选择模式"""
        mode = self.rename_range_mode.get()
        if mode == "index":
            self.name_range_row.pack_forget()
            self.index_range_row.pack(fill=tk.X, pady=(0, 6), after=self.file_range_row)
        else:
            self.index_range_row.pack_forget()
            self.name_range_row.pack(fill=tk.X, pady=(0, 6), after=self.file_range_row)
        # 重置表格列宽，确保宽度不变
        self.rename_tree.column('seq', width=50, anchor='center')
        self.rename_tree.column('old_name', width=220, anchor='w')
        self.rename_tree.column('arrow', width=30, anchor='center')
        self.rename_tree.column('new_name', width=200, anchor='w')
        self.on_rename_count_change()
    
    def on_rename_count_change(self):
        folder = self.rename_folder_path.get()
        if not folder or not os.path.isdir(folder):
            return
        display_count = self.rename_display_count.get()
        # 清空并重新加载
        for item in self.rename_tree.get_children():
            self.rename_tree.delete(item)
        try:
            all_files = [f for f in Path(folder).iterdir() if f.is_file()]
            
            # 文件扩展名过滤
            ext_filter = self.rename_file_extensions.get().strip()
            if ext_filter:
                # 只支持单个文件类型
                ext = ext_filter.lower()
                if not ext.startswith('.'):
                    ext = f'.{ext}'
                all_files = [f for f in all_files if f.suffix.lower() == ext]
            
            # 更新文件总数和范围提示
            self.rename_total_files = len(all_files)
            if not all_files:
                self.rename_tree.insert('', 0, values=('', '提示', '→', f'文件夹中没有{ext_filter}文件'))
                self.rename_btn.config(state=tk.DISABLED)
                self.rename_range_hint_label.config(text=f"个（共0个文件）")
                return
            
            # 更新文件范围提示
            self.rename_range_hint_label.config(text=f"个（共{self.rename_total_files}个文件）")
            
            # 自然排序
            import re
            def natural_sort_key(path):
                name = path.name.lower()
                parts = re.split(r'(\d+)', name)
                return [(int(p) if p.isdigit() else p) for p in parts]
            all_files_sorted = sorted(all_files, key=natural_sort_key)
            
            # 根据选择模式获取文件范围
            mode = self.rename_range_mode.get()
            if mode == "index":
                # 按索引选择
                start_idx = max(1, self.rename_start_index.get())
                end_idx = self.rename_end_index.get()
                if end_idx <= 0 or end_idx > len(all_files_sorted):
                    end_idx = len(all_files_sorted)
                start_idx = min(start_idx, end_idx)
                selected_files = all_files_sorted[start_idx-1:end_idx]
            else:
                # 按文件名选择
                start_name = self.rename_start_filename.get().strip()
                end_name = self.rename_end_filename.get().strip()
                
                if not start_name and not end_name:
                    # 两个都为空，选择全部
                    selected_files = all_files_sorted
                else:
                    # 找到起始和结束文件的索引
                    start_idx = 0
                    end_idx = len(all_files_sorted)
                    
                    if start_name:
                        for i, f in enumerate(all_files_sorted):
                            if f.name == start_name:
                                start_idx = i
                                break
                    
                    if end_name:
                        for i, f in enumerate(all_files_sorted):
                            if f.name == end_name:
                                end_idx = i + 1
                                break
                    
                    selected_files = all_files_sorted[start_idx:end_idx]
            
            self.rename_files_list = selected_files[:display_count]
            digit_count = self.rename_digit_count.get()
            start_number = self.rename_start_number.get()
            
            for i, f in enumerate(self.rename_files_list):
                seq = start_number + i
                old_name = f.name
                suffix = f.suffix
                new_name = f"{str(seq).zfill(digit_count)}{suffix}"
                self.rename_tree.insert('', 'end', values=(i+1, old_name, '→', new_name))
            
            self.rename_btn.config(state=tk.NORMAL)
        except Exception as e:
            self.rename_log(f"更新预览失败: {str(e)}")
            self.rename_btn.config(state=tk.DISABLED)
    
    def _add_preview_placeholder(self, text: str):
        for item in self.rename_tree.get_children():
            self.rename_tree.delete(item)
        self.rename_tree.insert('', 0, values=('', text, '', ''))
    
    def _append_preview_row(self, seq: int, old_name: str, new_name: str, status: str = 'ok'):
        self.rename_tree.insert('', 'end', values=(seq, old_name, '→', new_name))
    def _on_rename_progress(self, payload):
        stage, seq, total, old_name, new_name = payload
        if total > 0:
            pct = int((seq / total) * 100)
            self.rename_progress_var.set(pct)
            self.rename_progress_label.config(text=f"正在处理: {old_name} → {new_name} ({seq}/{total})")
        if stage == 'empty':
            msg = new_name or "文件夹中没有可处理的文件"
            self.rename_log(msg)
            self.rename_progress_label.config(text=msg)
            return
        if stage == 'phase1':
            return
        if stage == 'phase1_error':
            self.rename_log(f"临时重命名失败: {old_name}, {new_name}")
            return
        if stage == 'phase2':
            self._append_preview_row(seq, old_name, new_name, status='ok')
            return
        if stage == 'phase2_error':
            self._append_preview_row(seq, old_name, f"错误: {new_name}", status='error')
            return
        if stage == 'skipped':
            self._append_preview_row(seq, old_name, new_name, status='skipped')
            return
        if stage == 'info':
            self.rename_log(new_name)
            return
        if stage == 'error':
            self.rename_log(new_name)
            self._append_preview_row(1, '—', f"错误: {new_name}", status='error')
            return
    def start_rename(self):
        folder = self.rename_folder_path.get()
        if not folder:
            return
        output_folder = self.rename_output_folder.get().strip()
        if not messagebox.askyesno("确认", "确定要重命名这些文件吗？"):
            return
        self._preview_has_data = False
        # Clear table and reset progress
        for item in self.rename_tree.get_children():
            self.rename_tree.delete(item)
        self.rename_progress_var.set(0)
        self.rename_progress_label.config(text="正在处理...")
        self.is_processing = True
        self.rename_btn.config(state=tk.DISABLED, text="重命名中...")
        self.rename_cancel_btn.config(state=tk.NORMAL)
        self.rename_cancel_event = threading.Event()
        def run_in_thread():
            rename_files_by_number(
                folder,
                output_folder if output_folder else None,
                digit_count=self.rename_digit_count.get(),
                start_index=self.rename_start_index.get(),
                end_index=self.rename_end_index.get(),
                start_number=self.rename_start_number.get(),
                file_extensions=self.rename_file_extensions.get(),
                progress_callback=lambda payload: self.root.after(0, lambda: self._on_rename_progress(payload)),
                result_callback=lambda count: self.root.after(0, lambda: self.on_rename_complete(count)),
                cancel_event=self.rename_cancel_event
            )
        threading.Thread(target=run_in_thread, daemon=True).start()
    
    def cancel_rename(self):
        if hasattr(self, 'rename_cancel_event') and self.rename_cancel_event:
            self.rename_cancel_event.set()
        self.is_processing = False
        self.rename_log("重命名已取消")
    
    def on_rename_complete(self, count: int):
        self.is_processing = False
        self.rename_btn.config(state=tk.NORMAL, text="开始重命名")
        self.rename_cancel_btn.config(state=tk.DISABLED)
        self.rename_progress_var.set(100)
        if count > 0:
            self.rename_log(f"成功重命名 {count} 个文件")
            self.rename_progress_label.config(text=f"完成！共处理 {count} 个文件")
            messagebox.showinfo("完成", f"成功重命名 {count} 个文件！")
        else:
            self.rename_log("重命名失败或没有文件需要重命名")
            self.rename_progress_label.config(text="重命名失败")
            if hasattr(self, '_preview_has_data') and not self._preview_has_data:
                self._add_preview_placeholder("没有可重命名的文件")
    def create_frame_tab(self) -> tk.Frame:
        tab = tk.Frame(self.content_frame, bg=COLORS['bg_primary'])
        
        # 顶部：选择视频
        frame_top = tk.Frame(tab, bg=COLORS['bg_primary'])
        frame_top.pack(fill=tk.X, pady=10)
        tk.Button(frame_top, text="1. 选择视频文件", command=self.load_frame_video,
                  font=('Microsoft YaHei UI', 11, 'bold')).pack(side=tk.LEFT, padx=20)
        self.lbl_frame_video = tk.Label(frame_top, text="未选择视频", fg="gray")
        self.lbl_frame_video.pack(side=tk.LEFT)
        
        # 视频预览区域 - 缩小尺寸
        self.canvas_frame = tk.Canvas(tab, width=640, height=320, bg="black")
        self.canvas_frame.pack(pady=5)
        self.scale_frame = tk.Scale(tab, variable=self.frame_scale_var,
                                    orient=tk.HORIZONTAL, length=640, showvalue=0,
                                    command=self.on_frame_slider_move, state=tk.DISABLED)
        self.scale_frame.pack()
        self.scale_frame.bind('<B1-Motion>', self.on_frame_slider_drag)
        
        # 时间显示
        self.lbl_frame_time = tk.Label(tab, text="00:00:00 / 00:00:00", font=('Microsoft YaHei UI', 10, 'bold'), bg=COLORS['bg_primary'])
        self.lbl_frame_time.pack(pady=2)
        
        # 时间段卡片区域
        seg_header = tk.Frame(tab, bg=COLORS['bg_primary'])
        seg_header.pack(fill=tk.X, padx=20, pady=(5, 2))
        tk.Label(seg_header, text="时间段设置:", font=('Microsoft YaHei UI', 11, 'bold'), 
                 bg=COLORS['bg_primary']).pack(side=tk.LEFT)
        tk.Button(seg_header, text="+ 添加时间段", command=self.add_time_segment,
                  font=('Microsoft YaHei UI', 9), bg=COLORS['accent'], fg='white',
                  relief=tk.FLAT, cursor='hand2').pack(side=tk.RIGHT)
        
        # 时间段卡片容器（带滚动条）- 缩小高度
        segments_container = tk.Frame(tab, bg=COLORS['bg_primary'])
        segments_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 5))
        
        self.segments_canvas = tk.Canvas(segments_container, bg=COLORS['bg_primary'], 
                                         highlightthickness=0, height=100)
        self.segments_scrollbar = tk.Scrollbar(segments_container, orient=tk.VERTICAL, 
                                                 command=self.segments_canvas.yview)
        self.segments_canvas.configure(yscrollcommand=self.segments_scrollbar.set)
        self.segments_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.segments_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.segments_rows_frame = tk.Frame(self.segments_canvas, bg=COLORS['bg_primary'])
        self.segments_canvas.create_window((0, 0), window=self.segments_rows_frame, anchor='nw')
        self.segments_rows_frame.bind('<Configure>', 
            lambda e: self.segments_canvas.configure(scrollregion=self.segments_canvas.bbox('all')))
        
        # 底部设置和操作按钮
        frame_bottom = tk.Frame(tab, bg=COLORS['bg_primary'])
        frame_bottom.pack(fill=tk.X, padx=20, pady=5)
        
        tk.Button(frame_bottom, text="2. 选择保存文件夹", command=self.select_frame_folder,
                  font=('Microsoft YaHei UI', 10, 'bold')).grid(row=0, column=0, pady=5, sticky=tk.W)
        self.lbl_frame_folder = tk.Label(frame_bottom, text="未选择文件夹", fg="gray")
        self.lbl_frame_folder.grid(row=0, column=1, columnspan=4, sticky=tk.W, padx=10)
        
        self.progress_frame = ttk.Progressbar(tab, orient=tk.HORIZONTAL, length=600, mode="determinate")
        self.progress_frame.pack(pady=5)
        self.lbl_frame_status = tk.Label(tab, text="")
        self.lbl_frame_status.pack()
        
        # 操作按钮行
        btn_frame = tk.Frame(tab, bg=COLORS['bg_primary'])
        btn_frame.pack(pady=5)
        self.btn_frame_start = tk.Button(btn_frame, text="开始提取图片", command=self.start_frame_processing,
                                        font=('Microsoft YaHei UI', 12, 'bold'), state=tk.DISABLED, width=12)
        self.btn_frame_start.pack(side=tk.LEFT, padx=10)
        self.btn_frame_cancel = tk.Button(btn_frame, text="取消", command=self.cancel_frame_processing,
                                         font=('Microsoft YaHei UI', 12), state=tk.DISABLED, width=8)
        self.btn_frame_cancel.pack(side=tk.LEFT, padx=10)
        self.btn_frame_pause = tk.Button(btn_frame, text="暂停", command=self.pause_frame_processing,
                                        font=('Microsoft YaHei UI', 12), state=tk.DISABLED, width=8)
        self.btn_frame_pause.pack(side=tk.LEFT, padx=10)
        return tab

    def create_clean_tab(self) -> tk.Frame:
        tab = tk.Frame(self.content_frame, bg=COLORS['bg_primary'])

        folder_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=12, pady=12)
        folder_frame.pack(fill=tk.X, pady=(12, 0), padx=16)
        tk.Label(
            folder_frame,
            text="选择要扫描的文件夹：",
            font=("Microsoft YaHei UI", 12),
            fg=COLORS['text_primary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W)

        path_frame = tk.Frame(folder_frame, bg=COLORS['bg_card'])
        path_frame.pack(fill=tk.X, pady=(8, 0))

        self.clean_path_entry = tk.Entry(
            path_frame,
            textvariable=self.clean_folder_path,
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary']
        )
        self.clean_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        browse_clean_btn = tk.Button(
            path_frame,
            text="浏览",
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            activebackground=COLORS['border'],
            activeforeground=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=self.browse_clean_folder
        )
        browse_clean_btn.pack(side=tk.LEFT)
        browse_clean_btn.bind('<Enter>', lambda e: browse_clean_btn.configure(bg=COLORS['border']))
        browse_clean_btn.bind('<Leave>', lambda e: browse_clean_btn.configure(bg=COLORS['bg_secondary']))

        # 扫描说明
        scan_tip_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=12, pady=12)
        scan_tip_frame.pack(fill=tk.X, pady=(12, 0), padx=16)
        tk.Label(
            scan_tip_frame,
            text="说明：查找没有对应 .json 文件的 .jpg 文件",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS['text_secondary'],
            bg=COLORS['bg_card'],
            wraplength=400
        ).pack()

        # 孤立文件列表
        list_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=12, pady=12)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0), padx=16)
        tk.Label(
            list_frame,
            text="孤立文件列表",
            font=("Microsoft YaHei UI", 12, "bold"),
            fg=COLORS['text_primary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W)
        list_container = tk.Frame(list_frame, bg=COLORS['bg_secondary'])
        list_container.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.clean_file_listbox = tk.Listbox(
            list_container,
            font=("Consolas", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            selectbackground=COLORS['accent'],
            selectforeground=COLORS['bg_primary'],
            relief=tk.FLAT,
            highlightthickness=0
        )
        self.clean_file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scroll = tk.Scrollbar(list_container, orient=tk.VERTICAL)
        list_scroll.config(command=self.clean_file_listbox.yview)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.clean_file_listbox.config(yscrollcommand=list_scroll.set)

        # 输出设置卡片
        settings_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=12, pady=12)
        settings_frame.pack(fill=tk.X, pady=(12, 0), padx=16)
        tk.Label(
            settings_frame,
            text="输出设置",
            font=("Microsoft YaHei UI", 12, "bold"),
            fg=COLORS['text_primary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W, pady=(0, 12))
        tk.Label(
            settings_frame,
            text="输出文件夹：",
            font=("Microsoft YaHei UI", 10),
            fg=COLORS['text_secondary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W)
        output_folder_frame = tk.Frame(settings_frame, bg=COLORS['bg_card'])
        output_folder_frame.pack(fill=tk.X, pady=(4, 0))
        self.clean_output_entry = tk.Entry(
            output_folder_frame,
            textvariable=self.clean_output_folder,
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary']
        )
        self.clean_output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        select_clean_output_btn = tk.Button(
            output_folder_frame,
            text="选择",
            font=("Microsoft YaHei UI", 10),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            activebackground=COLORS['border'],
            activeforeground=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=self.browse_clean_output_folder
        )
        select_clean_output_btn.pack(side=tk.LEFT)
        select_clean_output_btn.bind('<Enter>', lambda e: select_clean_output_btn.configure(bg=COLORS['border']))
        select_clean_output_btn.bind('<Leave>', lambda e: select_clean_output_btn.configure(bg=COLORS['bg_secondary']))
        tk.Label(
            settings_frame,
            text="（默认为源文件夹，同源则删除孤立文件）",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS['text_secondary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W)
        
        # 操作卡片
        action_frame = tk.Frame(tab, bg=COLORS['bg_card'], padx=12, pady=12)
        action_frame.pack(fill=tk.X, pady=(12, 0), padx=16)
        tk.Label(
            action_frame,
            text="文件清洗操作",
            font=("Microsoft YaHei UI", 12, "bold"),
            fg=COLORS['text_primary'],
            bg=COLORS['bg_card']
        ).pack(anchor=tk.W, pady=(0, 8))
        
        # 按钮行
        clean_btn_row = tk.Frame(action_frame, bg=COLORS['bg_card'])
        clean_btn_row.pack(fill=tk.X)
        self.clean_process_btn = tk.Button(
            clean_btn_row,
            text="开始清洗",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=COLORS['bg_secondary'],
            fg=COLORS['text_primary'],
            activebackground=COLORS['border'],
            activeforeground=COLORS['text_primary'],
            relief=tk.FLAT,
            cursor='hand2',
            command=self.start_clean_move
        )
        self.clean_process_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.clean_process_btn.config(state=tk.DISABLED)
        self.clean_process_btn.bind('<Enter>', lambda e: self.clean_process_btn.configure(bg=COLORS['border']))
        self.clean_process_btn.bind('<Leave>', lambda e: self.clean_process_btn.configure(bg=COLORS['bg_secondary']))
        self.clean_cancel_btn = tk.Button(clean_btn_row, text="取消", font=("Microsoft YaHei UI", 12),
            command=self.cancel_clean, state=tk.DISABLED, bg=COLORS['bg_secondary'], fg=COLORS['text_primary'])
        self.clean_cancel_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(
            action_frame,
            text="提示：将对扫描到的孤立文件进行清洗",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS['text_secondary'],
            bg=COLORS['bg_card'],
            wraplength=400
        ).pack(pady=(8, 0))

        # 结果显示标签
        self.clean_result_label = tk.Label(
            action_frame,
            text="",
            font=("Microsoft YaHei UI", 11),
            fg='#27ae60',
            bg=COLORS['bg_card']
        )
        self.clean_result_label.pack(pady=(12, 0))

        return tab


    def clean_log(self, message: str):
        self.clean_result_label.config(text=message)

    def browse_clean_folder(self):
        folder = filedialog.askdirectory(title="选择要扫描的文件夹", initialdir=os.path.expanduser("~"))
        if folder:
            self.clean_folder_path.set(folder)
            self.clean_output_folder.set('')
            self.start_clean_scan()

    def browse_clean_output_folder(self):
        folder = filedialog.askdirectory(title="选择输出文件夹", initialdir=os.path.expanduser("~"))
        if folder:
            self.clean_output_folder.set(folder)

    def collect_orphan_jpg(self, root: str):
        orphan_jpgs = []
        for dirpath, _, filenames in os.walk(root):
            json_stems = set()
            for f in filenames:
                if Path(f).suffix.lower() == '.json':
                    json_stems.add(Path(f).stem.lower())
            for f in filenames:
                if Path(f).suffix.lower() == '.jpg':
                    stem = Path(f).stem.lower()
                    if stem not in json_stems:
                        orphan_jpgs.append(os.path.join(dirpath, f))
        return orphan_jpgs

    def start_clean_scan(self):
        folder = self.clean_folder_path.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("提示", "请选择有效的文件夹")
            return

        self.is_processing = True
        self.clean_result_label.config(text="正在扫描...", fg=COLORS['text_secondary'])

        def run_scan():
            try:
                orphan_files = self.collect_orphan_jpg(folder)
                self.root.after(0, lambda: self.on_clean_scan_complete(orphan_files))
            except Exception as e:
                self.root.after(0, lambda: self.on_clean_scan_error(str(e)))

        threading.Thread(target=run_scan, daemon=True).start()

    def on_clean_scan_complete(self, orphan_files):
        self.is_processing = False
        self.clean_orphan_files = orphan_files
        self.clean_file_listbox.delete(0, tk.END)

        if not orphan_files:
            self.clean_result_label.config(text="没有找到孤立文件", fg='#27ae60')
            self.clean_process_btn.config(state=tk.DISABLED)
        else:
            for f in orphan_files:
                self.clean_file_listbox.insert(tk.END, os.path.basename(f))
            self.clean_result_label.config(
                text=f"找到 {len(orphan_files)} 个孤立文件",
                fg='#e74c3c'
            )
            self.clean_process_btn.config(state=tk.NORMAL)

    def on_clean_scan_error(self, error_msg):
        self.is_processing = False
        self.clean_result_label.config(text="扫描出错", fg='#e74c3c')

    def start_clean_move(self):
        if not hasattr(self, 'clean_orphan_files') or not self.clean_orphan_files:
            messagebox.showwarning("提示", "没有找到孤立文件")
            return

        output_folder = self.clean_output_folder.get().strip()
        if not output_folder:
            output_folder = self.clean_folder_path.get()

        source_folder = self.clean_folder_path.get().strip()
        is_same_folder = (os.path.abspath(output_folder) == os.path.abspath(source_folder))
        orphan_count = len(self.clean_orphan_files)

        if is_same_folder:
            confirm_msg = f"输出文件夹与源文件夹相同，将直接删除 {orphan_count} 个孤立文件。\n此操作不可恢复！"
        else:
            confirm_msg = f"确定要将所有非孤立文件复制到:\n{output_folder}\n（孤立文件会被排除）"

        if not messagebox.askyesno("确认", confirm_msg):
            return

        self.is_processing = True
        self.clean_process_btn.config(state=tk.DISABLED, text="清洗中...")
        self.clean_cancel_btn.config(state=tk.NORMAL)
        self.clean_cancel_event = threading.Event()

        def run_move():
            orphan_set = set(os.path.abspath(f) for f in self.clean_orphan_files)
            success_count = 0
            failed_count = 0

            if is_same_folder:
                # 同源：删除孤立文件
                for f in self.clean_orphan_files:
                    if self.clean_cancel_event.is_set():
                        break
                    try:
                        os.remove(f)
                        self.root.after(0, lambda file=f: self.clean_log(f"已删除: {os.path.basename(file)}"))
                        success_count += 1
                    except Exception as e:
                        failed_count += 1
                        self.root.after(0, lambda file=f, err=str(e): self.clean_log(f"删除失败: {os.path.basename(file)} - {err}"))
            else:
                # 不同源：复制非孤立文件
                for dirpath, _, filenames in os.walk(source_folder):
                    if self.clean_cancel_event.is_set():
                        break
                    rel_path = os.path.relpath(dirpath, source_folder)
                    dest_dir = output_folder if rel_path == '.' else os.path.join(output_folder, rel_path)

                    try:
                        os.makedirs(dest_dir, exist_ok=True)
                    except Exception as e:
                        self.root.after(0, lambda d=dest_dir, err=str(e): self.clean_log(f"创建目录失败: {d} - {err}"))
                        continue

                    for f in filenames:
                        if self.clean_cancel_event.is_set():
                            break
                        src_path = os.path.join(dirpath, f)
                        if os.path.abspath(src_path) in orphan_set:
                            continue

                        dest_path = os.path.join(dest_dir, f)
                        counter = 1
                        while os.path.exists(dest_path):
                            name, ext = os.path.splitext(f)
                            dest_path = os.path.join(dest_dir, f"{name}_{counter}{ext}")
                            counter += 1

                        try:
                            shutil.copy2(src_path, dest_path)
                            success_count += 1
                        except Exception as e:
                            failed_count += 1
                            self.root.after(0, lambda file=f, err=str(e): self.clean_log(f"复制失败: {file} - {err}"))

            self.root.after(0, lambda: self.on_clean_move_complete(success_count, failed_count, is_same_folder, orphan_count))

        threading.Thread(target=run_move, daemon=True).start()

    def on_clean_move_complete(self, success_count, failed_count, is_delete, orphan_count):
        self.is_processing = False
        self.clean_process_btn.config(state=tk.NORMAL, text="开始清洗")
        self.clean_cancel_btn.config(state=tk.DISABLED)
        if is_delete:
            self.clean_log(f"操作完成：成功删除 {orphan_count} 个孤立文件，失败 {failed_count} 个")
            messagebox.showinfo("完成", f"文件清洗完成！\n成功删除: {orphan_count} 个\n失败: {failed_count} 个")
            self.clean_result_label.config(text=f"已删除 {orphan_count} 个孤立文件", fg='#27ae60')
        else:
            self.clean_log(f"操作完成：成功复制 {success_count} 个文件到目标文件夹")
            self.clean_result_label.config(text=f"已复制 {success_count} 个文件", fg='#27ae60')
        self.clean_process_btn.config(state=tk.DISABLED)
        self.clean_orphan_files = []
    
    def cancel_clean(self):
        if hasattr(self, 'clean_cancel_event') and self.clean_cancel_event:
            self.clean_cancel_event.set()
        self.is_processing = False
        self.clean_log("清洗已取消")
    
    def on_extract_mode_frame_change(self):
        if self.extract_mode_frame.get() == "fps":
            self.interval_options_frame_tab.grid_remove()
            self.fps_options_frame_tab.grid(row=1, column=0, columnspan=6, sticky=tk.W, pady=5)
        else:
            self.fps_options_frame_tab.grid_remove()
            self.interval_options_frame_tab.grid(row=1, column=0, columnspan=6, sticky=tk.W, pady=5)
    
    def format_time(self, seconds):
        h = int(seconds // 3600)
        m = int(seconds % 3600 // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    
    def create_segment_widget(self, segment: TimeSegment, parent_frame: tk.Frame = None) -> dict:
        if parent_frame is None:
            parent_frame = self.segments_rows_frame
        widgets = {}
        frame = tk.Frame(parent_frame, bg=COLORS['bg_card'], padx=12, pady=8, relief=tk.RIDGE, bd=1)
        
        header = tk.Frame(frame, bg=COLORS['bg_secondary'])
        header.pack(fill=tk.X, pady=(0, 6))
        tk.Label(header, text=f"时间段 {segment.id}", font=("Microsoft YaHei UI", 11, "bold"),
                fg=COLORS['accent'], bg=COLORS['bg_secondary']).pack(side=tk.LEFT)
        btn_del = tk.Button(header, text="×", font=("Arial", 14, "bold"), width=2,
                           bg=COLORS['bg_secondary'], fg=COLORS['text_secondary'],
                           relief=tk.FLAT, cursor='hand2', command=lambda: self.remove_time_segment(segment.id))
        btn_del.pack(side=tk.RIGHT)
        btn_del.bind('<Enter>', lambda e: btn_del.configure(fg='#e74c3c'))
        btn_del.bind('<Leave>', lambda e: btn_del.configure(fg=COLORS['text_secondary']))
        
        content = tk.Frame(frame, bg=COLORS['bg_secondary'], padx=8, pady=6)
        content.pack(fill=tk.X)
        
        time_frame = tk.Frame(content, bg=COLORS['bg_secondary'])
        time_frame.pack(fill=tk.X, pady=4)
        tk.Label(time_frame, text="开始时间:", font=("Microsoft YaHei UI", 10), bg=COLORS['bg_secondary']).pack(side=tk.LEFT)
        entry_start = tk.Entry(time_frame, width=12, justify="center", font=("Consolas", 10))
        entry_start.pack(side=tk.LEFT, padx=6)
        entry_start.insert(0, self.format_time(segment.start_sec))
        entry_start.bind('<FocusOut>', lambda e: self.update_segment_time(segment, 'start', entry_start.get()))
        entry_start.bind('<Return>', lambda e: self.update_segment_time(segment, 'start', entry_start.get()))
        tk.Label(time_frame, text="结束时间:", font=("Microsoft YaHei UI", 10), bg=COLORS['bg_secondary']).pack(side=tk.LEFT, padx=(10, 0))
        entry_end = tk.Entry(time_frame, width=12, justify="center", font=("Consolas", 10))
        entry_end.pack(side=tk.LEFT, padx=6)
        entry_end.insert(0, self.format_time(segment.end_sec))
        entry_end.bind('<FocusOut>', lambda e: self.update_segment_time(segment, 'end', entry_end.get()))
        entry_end.bind('<Return>', lambda e: self.update_segment_time(segment, 'end', entry_end.get()))
        
        mode_row = tk.Frame(content, bg=COLORS['bg_secondary'])
        mode_row.pack(fill=tk.X, pady=6)
        mode_var = tk.StringVar(value=segment.mode)
        tk.Label(mode_row, text="采样模式:", font=("Microsoft YaHei UI", 10), bg=COLORS['bg_secondary']).pack(side=tk.LEFT)
        tk.Radiobutton(mode_row, text="按帧率采样", variable=mode_var, value="fps",
                       font=("Microsoft YaHei UI", 9), bg=COLORS['bg_secondary'], padx=6,
                       command=lambda s=segment, m="fps", v=mode_var: self.on_segment_mode_change(s, m, v)).pack(side=tk.LEFT)
        tk.Radiobutton(mode_row, text="按时间间隔", variable=mode_var, value="interval",
                       font=("Microsoft YaHei UI", 9), bg=COLORS['bg_secondary'], padx=6,
                       command=lambda s=segment, m="interval", v=mode_var: self.on_segment_mode_change(s, m, v)).pack(side=tk.LEFT)
        
        param_frame = tk.Frame(content, bg=COLORS['bg_secondary'])
        param_frame.pack(fill=tk.X, pady=4)
        
        fps_frame = tk.Frame(param_frame, bg=COLORS['bg_secondary'])
        fps_frame.pack(fill=tk.X, pady=2)
        tk.Label(fps_frame, text="每秒采样帧数:", font=("Microsoft YaHei UI", 9), bg=COLORS['bg_secondary']).pack(side=tk.LEFT)
        fps_var = tk.IntVar(value=segment.fps if segment.fps > 0 else 1)
        tk.Radiobutton(fps_frame, text="1帧/秒", variable=fps_var, value=1, font=("Microsoft YaHei UI", 8),
                       bg=COLORS['bg_secondary'], padx=2, command=lambda s=segment, v=1, fv=fps_var: self.on_segment_fps_change(s, v, fv)).pack(side=tk.LEFT)
        tk.Radiobutton(fps_frame, text="5帧/秒", variable=fps_var, value=5, font=("Microsoft YaHei UI", 8),
                       bg=COLORS['bg_secondary'], padx=2, command=lambda s=segment, v=5, fv=fps_var: self.on_segment_fps_change(s, v, fv)).pack(side=tk.LEFT)
        tk.Radiobutton(fps_frame, text="自定义", variable=fps_var, value=-1, font=("Microsoft YaHei UI", 8),
                       bg=COLORS['bg_secondary'], padx=2, command=lambda s=segment, v=-1, fv=fps_var: self.on_segment_fps_change(s, v, fv)).pack(side=tk.LEFT)
        custom_fps = tk.IntVar(value=segment.fps if segment.fps > 0 else 10)
        custom_fps_entry = tk.Entry(fps_frame, width=4, justify="center", font=("Consolas", 9))
        custom_fps_entry.pack(side=tk.LEFT, padx=2)
        custom_fps_entry.insert(0, str(segment.fps if segment.fps > 0 else 10))
        custom_fps_entry.bind('<FocusOut>', lambda e: self.on_segment_custom_fps_change(segment, custom_fps_entry, fps_var))
        custom_fps_entry.bind('<Return>', lambda e: self.on_segment_custom_fps_change(segment, custom_fps_entry, fps_var))
        tk.Label(fps_frame, text="帧", font=("Microsoft YaHei UI", 8), bg=COLORS['bg_secondary']).pack(side=tk.LEFT)
        
        interval_frame = tk.Frame(param_frame, bg=COLORS['bg_secondary'])
        interval_frame.pack(fill=tk.X, pady=2)
        tk.Label(interval_frame, text="每帧间隔秒数:", font=("Microsoft YaHei UI", 9), bg=COLORS['bg_secondary']).pack(side=tk.LEFT)
        interval_var = tk.IntVar(value=segment.interval if segment.interval > 0 else 5)
        tk.Radiobutton(interval_frame, text="5秒/帧", variable=interval_var, value=5, font=("Microsoft YaHei UI", 8),
                       bg=COLORS['bg_secondary'], padx=2, command=lambda s=segment, v=5, iv=interval_var: self.on_segment_interval_change(s, v, iv)).pack(side=tk.LEFT)
        tk.Radiobutton(interval_frame, text="自定义", variable=interval_var, value=-1, font=("Microsoft YaHei UI", 8),
                       bg=COLORS['bg_secondary'], padx=2, command=lambda s=segment, v=-1, iv=interval_var: self.on_segment_interval_change(s, v, iv)).pack(side=tk.LEFT)
        custom_interval = tk.IntVar(value=segment.interval if segment.interval > 0 else 10)
        custom_interval_entry = tk.Entry(interval_frame, width=4, justify="center", font=("Consolas", 9))
        custom_interval_entry.pack(side=tk.LEFT, padx=2)
        custom_interval_entry.insert(0, str(segment.interval if segment.interval > 0 else 10))
        custom_interval_entry.bind('<FocusOut>', lambda e: self.on_segment_custom_interval_change(segment, custom_interval_entry, interval_var))
        custom_interval_entry.bind('<Return>', lambda e: self.on_segment_custom_interval_change(segment, custom_interval_entry, interval_var))
        tk.Label(interval_frame, text="秒", font=("Microsoft YaHei UI", 8), bg=COLORS['bg_secondary']).pack(side=tk.LEFT)
        
        widgets['frame'] = frame
        widgets['entry_start'] = entry_start
        widgets['entry_end'] = entry_end
        widgets['mode_var'] = mode_var
        widgets['fps_var'] = fps_var
        widgets['interval_var'] = interval_var
        widgets['custom_fps_entry'] = custom_fps_entry
        widgets['custom_interval_entry'] = custom_interval_entry
        widgets['fps_frame'] = fps_frame
        widgets['interval_frame'] = interval_frame
        
        if segment.mode == 'fps':
            interval_frame.pack_forget()
        else:
            fps_frame.pack_forget()
        
        return widgets
    
    def update_segment_time(self, segment, which, time_str):
        try:
            parts = time_str.replace('：', ':').split(':')
            if len(parts) == 3:
                seconds = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                seconds = float(parts[0]) * 60 + float(parts[1])
            else:
                seconds = float(parts[0])
            if seconds >= 0:
                if which == 'start':
                    segment.start_sec = seconds
                else:
                    segment.end_sec = seconds
        except:
            pass
    
    def on_segment_mode_change(self, segment: TimeSegment, mode: str, mode_var):
        segment.mode = mode
        mode_var.set(mode)
        widgets = self.frame_segment_widgets.get(segment.id, {})
        if mode == 'fps':
            widgets.get('interval_frame', tk.Frame()).pack_forget()
            widgets.get('fps_frame', tk.Frame()).pack(fill=tk.X)
        else:
            widgets.get('fps_frame', tk.Frame()).pack_forget()
            widgets.get('interval_frame', tk.Frame()).pack(fill=tk.X)
    
    def on_segment_fps_change(self, segment: TimeSegment, value: int, fps_var):
        segment.fps = value
        fps_var.set(value)
    
    def on_segment_custom_fps_change(self, segment, entry, fps_var):
        try:
            val = int(entry.get())
            if 1 <= val <= 30:
                segment.fps = val
                fps_var.set(val)
        except:
            pass
    
    def on_segment_interval_change(self, segment: TimeSegment, value: int, interval_var):
        # value 只能是 5 或 -1；-1 表示"自定义"，不修改 segment.interval
        if value == 5:
            segment.interval = 5
        # value == -1 时保留 segment.interval 的自定义值
        interval_var.set(value)
    
    def on_segment_custom_interval_change(self, segment, entry, interval_var):
        try:
            val = int(entry.get())
            if 1 <= val <= 3600:
                segment.interval = val
                # interval_var 是 radio 单选变量，值只能是 5 或 -1
                # -1 表示"自定义"，实际秒数从 segment.interval 读取
                interval_var.set(-1)
                entry.delete(0, tk.END)
                entry.insert(0, str(val))
        except:
            pass
    
    def add_time_segment(self):
        self.frame_segment_counter += 1
        segment = TimeSegment(
            id=self.frame_segment_counter,
            start_sec=0,
            end_sec=self.frame_duration if self.frame_duration > 0 else 60,
            mode='fps',
            fps=1,
            interval=5
        )
        self.frame_segments.append(segment)
        
        row = None
        for r in self.segments_row_widgets:
            if len(r.pack_slaves()) < 3:
                row = r
                break
        
        if row is None:
            row = tk.Frame(self.segments_rows_frame, bg=COLORS['bg_card'])
            row.pack(fill=tk.X, pady=2)
            self.segments_row_widgets.append(row)
        
        widgets = self.create_segment_widget(segment, parent_frame=row)
        self.frame_segment_widgets[segment.id] = widgets
        widgets['frame'].pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        self.segments_rows_frame.update_idletasks()
        self.segments_canvas.configure(scrollregion=self.segments_canvas.bbox('all'))
        
        self.check_frame_ready()
    
    def remove_time_segment(self, seg_id: int):
        if seg_id not in self.frame_segment_widgets:
            return
        widgets = self.frame_segment_widgets[seg_id]
        row = widgets['frame'].master
        widgets['frame'].destroy()
        del self.frame_segment_widgets[seg_id]
        self.frame_segments = [s for s in self.frame_segments if s.id != seg_id]
        
        if len(row.pack_slaves()) == 0:
            row.destroy()
            self.segments_row_widgets.remove(row)
    
    def parse_frame_time(self, time_str):
        try:
            parts = time_str.replace('：', ':').split(':')
            if len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            if len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            if len(parts) == 1:
                return float(parts[0])
            return -1
        except:
            return -1
    def load_frame_video(self):
        file_path = filedialog.askopenfilename(filetypes=[('Video Files', '*.mp4 *.avi *.mov *.mkv')])
        if not file_path:
            return
        self.frame_video_path = file_path
        self.lbl_frame_video.config(text=os.path.basename(file_path))
        self.lbl_frame_time.config(text="加载中...")

        # 后台线程探测视频信息（避免主线程阻塞）
        def _do_probe():
            info = probe_video_with_ffmpeg(self.frame_video_path)
            # 切回主线程更新 UI
            self.root.after(0, lambda: self._apply_video_info(info))

        threading.Thread(target=_do_probe, daemon=True).start()

    def _apply_video_info(self, info):
        if info is None:
            messagebox.showerror("错误", "无法读取视频信息！\n请确认视频文件正常。")
            self.lbl_frame_time.config(text="--:--:-- / --:--:--")
            return
        self.frame_fps, self.frame_total_frames, self.frame_duration = info
        self.frame_cap = None
        self.frame_start_frame = 0
        self.frame_end_frame = max(0, self.frame_total_frames - 1)
        self.scale_frame.config(state=tk.NORMAL, to=max(1, self.frame_total_frames - 1))
        self.lbl_frame_time.config(text=f"00:00:00 / {self.format_time(self.frame_duration)}")
        self.frame_scale_var.set(0)
        self.frame_current_frame_idx = 0
        self.check_frame_ready()
        # 立即显示第一帧
        self.root.after(50, lambda: self.show_frame_at_idx(0))
        # 添加一个默认时间段
        if not self.frame_segments:
            self.add_time_segment()

    def show_frame_at_idx(self, frame_idx):
        # cv2 回退：用 ffmpeg 抽帧
        if self.frame_cap is None:
            if self.frame_fps > 0 and self.frame_video_path:
                time_sec = frame_idx / self.frame_fps
                self._ffmpeg_preview_at_time(time_sec)
                curr_time = frame_idx / self.frame_fps
                self.lbl_frame_time.config(text=f"{self.format_time(curr_time)} / {self.format_time(self.frame_duration)}")
            return
        if not self.frame_cap.isOpened():
            return
        try:
            total = int(self.frame_cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total <= 0:
                return
            frame_idx = max(0, min(frame_idx, total - 1))
            self.frame_cap.set(int(cv2.CAP_PROP_POS_FRAMES), frame_idx)
            ret, frame = self.frame_cap.read()
            if ret and frame is not None:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w = frame.shape[:2]
                if w == 0 or h == 0:
                    return
                # 用 PIL 正确转换图片
                from PIL import Image, ImageTk
                img_pil = Image.fromarray(frame)
                img_tk = ImageTk.PhotoImage(img_pil)
                # 缩放图片以适应canvas
                canvas_w, canvas_h = 640, 320
                img_w, img_h = img_pil.size
                if img_w > canvas_w or img_h > canvas_h:
                    ratio = min(canvas_w / img_w, canvas_h / img_h)
                    new_w = max(1, int(img_w * ratio))
                    new_h = max(1, int(img_h * ratio))
                    img_pil = img_pil.resize((new_w, new_h), Image.LANCZOS)
                    img_tk = ImageTk.PhotoImage(img_pil)
                # 保存到实例属性防止被 GC 回收
                self._frame_img_cache = img_tk
                self.canvas_frame.delete("all")
                x = (canvas_w - img_tk.width()) // 2
                y = (canvas_h - img_tk.height()) // 2
                self.canvas_frame.create_image(x, y, anchor=tk.NW, image=img_tk)
                if self.frame_fps > 0:
                    curr_time = frame_idx / self.frame_fps
                    self.lbl_frame_time.config(text=f"{self.format_time(curr_time)} / {self.format_time(self.frame_duration)}")
        except Exception as e:
            print(f"show_frame error: {e}")

    def _ffmpeg_preview_at_time(self, time_sec: float):
        """异步 ffmpeg 抽帧（节流 80ms + token 取消，确保最新帧一定显示）"""
        if not self.frame_video_path:
            return
        # 每次拖动记录目标时间 + token
        self._preview_target_time = time_sec
        self._preview_token = getattr(self, '_preview_token', 0) + 1
        my_token = self._preview_token
        # 节流：80ms 内的请求合并为最后一次
        if hasattr(self, '_preview_throttle_job') and self._preview_throttle_job:
            try: self.root.after_cancel(self._preview_throttle_job)
            except Exception: pass
        def _kickoff():
            self._preview_throttle_job = None
            # 若已被新请求覆盖则不启动（节省 CPU）
            if my_token != self._preview_token:
                return
            threading.Thread(target=self._do_ffmpeg_preview, args=(time_sec, my_token), daemon=True).start()
        self._preview_throttle_job = self.root.after(80, _kickoff)

    def _do_ffmpeg_preview(self, time_sec, my_token):
        """后台线程：执行 ffmpeg 抽帧，完成后切回主线程显示"""
        if not self.frame_video_path:
            return
        try:
            ffmpeg_exe = _get_ffmpeg_path("ffmpeg")
            out_path = os.path.join(tempfile.gettempdir(), f"_fp_{my_token}.png")
            # -ss 在 -i 之前（输入前 seek，启动最快）
            cmd = [
                ffmpeg_exe, "-y",
                "-ss", f"{time_sec:.3f}",
                "-i", self.frame_video_path,
                "-frames:v", "1",
                "-vcodec", "png",
                "-hide_banner", "-loglevel", "error",
                out_path
            ]
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            r = subprocess.run(cmd, capture_output=True, timeout=8, creationflags=creationflags)
            # 检查是否被新请求覆盖
            if my_token != self._preview_token:
                if os.path.exists(out_path):
                    try: os.remove(out_path)
                    except Exception: pass
                return
            if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                self.root.after(0, lambda: self._show_preview_image(out_path, my_token))
            elif os.path.exists(out_path):
                try: os.remove(out_path)
                except Exception: pass
        except Exception as e:
            print(f"_do_ffmpeg_preview error: {e}")

    def _show_preview_image(self, out_path, token):
        """主线程：加载并显示预览图"""
        if token != getattr(self, '_preview_token', None):
            try: os.remove(out_path)
            except Exception: pass
            return
        try:
            from PIL import Image, ImageTk
            # 使用 PIL 加载图片
            img_pil = Image.open(out_path)
            img_w, img_h = img_pil.size
            canvas_w, canvas_h = 640, 320
            # 缩放图片以适应canvas
            if img_w > canvas_w or img_h > canvas_h:
                ratio = min(canvas_w / img_w, canvas_h / img_h)
                new_w = max(1, int(img_w * ratio))
                new_h = max(1, int(img_h * ratio))
                img_pil = img_pil.resize((new_w, new_h), Image.LANCZOS)
            # 转换为 PhotoImage
            img_tk = ImageTk.PhotoImage(img_pil)
            self._frame_img_cache = img_tk
            self.canvas_frame.delete("all")
            x = (canvas_w - img_tk.width()) // 2
            y = (canvas_h - img_tk.height()) // 2
            self.canvas_frame.create_image(x, y, anchor=tk.NW, image=img_tk)
            try: os.remove(out_path)
            except Exception: pass
        except Exception as e:
            print(f"_show_preview_image error: {e}")


    def on_frame_slider_move(self, val):
        pass  # Do nothing when slider button is clicked, only show on drag
    
    def on_frame_slider_drag(self, event):
        frame_idx = int(float(self.frame_scale_var.get()))
        self.frame_current_frame_idx = frame_idx
        # 更新时间标签（无论有无 cv2）
        if self.frame_fps > 0:
            curr_time = frame_idx / self.frame_fps
            self.lbl_frame_time.config(text=f"{self.format_time(curr_time)} / {self.format_time(self.frame_duration)}")
        # 调用预览（有 cv2 时用 cv2，无 cv2 时用 ffmpeg）
        self.show_frame_at_idx(frame_idx)
    
    def set_frame_start_time(self):
        self.frame_start_frame = self.frame_current_frame_idx
        if self.frame_start_frame > self.frame_end_frame:
            self.frame_end_frame = self.frame_total_frames - 1
        self.update_frame_entries()
        self.update_frame_range_label()
    def set_frame_end_time(self):
        self.frame_end_frame = self.frame_current_frame_idx
        if self.frame_end_frame < self.frame_start_frame:
            self.frame_start_frame = 0
        self.update_frame_entries()
        self.update_frame_range_label()
    def apply_frame_manual_time(self):
        if not self.frame_cap:
            return
        st_sec = self.parse_frame_time(self.entry_frame_start.get())
        et_sec = self.parse_frame_time(self.entry_frame_end.get())
        if st_sec < 0 or et_sec < 0:
            messagebox.showerror("格式错误", "请输入正确的时间格式！")
            return
        if st_sec > self.frame_duration:
            st_sec = self.frame_duration
        if et_sec > self.frame_duration:
            et_sec = self.frame_duration
        if st_sec > et_sec:
            st_sec, et_sec = et_sec, st_sec
        self.frame_start_frame = int(st_sec * self.frame_fps)
        self.frame_end_frame = int(et_sec * self.frame_fps)
        self.update_frame_entries()
        self.update_frame_range_label()
        self.frame_scale_var.set(self.frame_start_frame)
        self.on_frame_slider_move(self.frame_start_frame)
        messagebox.showinfo("成功", f"时间已更新！将提取: {self.format_time(st_sec)} 到 {self.format_time(et_sec)}")
    def update_frame_entries(self):
        if self.frame_fps > 0:
            self.entry_frame_start.delete(0, tk.END)
            self.entry_frame_start.insert(0, self.format_time(self.frame_start_frame / self.frame_fps))
            self.entry_frame_end.delete(0, tk.END)
            self.entry_frame_end.insert(0, self.format_time(self.frame_end_frame / self.frame_fps))
    def update_frame_range_label(self):
        if self.frame_fps > 0:
            st = self.format_time(self.frame_start_frame / self.frame_fps)
            et = self.format_time(self.frame_end_frame / self.frame_fps)
            self.lbl_frame_range.config(text=f"当前提取范围: {st} 到 {et}")
    def select_frame_folder(self):
        folder = filedialog.askdirectory(initialdir=os.path.expanduser("~"))
        if folder:
            self.frame_save_folder = folder
            self.lbl_frame_folder.config(text=self.frame_save_folder)
            self.check_frame_ready()
    def check_frame_ready(self):
        if hasattr(self, 'frame_video_path') and self.frame_video_path:
            if hasattr(self, 'frame_save_folder') and self.frame_save_folder:
                self.btn_frame_start.config(state=tk.NORMAL)
    def start_frame_processing(self):
        self.btn_frame_start.config(state=tk.DISABLED)
        self.btn_frame_cancel.config(state=tk.NORMAL)
        self.btn_frame_pause.config(state=tk.NORMAL, text="暂停")
        self.scale_frame.config(state=tk.DISABLED)
        self.frame_is_processing = True
        self.frame_is_paused = False
        self.frame_pause_event = threading.Event()
        threading.Thread(target=self.process_frame_video, daemon=True).start()
    
    def cancel_frame_processing(self):
        self.frame_is_processing = False
        self.frame_is_paused = False
        self.finish_frame_processing(0, cancelled=True)
    
    def pause_frame_processing(self):
        if self.frame_is_paused:
            self.frame_is_paused = False
            self.btn_frame_pause.config(text="暂停")
            if self.frame_pause_event:
                self.frame_pause_event.set()
        else:
            self.frame_is_paused = True
            self.btn_frame_pause.config(text="继续")
    
    def get_frame_interval_frame(self):
        if self.extract_mode_frame.get() == "fps":
            target_fps = self.fps_var_frame.get()
            if target_fps == -1:
                try:
                    target_fps = self.custom_fps_var_frame.get()
                    if target_fps < 1 or target_fps > 30:
                        raise ValueError
                except:
                    return None, "自定义帧率必须是 1 到 30 之间的整数！"
            frame_interval = int(round(self.frame_fps / target_fps))
        else:
            interval_sec = self.interval_var_frame.get()
            if interval_sec == -1:
                try:
                    interval_sec = self.custom_interval_var_frame.get()
                    if interval_sec < 1 or interval_sec > 3600:
                        raise ValueError
                except:
                    return None, "自定义间隔必须是 1 到 3600 秒之间的整数！"
            frame_interval = int(round(self.frame_fps * interval_sec))
        return max(1, frame_interval), None
    def process_frame_video(self):
        if not self.frame_segments:
            self.root.after(0, lambda: messagebox.showerror("错误", "请先添加时间段设置！"))
            self.root.after(0, self.finish_frame_processing, 0, False)
            return
        
        # 在处理前更新所有时间段的值（start/end/mode/fps/interval）
        for segment in self.frame_segments:
            widgets = self.frame_segment_widgets.get(segment.id, {})
            start_text = widgets.get('entry_start', None)
            end_text = widgets.get('entry_end', None)
            mode_var = widgets.get('mode_var', None)
            fps_var = widgets.get('fps_var', None)
            interval_var = widgets.get('interval_var', None)
            custom_fps_entry = widgets.get('custom_fps_entry', None)
            custom_interval_entry = widgets.get('custom_interval_entry', None)
            if start_text:
                self.update_segment_time(segment, 'start', start_text.get())
            if end_text:
                self.update_segment_time(segment, 'end', end_text.get())
            # 同步 mode
            if mode_var is not None:
                try:
                    segment.mode = mode_var.get()
                except Exception:
                    pass
            # 同步 fps（自定义值）
            if fps_var is not None:
                try:
                    fv = fps_var.get()
                    if fv == -1 and custom_fps_entry is not None:
                        try:
                            segment.fps = max(1, int(custom_fps_entry.get()))
                        except Exception:
                            segment.fps = 1
                    elif fv > 0:
                        segment.fps = fv
                except Exception:
                    pass
            # 同步 interval（自定义值）
            if interval_var is not None:
                try:
                    iv = interval_var.get()
                    if iv == -1 and custom_interval_entry is not None:
                        try:
                            v = int(custom_interval_entry.get())
                            if 1 <= v <= 3600:
                                segment.interval = v
                        except Exception:
                            pass
                    elif iv == 5:
                        segment.interval = 5
                except Exception:
                    pass
        
        saved_count = 0
        total_segments = len(self.frame_segments)

        # cv2 不可用时用 ffmpeg 抽帧
        if self.frame_cap is None or not self.frame_cap.isOpened():
            for seg_idx, segment in enumerate(self.frame_segments):
                cnt = self._ffmpeg_process_segment(segment, seg_idx, total_segments)
                saved_count += cnt
                if not self.frame_is_processing:
                    self.root.after(0, lambda c=saved_count: self.finish_frame_processing(c, cancelled=True))
                    return
            self.root.after(0, lambda c=saved_count: self.finish_frame_processing(c, cancelled=False))
            return


        
        for seg_idx, segment in enumerate(self.frame_segments):
            if not self.frame_is_processing:
                self.root.after(0, lambda c=saved_count: self.finish_frame_processing(c, cancelled=True))
                return
            
            start_sec = segment.start_sec if segment.start_sec else 0
            end_sec = segment.end_sec if segment.end_sec else self.frame_duration
            
            start_frame = int(start_sec * self.frame_fps)
            end_frame = int(end_sec * self.frame_fps)
            start_frame = max(0, min(start_frame, self.frame_total_frames - 1))
            end_frame = max(0, min(end_frame, self.frame_total_frames - 1))
            
            if segment.mode == 'fps':
                target_fps = segment.fps if segment.fps > 0 else 1
                frame_interval = max(1, int(self.frame_fps / target_fps))
            else:
                interval_sec = segment.interval if segment.interval > 0 else 5
                frame_interval = max(1, int(self.frame_fps * interval_sec))
            
            self.frame_cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
            seg_total = end_frame - start_frame + 1
            
            for curr_f in range(start_frame, end_frame + 1):
                if not self.frame_is_processing:
                    self.root.after(0, lambda c=saved_count: self.finish_frame_processing(c, cancelled=True))
                    return
                if self.frame_is_paused:
                    self.frame_pause_event.wait()
                    continue
                
                ret, frame = self.frame_cap.read()
                if not ret:
                    break
                
                local_frame = curr_f - start_frame
                if local_frame % frame_interval == 0:
                    time_sec = curr_f / self.frame_fps
                    h = int(time_sec // 3600)
                    m = int(time_sec % 3600 // 60)
                    s = int(time_sec % 60)
                    ms = int((time_sec % 1) * 100)
                    file_time_str = f"{h:02d}-{m:02d}-{s:02d}-{ms:02d}"
                    filename = os.path.join(self.frame_save_folder, f"frame_{file_time_str}.jpg")
                    is_success, im_buf_arr = cv2.imencode(".jpg", frame)
                    if is_success:
                        im_buf_arr.tofile(filename)
                        saved_count += 1
                
                if curr_f % 10 == 0 or curr_f == end_frame:
                    overall_progress = ((seg_idx + local_frame / seg_total) / total_segments) * 100
                    self.root.after(0, lambda v=overall_progress, c=saved_count: self.update_frame_progress(v, c))
        
        self.root.after(0, lambda c=saved_count: self.finish_frame_processing(c, cancelled=False))

    def _ffmpeg_process_segment(self, segment, seg_idx, total_segments):
        """用 ffmpeg 处理一个时间段的抽帧（cv2 不可用时回退）"""
        if self.frame_fps <= 0 or not self.frame_video_path:
            return 0
        start_sec = segment.start_sec if segment.start_sec else 0
        end_sec = segment.end_sec if segment.end_sec else self.frame_duration
        mode = segment.mode
        if mode == 'fps':
            target_fps = segment.fps if segment.fps > 0 else 1
            step_sec = 1.0 / target_fps
        else:
            step_sec = segment.interval if segment.interval > 0 else 5
        saved_count = 0
        t = start_sec
        seg_total = max(1, int((end_sec - start_sec) / step_sec))
        ffmpeg_exe = _get_ffmpeg_path("ffmpeg")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        while t <= end_sec:
            if not self.frame_is_processing:
                return saved_count
            if self.frame_is_paused:
                self.frame_pause_event.wait()
            filename = os.path.join(
                self.frame_save_folder,
                f"frame_{int(t // 3600):02d}-{int(t % 3600 // 60):02d}-{int(t % 60):02d}-{int((t % 1) * 100):02d}.jpg"
            )
            out_path = os.path.join(tempfile.gettempdir(), f"_frame_{int(t * 1000)}.png")
            cmd = [
                ffmpeg_exe, "-y", "-ss", f"{t:.3f}",
                "-i", self.frame_video_path,
                "-vframes", "1",
                "-vcodec", "png",
                "-hide_banner", "-loglevel", "error",
                out_path
            ]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=15, creationflags=creationflags)
                if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    # 保存为 JPG
                    if Image is not None:
                        img = Image.open(out_path)
                        img = img.convert('RGB')
                        img.save(filename, 'JPEG', quality=95)
                    else:
                        # 无 PIL：用 ffmpeg 直接转 jpg
                        jpg_path = filename  # ffmpeg 直接输出 jpg
                        cmd2 = [
                            ffmpeg_exe, "-y", "-i", out_path,
                            "-q:v", "2", jpg_path
                        ]
                        subprocess.run(cmd2, capture_output=True, timeout=10, creationflags=creationflags)
                    saved_count += 1
                    os.remove(out_path)
            except Exception:
                pass
            t += step_sec
            overall_progress = ((seg_idx + saved_count / max(1, seg_total)) / total_segments) * 100
            self.root.after(0, lambda v=overall_progress, c=saved_count: self.update_frame_progress(v, c))
        return saved_count

    def update_frame_progress(self, val, count):
        self.progress_frame["value"] = val
        self.lbl_frame_status.config(text=f"正在处理... 已保存 {count} 张图片")
    def finish_frame_processing(self, count, cancelled=False):
        self.progress_frame["value"] = 100 if not cancelled else 0
        self.lbl_frame_status.config(text="已取消" if cancelled else f"处理完成！共保存 {count} 张图片。")
        self.btn_frame_start.config(state=tk.NORMAL)
        self.btn_frame_cancel.config(state=tk.DISABLED)
        self.btn_frame_pause.config(state=tk.DISABLED)
        self.scale_frame.config(state=tk.NORMAL)
        self.frame_is_processing = False
        self.frame_is_paused = False
        self.frame_pause_event = None
        if not cancelled and count > 0:
            messagebox.showinfo("完成", f"视频采帧完成！\n按您的设置，共成功保存了 {count} 张图片。")
def main():
    root = tk.Tk()
    app = MedicalFileProcessorApp(root)
    root.mainloop()
if __name__ == "__main__":
    main()