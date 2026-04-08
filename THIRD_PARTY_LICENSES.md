# Third Party Licenses

本文档只做工程合规说明，不构成法律意见。

## 总体结论

- 当前两个 `QKKDecrypt` 仓库的作者自编源码，统一按 **GPLv3** 发布。
- UI 路线计划/实现依赖 **PySide6-Fluent-Widgets**，当前按其**非商业 GPLv3 路线**使用。
- 打包分发时，仍需同时遵守各第三方组件自己的许可证边界。

## 当前重点组件

### 1. PySide6 / Qt for Python

- UI 使用 `PySide6`
- Qt for Python 通常涉及：
  - `LGPLv3`
  - `GPLv3`
  - 商业许可
- 分发 UI 时，不应忽略 Qt 相关运行库的许可证要求

参考：
- https://doc.qt.io/qtforpython-6/commercial/index.html
- https://doc.qt.io/qtforpython-6/overviews/qtdoc-lgpl.html

### 2. PySide6-Fluent-Widgets / QFluentWidgets

- UI 计划/实现使用 `PySide6-Fluent-Widgets`
- 上游当前采用：
  - **非商业：GPLv3 路线**
  - **商业：需单独购买商业授权**

因此当前项目对它的使用边界应理解为：
- 当前仓库按 GPLv3 非商业开源路线发布
- 如果未来要做商业闭源分发，不能直接沿用当前依赖组合，必须先完成上游授权核验

参考：
- https://github.com/zhiyiYo/PyQt-Fluent-Widgets
- https://pypi.org/project/PySide6-Fluent-Widgets/

### 3. FFmpeg

当前仓库内置 FFmpeg 二进制构建信息包含：
- `--enable-gpl`
- `--enable-version3`

这意味着带该二进制一起分发时，不能只看仓库源码的 GPLv3，还需要同时遵守 FFmpeg 的许可证要求。

参考：
- https://ffmpeg.org/legal.html

### 4. 参考项目与思路来源

本项目 README 已注明：
- `qqmusic_decrypt` 等项目仅作为思路来源与致谢对象

工程上应继续避免：
- 直接把来源项目代码默认视为可任意再授权代码
- 在未重新核验许可证前，把外部项目代码大段并入再重新发布

## 推荐表述

建议在 README、发布页、安装包说明里统一使用类似口径：

> 本项目按 GPLv3 发布，并在 UI 路线中使用 PySide6 与 PySide6-Fluent-Widgets。项目仅面向非商业开源分发与学习研究场景。打包产物同时受 FFmpeg、Qt/PySide6 及其他第三方依赖的许可证约束；如需商业使用或闭源分发，请先自行完成许可证核验并取得必要授权。

## 不建议的表述

不建议直接写：
- “整个分发包只受 GPLv3 约束”
- “QFluentWidgets 可以直接用于任何商业闭源项目”
- “本项目天然适合任何商业用途”

这些表述在当前依赖组合下都不严谨。
