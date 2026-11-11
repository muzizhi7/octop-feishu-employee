# 参与开发

欢迎提 issue 和 PR。最有价值的贡献是**在你自己的环境里跑通并报告差异**，因为这套补丁的兼容性只能靠真实机器验证。

## 最有用的三类反馈

1. **兼容性报告**：Octop 版本、Python 版本、操作系统/架构、`doctor` 的完整输出（贴之前确认没有凭证）、`core_compatible` 与 `blockers`。
2. **行为差异**：某个按钮没反应、引用丢了、话题没隔离、附件打不开。带上复现步骤和你期望的行为。
3. **新环境适配**：Linux / Windows / 非标准目录 / 容器。请附上你改动的挂载点与验证结果。

`doctor` 输出默认不包含聊天正文与凭证，可以安全粘贴；如果你手动加了 `--state-dir` 等参数，贴之前再看一眼。

## 修改流程

不要直接改已安装的 portable 文件，也不要改构建产物 `assets/patch/staged/octop_gateway/channels/feishu_controls.py`（构建会覆盖）。权威源码位置见 [references/development.md](references/development.md)。

```sh
python3 scripts/employee_patch.py build
python3 scripts/employee_patch.py verify
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

三项都过再提 PR。改动涉及安装、回滚、监护或媒体路径时，请同时补测试。

## 提交约定

- 一个 PR 一件事，附上"改之前 / 改之后"的实际行为。
- 涉及飞书接口的改动，写明接口名与所需权限，并说明缺权限时的降级行为。
- 不要为了通过测试而放宽断言，也不要把未知的新版 Octop 源码直接覆盖进 `assets/patch/before/` 当基线。
- 不要在仓库里加入截图中的真实姓名、租户名、应用 ID、聊天记录或内部文档。

## 版本与发布

- `release.json` 的 `version` 随行为变化递增；`profile` 表示已验证的 Octop/Python 组合。
- 发布前必须 `build` → `verify` → 单测 → 打包，并在真实飞书账号上走一遍 [acceptance.md](references/acceptance.md)。
- 新增平台支持时，在 [references/environments.md](references/environments.md) 的表格里如实标注"已验证 / 仅检测"。

## 许可

提交即表示你同意以本仓库的 MIT 许可发布你的贡献。改写自 Octop 的文件保留其原始版权声明，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
