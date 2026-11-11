# 在同一个补丁中继续开发

## 权威源码

| 目标 | 编辑位置 |
| --- | --- |
| 状态、卡片、问答确认、路由、导出、回调鉴权 | `assets/patch/staged/feishu_controls.py` |
| 暂停、工具排空 | `assets/patch/staged/octop_gateway/channels/feishu_pause.py` |
| 媒体处理及附件路由 | `assets/patch/media_methods.py`、`media_patch.py` |
| 群话题入站及锁键 | `assets/patch/topic_patch.py` |
| 网关接入、中间件、异步流清理 | `assets/patch/build.py` |
| 检测、安装、回滚、打包 | `scripts/employee_patch.py` |
| 更新后恢复 | `assets/patch/upgrade_guard.py`、`startup_hook.py`、`watch_upgrade.py` |
| 测试 | `assets/patch/test_*.py`、`tests/test_manager.py` |

不要直接改生成的 `staged/octop_gateway/channels/feishu_controls.py`；构建会覆盖。不要只改已安装的 portable/packages 文件，守护会恢复。应编辑上表中的权威源码。

`before/` 为原始基线，`vendor/` 为另一份已知兼容输入，`released/` 为一份已知安装的补丁快照（可能早于当前源码），供后续编辑识别升级路径。不要把未知新版覆盖进基线来绕过冲突。

## 修改与验证

在 Skill 根目录执行：

```sh
python3 scripts/employee_patch.py build
python3 scripts/employee_patch.py doctor
python3 scripts/employee_patch.py verify
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

已获应用修改授权时，等待任务空闲并退出 Octop，再 `install --dry-run`、`install`、`guard-enable`，然后启动并在已授权测试会话验收。不能把历史测试结果当作目标机器的真人验收。

完成后没有重新生成是当前规则；新消息继续修改同一会话。Word/PDF 交付和失败重试另有用途，不要一起误删。

## 新版本适配

在隔离目录取目标版本的干净源码，比较真实改动，调整构建挂载点和依赖接口，增加经过验证的合并输入。测试原版本、新版本、未知改动拒绝覆盖、升级回滚和暂停清理后再更新契约。不能单独运行 `upgrade_guard.py --contracts` 接受未知依赖变化。

## 发布

修改 `release.json` 中的版本号，构建、验证并打包：

```sh
python3 scripts/employee_patch.py build
python3 scripts/employee_patch.py verify
python3 scripts/employee_patch.py package --output /path/out/octop-feishu-employee-skill.zip
```

打包过滤安装记录、凭证、会话、缓存和备份，并生成逐文件 SHA-256 清单。`bundle-manifest.json` 检查包完整性，不是发布者数字签名。

源码可用 Git 维护；机器安装记录和备份保留在外部持久目录。其他同学也可直接在同一 Skill 目录修改、构建和发布。

## 个人配置页

- `assets/settings/index.html`、`style.css`、`app.js`：可继续修改的前端，不依赖在线 CDN 或构建服务。
- `scripts/settings_server.py`：本机服务、环境及维护操作；`settings_worker.py`：短进程调用飞书，只返回白名单非密钥字段。
- `assets/patch/staged/octop_gateway/channels/feishu_preferences.py`：校验、原子保存、修订冲突和运行时读取的共同源。属于第九个运行时补丁文件。
- `tests/test_settings.py` 与 `assets/patch/test_preferences.py`：偏好持久化、防错、真实回复/附件/显示路径的隔离测试。

安装或升级守护时须将第九个模块一起安装。普通偏好保存无需重启 Octop；改运行时源码仍遵循先空闲、退出、安装、刷新守护、再启动的流程。不要在打包中包含本机 preferences.json、服务访问凭据、截图或诊断导出。
