# 安全策略

## 报告问题

请通过 GitHub 的 **Private vulnerability reporting**（仓库 Security 页）提交，不要开公开 issue，也不要在 issue 里粘贴 App Secret、tenant token、聊天记录或用户数据。

请说明：Octop 版本、Python 版本、操作系统、复现步骤、你能确认的影响范围。涉及飞书接口时给出接口名与错误码即可，不要附带完整请求体。

## 这个包会改什么

安装过程只做三类改动：

1. 修改你本机 Octop portable 目录下 `release.json` 里列出的 9 个运行时文件，改前逐份备份到 `~/Library/Application Support/OctopFeishuPatch/managed/`。
2. 在 `~/Library/Application Support/OctopFeishuPatch/` 下写入任务记录、偏好、媒体工作目录与状态文件。
3. 执行 `guard-enable` 时安装一个 launchd LaunchAgent，用于升级后巡检。

不会做的事：不上传你的文件或对话到任何第三方服务；不写入飞书凭证副本；不修改飞书应用权限；不在未经确认时向群聊发消息。

媒体识别默认只调用本机能力（Octop 自带 OCR、本机 FFmpeg、本机 Whisper）。若你自行替换为云端识别，数据出境与合规由你自行评估。

## 凭证

`configure` 页面读取你已配置的飞书通道，不重复保存 App Secret；页面只监听 `127.0.0.1`，不要把该端口暴露到外网或做端口转发。

仓库与发布包中不包含任何真实凭证。打包脚本会拒绝包含 `.env*`、`*.pem`、`*.key` 的文件，并扫描本地绝对路径与 `cli_` 开头的应用 ID。

## 权限边界

「已参与话题免 @ 续聊」需要飞书应用具备 `im:message.group_msg`。这会让平台向你投递所在群里**未 @ 你**的用户消息；本地过滤只能决定是否进入任务，无法把平台权限缩小到单个话题。开通前请按你所在企业的要求评估并发布。

## 支持范围

自动安装与自动恢复仅在 macOS + 标准 portable 目录 + Octop 1.0.2 + Python 3.12 上验收。其他环境请先运行 `doctor`，不要把检测通过当成已适配。
