# 不同环境

| 环境 | 当前处理 |
| --- | --- |
| macOS、标准 portable 目录、Python 3.12，源码与接口兼容 | 检测、验证、安装、回滚、launchd 自动恢复 |
| Apple Silicon / Intel | 可执行源码检测；原始真人验收在 Apple Silicon。Intel 媒体依赖须在目标机安装验证，不复制另一架构的二进制 |
| 版本号相同但代码改过 | 语法合并保留无关更新；修改同一目标时阻断 |
| 新版 Octop / 不同 Python 小版本 | 先检测；接口或 ABI 改变时停止，新增经过验证的兼容配置 |
| Linux | 标准库检测可用；自动安装阻断。需适配路径、服务管理、OCR 和运行时后再提供安装支持 |
| Windows | 检测可用；自动安装阻断。需适配 python.exe、Lib/site-packages、文件锁、计划任务及媒体依赖 |
| 非标准目录 / 容器 / 多用户 | 显式路径检测；自动安装阻断，需要连同状态、媒体和守护路径适配 |

已验证配置是 Octop 1.0.2、Python 3.12、macOS 标准安装，其他环境不标成已真人验收。

## 指定检测位置

```sh
python3 scripts/employee_patch.py doctor --octop-home /path/to/octop-data
python3 scripts/employee_patch.py doctor --packages /path/to/packages --runtime /path/to/python
```

Windows：

```powershell
py -3 scripts/employee_patch.py doctor --packages 'D:\Octop\portable\packages' --runtime 'D:\Octop\portable\runtime\python.exe'
```

`nonstandard_layout_requires_adaptation` 是预期阻断。代码也使用默认状态、媒体工作目录与 macOS OCR 路径，不能仅改根路径就宣称支持跨平台安装。

## 可选媒体依赖

基础收发与内容识别分别验收。当前路径是图片 OCR、视频抽帧 OCR、Whisper 语音转写，不代表完整视觉或视频理解。

仅在用户要求启用识别时准备依赖；可能访问软件和模型仓库，不自动把用户附件上传到其他识别服务。

1. 在目标系统安装匹配 CPU 架构的 FFmpeg，确认 `ffmpeg -version` 可运行，使用本机已有包管理器或官方发行说明。
2. macOS 图片/视频文字识别复用 Octop 的 `octop.infra.knowledge.ocr._extract_local`，用合成图片验证；缺失时如实提示识别未完成。
3. 退出 Octop，在 Skill 根目录安装 ASR：

```sh
MEDIA="$HOME/Library/Application Support/OctopFeishuPatch/media"
RUNTIME="$HOME/.octop/portable/runtime/bin/python3"
mkdir -p "$MEDIA"
cp assets/patch/media/asr_worker.py "$MEDIA/asr_worker.py"
"$RUNTIME" -m pip install --target "$MEDIA/deps" -r assets/patch/media/requirements.lock
"$RUNTIME" "$MEDIA/asr_worker.py" --download
python3 scripts/employee_patch.py media-check
```

已有 deps 时先核对 Python、CPU 和来源，先备份再在新目录验证，避免不同 ABI 叠装。模型与依赖未捆绑。锁定版本在目标环境不可用时，不盲目换最新版；在隔离目录选版本，实测后更新锁文件。

飞书应用、接收事件、卡片回调、用户授权及数据范围由各自账号配置。免 @续聊需要相应消息权限，缺失时用 @。豆包专属任务抽屉不在当前开放卡片实现中。


个人配置页当前随 macOS 自动安装配置提供（标准本地 Octop）。Windows/Linux 继续先运行 doctor 并按本文件适配运行时与持久目录；不能把在 Mac 上安装成功当作其他系统已通过安装或收发验收。无需团队管理员或独立远端部署。
