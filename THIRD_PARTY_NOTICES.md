# 第三方代码与商标声明

## Octop

本项目的若干运行时补丁文件包含复制并修改自 Octop 的源码：

- 上游仓库：https://github.com/TencentCloud/Octop
- 使用方式：改写、合并与重新分发（见仓库内 `assets/patch/before/`、`assets/patch/vendor/`、`assets/patch/staged/` 与 `assets/patch/released/`）
- 许可证：MIT

上述文件已被修改，用于飞书/Lark 通道的交互行为、任务控制与媒体收发。原始版权与许可声明如下：

```
MIT License

Copyright (c) 2026 Octop

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 商标与关联关系

Octop、飞书 / Feishu、Lark、Aily 及其标识归各自权利人所有。

本项目是第三方社区集成，**不是** Octop、飞书、Lark 或 Aily 的官方发行版，也**未获得**上述任何一方的背书、认证或支持。仓库中不包含上述产品的二进制分发，安装脚本只在你自己的机器上修改你已安装的 Octop 运行时文件，并保留可回滚备份。

安装前请自行确认：你的飞书企业允许使用自建应用与自定义机器人、你的 Octop 许可允许本地修改，以及你对被修改文件拥有操作权限。

## 其他依赖

媒体识别为可选能力，运行时会调用你自己安装的 FFmpeg、Octop 自带 OCR 与本机 Whisper 依赖。它们不随本仓库分发，许可与版本以各自项目为准（见 `assets/patch/media/requirements.lock` 与 `references/environments.md`）。
