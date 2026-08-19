# 医疗文件处理工具

一个功能强大的医疗文件处理工具，提供视频拼接、视频采帧、文件清洗和文件重命名四大核心功能。

## 功能特性

### 1. 视频拼接
- 支持多个视频文件按顺序拼接
- 使用FFmpeg进行高效处理
- 实时显示拼接进度
- 支持取消操作

### 2. 视频采帧
- 支持两种采帧方式：OpenCV和FFmpeg
- 可自定义采样间隔（按帧数或秒数）
- 支持批量处理多个视频
- 可选输出格式和质量

### 3. 文件清洗
- 识别并处理孤立的JPG文件
- 支持删除或移动孤立文件
- 安全的预览功能
- 批量处理能力

### 4. 文件重命名
- 按数字顺序批量重命名文件
- 支持自定义起始编号和位数
- 可指定文件范围
- 支持复制模式，保留原文件

## 系统要求

- Windows 10 或更高版本
- Python 3.8+ (如果从源码运行)

## 使用方法

### 直接运行（推荐）

1. 下载 `dist/医疗文件处理工具.exe`
2. 双击运行即可

### 从源码运行

1. 克隆仓库：
```bash
git clone <repository-url>
cd medical_file_processor
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

3. 运行程序：
```bash
python main.py
```

## 项目结构

```
medical_file_processor/
├── main.py                      # 主程序入口和GUI界面
├── modules/                     # 功能模块
│   ├── __init__.py
│   ├── video_concat.py         # 视频拼接模块
│   ├── video_frame_extract.py  # 视频采帧模块
│   ├── file_cleanup.py         # 文件清洗模块
│   └── file_rename.py          # 文件重命名模块
├── utils.py                     # 工具函数
├── requirements.txt             # Python依赖
├── ffmpeg.exe                   # FFmpeg工具
├── ffprobe.exe                  # FFprobe工具
└── dist/                        # 编译后的可执行文件
    └── 医疗文件处理工具.exe
```

## 开发说明

### 构建可执行文件

使用PyInstaller打包：

```bash
pyinstaller --onefile --windowed --add-data "ffmpeg.exe;." --add-data "ffprobe.exe;." --name "医疗文件处理工具" --clean main.py
```

### 技术栈

- **GUI框架**: Tkinter
- **视频处理**: OpenCV, FFmpeg
- **图像处理**: Pillow
- **打包工具**: PyInstaller

## 注意事项

- 视频拼接功能需要ffmpeg.exe和ffprobe.exe在同目录下
- 处理大文件时请确保有足够的磁盘空间
- 文件清洗操作不可逆，请谨慎使用删除功能

## 贡献者

<a href="https://github.com/JinYuYao04">
  <img src="https://github.com/JinYuYao04.png" width="50" height="50" alt="JinYuYao04" style="border-radius: 50%;">
</a>

- [@JinYuYao04](https://github.com/JinYuYao04) - 项目创建者和主要开发者

## 许可证

MIT License

## 更新日志

### v1.0.0 (2026-08-19)
- 初始版本发布
- 实现视频拼接功能
- 实现视频采帧功能
- 实现文件清洗功能
- 实现文件重命名功能
