# -*- coding: utf-8 -*-
"""
文件重命名模块
"""

import re
import shutil
from pathlib import Path


def rename_files_by_number(folder_path: str, output_folder: str = None, digit_count: int = 1, 
                           start_index: int = 1, end_index: int = 0, start_number: int = 1, 
                           file_extensions: str = "", progress_callback = None, result_callback = None, 
                           cancel_event = None) -> int:
    """按数字顺序重命名文件"""
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
    
    if not file_extensions:
        if progress_callback:
            progress_callback(('error', 0, 0, '', '请输入文件类型（如.jpg）'))
        if result_callback:
            result_callback(0)
        return 0
    
    ext = file_extensions.strip().lower()
    if not ext.startswith('.'):
        ext = f'.{ext}'
    file_suffixes = [ext]
    
    def natural_sort_key(path):
        name = path.name.lower()
        parts = re.split(r'(\d+)', name)
        return [(int(p) if p.isdigit() else p) for p in parts]
    
    renamed_count = 0
    total_processed = 0
    
    if progress_callback and copy_mode:
        progress_callback(('info', 0, 0, '',
                           f'复制模式：源文件夹将保持原样，文件复制到 "{output_dir}"'))
    
    for ext in file_suffixes:
        if cancel_event and cancel_event.is_set():
            if progress_callback:
                progress_callback(('info', 0, 0, '', '操作已取消'))
            break
        
        type_files = [
            f for f in folder.iterdir()
            if f.is_file()
            and not f.name.startswith(temp_prefix)
            and f.suffix.lower() == ext
        ]
        
        if not type_files:
            continue
        
        type_files.sort(key=natural_sort_key)
        
        s_idx = max(1, start_index) - 1
        e_idx = end_index if end_index and end_index > 0 else len(type_files)
        e_idx = min(e_idx, len(type_files))
        s_idx = min(s_idx, e_idx)
        selected_files = type_files[s_idx:e_idx]
        
        if not selected_files:
            continue
        
        for i, file_path in enumerate(selected_files):
            if cancel_event and cancel_event.is_set():
                if progress_callback:
                    progress_callback(('info', 0, 0, '', '操作已取消'))
                break
            
            seq = start_number + i
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
