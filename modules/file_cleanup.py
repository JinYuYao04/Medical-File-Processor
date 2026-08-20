# -*- coding: utf-8 -*-
"""
文件清洗模块 - 清理没有对应JSON的JPG文件
"""

import os
from pathlib import Path
from typing import List


def collect_orphan_jpg(root: str) -> List[str]:
    """收集没有对应JSON文件的JPG文件"""
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


def clean_orphan_files(source_folder: str, output_folder: str, orphan_files: List[str],
                       progress_callback = None, cancel_event = None) -> tuple:
    """清洗孤立文件
    
    如果输出文件夹与源文件夹相同，则删除孤立文件
    否则复制非孤立文件到输出文件夹
    
    返回: (成功数, 失败数, 是否删除模式)
    """
    is_same_folder = (os.path.abspath(output_folder) == os.path.abspath(source_folder))
    orphan_set = set(os.path.abspath(f) for f in orphan_files)
    success_count = 0
    failed_count = 0
    
    if is_same_folder:
        for f in orphan_files:
            if cancel_event and cancel_event.is_set():
                break
            try:
                os.remove(f)
                if progress_callback:
                    progress_callback(f"已删除: {os.path.basename(f)}")
                success_count += 1
            except Exception as e:
                failed_count += 1
                if progress_callback:
                    progress_callback(f"删除失败: {os.path.basename(f)} - {str(e)}")
    else:
        import shutil
        for dirpath, _, filenames in os.walk(source_folder):
            if cancel_event and cancel_event.is_set():
                break
            rel_path = os.path.relpath(dirpath, source_folder)
            dest_dir = output_folder if rel_path == '.' else os.path.join(output_folder, rel_path)
            
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except Exception as e:
                if progress_callback:
                    progress_callback(f"创建目录失败: {dest_dir} - {str(e)}")
                continue
            
            for f in filenames:
                if cancel_event and cancel_event.is_set():
                    break
                src_path = os.path.join(dirpath, f)
                if os.path.abspath(src_path) in orphan_set:
                    continue
                
                dest_path = os.path.join(dest_dir, f)
                try:
                    shutil.copy2(src_path, dest_path)
                    success_count += 1
                    if progress_callback:
                        progress_callback(f"已复制: {f}")
                except Exception as e:
                    failed_count += 1
                    if progress_callback:
                        progress_callback(f"复制失败: {f} - {str(e)}")
    
    return success_count, failed_count, is_same_folder
