# 架构与关键设计

## 一次消息的完整路径

```
飞书事件（im.message.receive_v1 / card.action.trigger）
  → Octop 飞书通道（octop_gateway/channels/feishu.py）
  → 触发判断：私聊？@？已参与话题？免 @ 配置？权限是否具备？
  → 路由：普通群 / 话题 / 私聊，决定回复形态与锁键
  → 任务记录与控制（feishu_controls.py + hitl/coordinator.py）
  → Octop 模型与工具运行时
  → 流式投影（process/stream_project.py）
  → 原生气泡（短纯文本）或交互卡片（复杂正文 / 工具 / 确认）
  → 回调校验：发起者、会话、原卡、版本、能力凭据、问题索引
```

关键取舍：

- **短消息走气泡**。卡片适合承载工具与确认，不适合三行字。判定在投影层做，不在模型提示词里做。
- **话题即上下文边界**。锁键包含 chat + thread，避免同一群里多个话题互相污染任务记录。
- **暂停是准入拦截**。拦截点在模型与工具调用入口，已经开始的调用无法收回，因此文案只说"暂停后续调用"，不承诺冻结。
- **回调凭据一次性**。卡片按钮只携带不透明 turn ID、revision 和随机能力值，不携带提示词；旧卡回调即使被重放也过不了版本校验。

## 九个运行时文件

`release.json` 的 `files` 列出被修改的文件及期望摘要：

| 文件 | 作用 |
| --- | --- |
| `octop_gateway/channels/feishu.py` | 入站消息、路由、话题识别、发送 |
| `octop_gateway/channels/feishu_controls.py` | 卡片生命周期、任务状态、确认与问答、回调鉴权 |
| `octop_gateway/channels/feishu_pause.py` | 暂停、工具排空 |
| `octop_gateway/channels/feishu_preferences.py` | 偏好校验、原子保存、修订冲突 |
| `octop/infra/gateway/gateway.py` | 网关装配与中间件 |
| `octop/infra/gateway/process/processor.py` | 处理主循环、流清理 |
| `octop/infra/gateway/process/stream_project.py` | 流式投影、气泡/卡片切换 |
| `octop/infra/gateway/hitl/coordinator.py` | 人工确认协调 |
| `octop/infra/agents/manager.py` | 任务与代理生命周期 |

## 为什么需要"构建"这一步

上游版本之间的差异集中在这几个文件。直接分发整份文件会在小版本升级后覆盖掉上游修复，所以本项目维护三层输入：

- `assets/patch/before/`：已知原始基线
- `assets/patch/vendor/`：另一份已知兼容输入
- `assets/patch/released/`：一份已知安装的快照

`build` 把 `staged/` 里的权威源码与这些输入合并成最终文件，`verify` 在临时目录跑回归。合并冲突时阻断，不刷新基线来掩盖不兼容。

## 升级守护

```
guard-enable（launchd，每 60 秒 + 启动前）
  → 比对已安装文件摘要
  → 不一致：取当前源码做语法与接口契约检查
      → 兼容：跑隔离回归 → 通过则恢复补丁
      → 不兼容：保留新版文件，写状态并报告，不重启正在工作的 Octop
```

守护不会为了刷新补丁而重启 Octop，也不会下载任何更新。

## 数据落在哪里

| 内容 | 位置 |
| --- | --- |
| 安装前备份 | `~/Library/Application Support/OctopFeishuPatch/managed/` |
| 安装记录 / 状态 | 同目录 `installation.json`、`status.md` |
| 个人偏好 | 同目录 `preferences.json` |
| 媒体工作目录 | 同目录 `media/` |

源码与运行数据分离，升级和回滚都不删除个人偏好。
