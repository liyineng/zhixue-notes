# 智学笔记 · Windows 版 v0.3.2

1. 安装 Python 3.11 或更新版本：https://www.python.org/downloads/windows/ 。安装后确认命令 `py -3 --version` 或 `python --version` 能运行。
2. 右键 ZIP → 全部解压。不要直接在压缩包里启动。
3. 双击 `start.bat`，首次运行会联网安装依赖，随后自动打开浏览器。
4. 使用期间保持命令窗口开启。模型接口和 API Key 需在应用设置中自行填写。

遇到失败时，请在解压目录的命令提示符中运行 `start.bat --check`，把完整报错发给维护者。Windows 使用 `.venv\Scripts\python.exe`，不需要 Bash，也不需要输入 Mac 的 `./run.sh` 命令。文件夹路径包含空格或中文也可以。

PPTX 原始幻灯片预览需要安装 LibreOffice：https://www.libreoffice.org/download/download-libreoffice/ 。默认检测 `C:\Program Files\LibreOffice\program\soffice.exe`；安装到其他位置时，可把相应的 program 目录加入 PATH。安装后重开课件；未安装时仍可看提取文字和下载原文件。图片 OCR 另需 Tesseract 中文/英文语言包，或配置视觉模型。

发行包不内置 Python 或 LibreOffice，不是免安装的 `.exe` 程序。不包含作者的资料、对话、笔记、数据库、模型设置或密钥，首次启动为空白工作区。升级前请备份并保留自己的 `data\` 文件夹。iCloud 同步当前面向 Mac，Windows 使用本地工作区。
