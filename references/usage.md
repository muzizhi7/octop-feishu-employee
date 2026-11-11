# 安装、使用与回滚

## 获取与分享 Skill

三种拿到本 Skill 的方式，内容一致，任选一种：

```sh
# 1. Git 克隆（便于以后 git pull 更新）
git clone https://github.com/muzizhi7/octop-feishu-employee.git ~/.codex/skills/octop-feishu-employee

# 2. 从 GitHub Releases 下载 zip 后解压到 Skill 目录
unzip octop-feishu-employee-skill-v1.1.0.zip -d ~/.codex/skills/

# 3. 从 SkillHub 安装：https://skillhub.cn/skills/octop-feishu-employee
```

解压得到 `octop-feishu-employee/`，整体放进支持该格式的编码代理的 Skill 目录。Codex 默认是 `~/.codex/skills/`；自定义 CODEX_HOME 时使用对应 skills 目录。刷新 Skill 列表后调用：

> 使用 $octop-feishu-employee 检测本机环境，为我的 Octop 安装飞书数字员工交互补丁。

已有同名 Skill 时先保留自己的源码修改，再合并新版。也可以不安装 Skill，直接在解压目录执行命令——安装逻辑不依赖任何在线服务。

压缩包内 `bundle-manifest.json` 是逐文件 SHA-256 清单，可用来核对解压结果是否完整；它不是发布者签名。发布页同时提供 `.zip.sha256` 供整包校验：

```sh
shasum -a 256 -c octop-feishu-employee-skill-v1.1.0.zip.sha256
```

## 检测

控制解释器需要 Python 3.9 以上；macOS 可先尝试 `/usr/bin/python3`。补丁回归使用 Octop 自带 Python 3.12。

```sh
python3 scripts/employee_patch.py doctor
python3 scripts/employee_patch.py verify
```

`doctor` 检查实际源码、接口契约、运行进程和任务状态，不读取凭证，只从本地任务记录提取状态，不输出正文。`verify` 在临时覆盖目录运行候选回归，不修改已安装运行文件。媒体缺失单独提示，不阻止基础对话。仅要求检测时不要继续安装。

## 安装

等当前任务结束，再退出 Octop：

```sh
python3 scripts/employee_patch.py install --dry-run
python3 scripts/employee_patch.py install
python3 scripts/employee_patch.py guard-enable
```

安装前再次检测、验证候选并备份本机文件；验证期间 Octop 重启或更新器改文件时取消安装。然后启动 Octop，在本人机器人测试。

每位使用者仍需连接自己的飞书应用、模型和权限。包中没有创建者的凭证、会话及已授权账号。备份默认位于 `~/Library/Application Support/OctopFeishuPatch/managed/`，与源码分离。完全相同的补丁已存在时返回 `already_installed`，不伪造安装前备份；后续修改并安装时会备份该机器真实的当前文件。

## 正常对话

普通群 @机器人默认引用原消息回复，不自动新建话题；明确说“请开一个话题处理……”才创建。已有话题内继续回复，权限只推送 @消息时仍需 @。展开状态可暂停后续调用、继续或停止。完成后通过新消息提修改，不显示重新生成。适合的文稿可保留 Word/PDF 交付入口。

旧卡迁移只覆盖本机保留的最多 100 条、14 天内记录，不承诺改写全部历史消息。

## 自动恢复与回滚

`guard-enable` 安装 macOS LaunchAgent，每 60 秒检测、启动前检查；兼容时运行回归后恢复，不兼容时保留新版。它不会为了刷新补丁重启正在工作的 Octop。每次修改、构建并安装后都要再次 `guard-enable`，同步新版源码和测试。

```sh
python3 scripts/employee_patch.py status
python3 scripts/employee_patch.py guard-disable
```

状态文件位于 `~/Library/Application Support/OctopFeishuPatch/status.md`。

退出 Octop后回滚：

```sh
python3 scripts/employee_patch.py rollback --dry-run
python3 scripts/employee_patch.py rollback
```

检查当前文件和备份后，停用自动恢复，返回**本 Skill 最近一次安装之前**的状态。若安装前已有补丁，回滚保留它，不称为出厂恢复。无记录或安装文件已发生其他变更时拒绝自动回滚。

媒体检测用 `python3 scripts/employee_patch.py media-check`；依赖准备见 [不同环境](environments.md)。

## 打开个人配置页

在 Skill 根目录执行：

```sh
python3 scripts/employee_patch.py configure
```

自动监听本机 `127.0.0.1:8766` 并打开浏览器；端口占用时用 `--port 8767`。无桌面浏览器的环境加 `--no-open`，读取输出的本机链接。页面只在运行这条命令的机器访问，不要将此端口暴露到外网。关闭页面不会清空设置，结束命令后页面服务停止，机器人继续工作。

页面读取已有飞书通道，不重复保存 App Secret。可按连接设置群回复方式、@ 触发、执行过程和媒体识别。保存后新消息使用新设置，已有任务保留快照；无完成后重新生成、确认和停止入口属于固定行为。配置文件放在补丁的持久目录中，升级/回滚不删除个人偏好。

「检查连接」只访问机器人及权限接口；「发送私聊测试」需确认后才向机器人最近的私聊发送一条消息，使用前请确保最近私聊属于你本人。它不会给群发消息。「获取授权指令」复制对应授权指令，由你发送给数字员工，沿用它现有的本人授权流程；页面列出的应用权限不代表已取得你的个人授权。此页不会自动扩大飞书消息权限。

「检查升级兼容」检查本机已安装版本，不从未知来源下载更新。兼容守护沿用原安装流程。回滚需退出 Octop，恢复上次安装前的文件并停用守护；不会删除你的个人偏好。

## 话题内免 @ 续聊

在个人配置页将触发条件设为「已参与话题可免 @ 续聊」。普通群主会话仍只在 @ 时回复；话题内已与机器人交互过的用户可直接续聊，使用该话题自己的会话上下文。机器人消息、无关话题和普通群闲聊不会自动启动任务或显示输入中。

飞书应用必须具备 `im:message.group_msg`（或平台支持的同类群消息事件权限），并已订阅 `im.message.receive_v1`。`im:message.group_msg:get_as_user` 不是机器人接收群消息事件的权限。开通前需说明：平台会向机器人投递所在群中未 @ 的用户消息；本地筛选只能限制是否进入任务，不能把平台授予的权限缩小为单个话题。按目标企业要求开通及发布，免审权限可能即时生效，必须实际检查授权结果。不要顺带发布其他人的未完成草稿或扩大其他权限。

话题识别复用本机保留的任务记录（最多100条、14天）；不在记录中的旧话题先 @ 一次重新建立参与关系。重启后仍在记录内的话题可继续免 @。尚未与机器人交互过的其他成员先 @ 一次；待确认任务仍只接受任务发起者操作。
