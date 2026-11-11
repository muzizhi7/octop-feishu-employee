# Octop 飞书数字员工

把本地 Octop Agent 变成真正能用的飞书数字员工：一条安装流程完成接入，出问题能一条命令回滚。

Octop 自带的飞书通道能收发消息，但真拿去给同事用，很快就会撞上这些事：回答不分话题、群里每条消息都要 @、处理过程一半折叠一半展开、按了「暂停」其实还在跑、发回来的附件打不开、Octop 一升级补丁就没了。

这个仓库就是补上这一层，并且把升级后的恢复也做进流程里。

```
飞书消息 → 路由与触发判断 → 任务记录与控制 → Octop 模型/工具运行时
        → 流式投影 → 原生气泡或交互卡片 → 回调校验
```

## 效果预览

以下为真实飞书交互效果，敏感信息已移除。

![独立话题与上下文](docs/screenshots/topic-isolation.png)

![任务执行过程](docs/screenshots/task-progress.png)

## 装完之后有什么不一样

| 场景 | Octop 原生飞书通道 | 安装本 Skill 后 |
| --- | --- | --- |
| 群聊回答 | 平铺在群里，上下文混在一起 | 默认引用原文回复；明确要求时开话题，话题内独立上下文 |
| 话题内追问 | 每次都要重新 @ | 可配置为「已参与话题免 @ 续聊」 |
| 处理过程 | 状态与正文混排，长卡片一大堆 | 「思考中 / 执行中」一行入口，点开看工具活动 |
| 暂停 | 视觉上停了，模型还在调工具 | 拦截后续模型与工具准入，继续可唤醒原执行 |
| 停止 / 重试 | 入口不稳定 | 停止在过程内；只有失败或停止后才出现重试 |
| 回答不满意 | 出现「重新生成」，但语义不适合数字员工 | 直接发新消息继续改，成功回答不显示重生成 |
| 短回答 | 巨大卡片，几行字占满屏 | 原生气泡，高度跟着内容走 |
| 图片 / 文件 / 语音 / 视频 | 部分场景失败或不可读 | 双向收发，识别失败会明确说明 |
| Octop 升级 | 补丁被覆盖，要重新改一遍 | 自动检查源码与接口，兼容则恢复，不兼容则保留新版并报告 |

## 三条命令跑起来

需要本机已装好 Octop，并且有一个可用的飞书自建应用。

```sh
git clone https://github.com/muzizhi7/octop-feishu-employee.git ~/.codex/skills/octop-feishu-employee
cd ~/.codex/skills/octop-feishu-employee
python3 scripts/employee_patch.py doctor
```

`doctor` 只做检测：看真实源码、接口契约、运行进程、任务状态和媒体依赖，不读凭证、不输出聊天正文、不改任何文件。重点看三个字段：

- `core_compatible`：源码与接口契约是否匹配当前 Octop
- `install_ready`：现在能不能装（Octop 正在运行时会给出 `quit_octop_before_install`）
- `blockers`：不满足的条件，逐条说明原因

确认可以装之后再走安装：

```sh
python3 scripts/employee_patch.py verify      # 隔离目录跑回归，不动已安装文件
# 等当前任务结束，退出 Octop
python3 scripts/employee_patch.py install --dry-run
python3 scripts/employee_patch.py install
python3 scripts/employee_patch.py guard-enable   # 升级后自动恢复
# 启动 Octop，在你自己的机器人私聊或测试群里验收
```

装完想调行为，打开本机配置页：

```sh
python3 scripts/employee_patch.py configure
```

页面只监听 `127.0.0.1:8766`，读取你已有的飞书连接，不重复保存 App Secret。可配置：群回复方式（引用 / 话题）、@ 触发条件、执行过程展示、媒体识别开关；还可检查连接、发送私聊测试、检查升级兼容。不需要团队管理员，也不需要独立远端部署。

## 为什么值得用 Skill 装

这个包既是可安装的 Agent Skill，也是可继续改的源码工程。把它放进支持 Skill 格式的编码代理目录（Codex 默认 `~/.codex/skills/`），然后说：

> 使用 $octop-feishu-employee 检测本机环境，为我的 Octop 安装飞书数字员工交互补丁。

代理会按 `SKILL.md` 的顺序走检测、验证、安装、验收，并在不兼容时停下来告诉你原因，而不是硬覆盖你的运行时。你也可以完全不用代理，直接在解压目录执行上面的命令——所有逻辑都在 `scripts/` 和 `assets/` 里，没有隐藏的在线服务。

包里没有作者的凭证、会话记录或已授权账号。每位使用者连接自己的飞书应用、自己的模型、自己的权限。

## 环境支持

| 环境 | 状态 |
| --- | --- |
| macOS（Apple Silicon）+ Octop 1.0.2 + 标准 portable 目录 + Python 3.12 | 完整验收：检测、安装、回滚、自动恢复、四类附件真实收发 |
| macOS Intel | 源码级兼容检查通过；媒体依赖需在目标机自行安装验证 |
| 其他 Octop 版本 / Python 小版本 | 先跑 `doctor`；接口或 ABI 变化会阻断，需要补适配 |
| Linux | 可检测，自动安装阻断（路径、服务管理、OCR、运行时待适配） |
| Windows | 可检测，自动安装阻断（python.exe、Lib/site-packages、文件锁、计划任务待适配） |
| 非标准目录 / 容器 / 多用户 | 支持显式路径检测；自动安装阻断 |

细节与手动指定路径见 [references/environments.md](references/environments.md)。自动安装目前只覆盖上表第一行这一种形态，这是明确的边界，不是"理论上应该也行"。

## 它做了什么

**对话层**：引用真实原消息；话题隔离上下文；群聊默认不抢话；短回答用气泡，长内容用卡片；Markdown、表格、代码、链接按飞书原生渲染。

**任务层**：任务日志记录每一次提问、工具调用和产出；等待确认与多选问答带超时和发起者校验；暂停做的是真实的主线程准入拦截，不是停掉事件消费；停止与重试有明确的前置条件；所有回调都校验会话、原卡、版本和能力凭据，并保留 Octop 自己的审批规则。

**附件层**：图片、文件、语音、视频接收后落盘并路由给模型；产出文件按会话回传；识别能力缺失时提示未完成，不静默给一个空答案。

**安装层**：安装前备份真实文件、验证期间若 Octop 重启或更新器改文件则取消、失败自动回滚；`rollback` 回到**最近一次安装之前**的状态；`guard-enable` 用 launchd 每 60 秒巡检，兼容才恢复。

## 与飞书 Aily 的差距

我们对着 Aily 的群聊、话题和任务状态逐项对齐，下面这些已经做到：引用回复、话题工作区、过程折叠、等待确认、暂停/继续/停止、附件收发、短消息紧凑展示。

还没做到、也不打算假装做到的：

- Aily 底部那种独立任务抽屉（当前用原生话题 + 折叠卡片替代）。
- Aily 的企业级托管、审计后台与官方模型通道——这是产品形态差异，本项目的价值在本地数据控制、可改源码和可回滚。
- 没有复制 Aily 的任何内部实现，也没有任何官方背书。

## 继续改这个补丁

权威源码、构建入口和测试都在这个目录里，改完重新构建即可：

```sh
python3 scripts/employee_patch.py build
python3 scripts/employee_patch.py verify
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/employee_patch.py package --output ~/Desktop/octop-feishu-employee.zip
```

改哪里、怎么加新版本适配，见 [references/development.md](references/development.md)；安装与日常使用见 [references/usage.md](references/usage.md)；交付前逐机验收清单见 [references/acceptance.md](references/acceptance.md)。

## 验证说明

`verify` 跑 118 项合成回归，`tests/` 另有 20 项安装器与配置测试，覆盖安装、回滚、部分写入失败、验证期间被改文件、暂停清理、偏好持久化和打包完整性。

这些测试证明代码路径正确，**不替代**你自己账号上的真实验收：引用对不对、话题有没有独立、按钮点下去有没有反应、附件能不能打开，都要在你自己的飞书里点一遍。给别人演示或交付给领导前，请按 [acceptance.md](references/acceptance.md) 走完整链路。

## 许可

本项目以 MIT 发布，见 [LICENSE](LICENSE)。其中若干运行时补丁文件改写自 [TencentCloud/Octop](https://github.com/TencentCloud/Octop)（MIT），完整声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

与 Octop、飞书、Lark、Aily 均无官方关联。
