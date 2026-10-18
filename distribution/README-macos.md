# 智学笔记 · Mac 版 v0.3.2

1. 先安装 Python 3.11 或更新版本：https://www.python.org/downloads/macos/
2. 完整解压 ZIP。双击 `start.command`，首次运行会联网安装依赖，之后自动打开浏览器。
3. 使用期间保持终端窗口开启。模型接口与 API Key 需在应用设置中自行填写。

若 macOS 未允许双击运行，请在终端进入此文件夹，执行 `bash start.command`。也可使用 `bash run.sh`；如需查看失败信息，在终端执行 `python3 launch.py --check`。

PPTX 的原始幻灯片预览需要 LibreOffice：https://www.libreoffice.org/download/download-libreoffice/ 。安装后重新打开课件；首次预览会在本机生成 PDF 缓存，不上传原文件。不安装时仍可看提取文字和下载课件。图片 OCR 可自行安装 Tesseract 中文/英文语言包，或配置视觉模型。

发行包不内置 Python 或 LibreOffice，不是 `.app` 安装程序。不包含作者的资料、对话、笔记、数据库、模型设置或密钥，首次启动为空白工作区，iCloud 同步默认关闭。已有用户升级前请备份并保留自己的 `data/` 文件夹。iCloud 同步当前面向 Mac，Windows 不支持此连接方式。
