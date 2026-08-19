# GitHub 部署说明

## 上传步骤

### 方法1：通过GitHub网页创建仓库

1. 访问 https://github.com/new
2. 创建新仓库：
   - Repository name: `medical-file-processor`
   - Description: `医疗文件处理工具 - 视频拼接、视频采帧、文件清洗、文件重命名`
   - 选择 Public
   - **不要**勾选 "Add a README file"
   - **不要**勾选 "Add .gitignore"
   - 点击 "Create repository"

3. 在本地运行以下命令：

```bash
cd e:\project\file\medical_file_processor
git remote add origin https://github.com/YOUR_USERNAME/medical-file-processor.git
git branch -M main
git push -u origin main
```

### 方法2：使用GitHub Desktop

1. 打开 GitHub Desktop
2. File -> Add Local Repository
3. 选择 `e:\project\file\medical_file_processor` 文件夹
4. 点击 "Publish repository"
5. 设置仓库名称为 `medical-file-processor`
6. 取消勾选 "Keep this code private"
7. 点击 "Publish Repository"

## 当前状态

✅ Git仓库已初始化
✅ 所有文件已添加到暂存区
✅ 代码已提交到本地仓库
⏳ 等待推送到GitHub远程仓库

## 项目文件清单

- `main.py` - GUI主程序
- `utils.py` - 工具函数
- `requirements.txt` - Python依赖
- `modules/` - 功能模块文件夹
  - `video_concat.py` - 视频拼接
  - `video_frame_extract.py` - 视频采帧
  - `file_cleanup.py` - 文件清洗
  - `file_rename.py` - 文件重命名
- `README.md` - 项目说明
- `.gitignore` - Git忽略配置
- `ffmpeg.exe` 和 `ffprobe.exe` - 视频处理工具

## 注意事项

- 仓库设置为公开（public）
- 已排除构建文件和临时文件
