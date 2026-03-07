# Third Party Licenses

本文件只做工程合规说明，不构成法律意见。

## 结论

`QKKDecrypt` 仓库中的**作者自有源码**可以继续使用 `MIT`。

但**当前分发包不是 MIT-only**。如果你分发打包后的可执行文件、安装包或内置运行库，必须同时遵守第三方组件各自的许可证。

## 当前最需要注意的风险点

### 1. PySide6 / Qt for Python

- 当前 `main-ui` 桌面界面使用 `PySide6`
- `PySide6 / Qt for Python` 不属于 MIT
- 其许可体系通常为：
  - `LGPLv3`
  - `GPLv3`
  - 商业许可

工程上更准确的表述是：
- 本项目 UI 版本动态链接 `PySide6 / Qt` 运行库
- 用户可以替换对应的共享库版本
- 分发 UI 安装包时，不能把整个安装包简单宣传成“纯 MIT 软件”

参考：
- Qt for Python licensing: https://doc.qt.io/qtforpython-6/commercial/index.html
- Qt LGPL overview: https://doc.qt.io/qtforpython-6/overviews/qtdoc-lgpl.html

### 2. FFmpeg

当前仓库内置的 FFmpeg 二进制为：

- `assets/ffmpeg-win-x86_64-v7.1.exe`

该构建的 `-version` 输出包含：

- `--enable-gpl`
- `--enable-version3`

这说明当前内置 FFmpeg 是 **GPL 构建**，而不是可直接按 MIT 叙述的独立组件。

工程含义：
- 仓库源码可以仍为 MIT
- 但**带这个 FFmpeg 一起分发的安装包/可执行包，不应宣称为 MIT-only**

当前最直接的 GPL 风险点就是它。

如果后续要降低合规复杂度，优先级最高的动作是：
- 替换当前内置 FFmpeg
- 改为许可证边界更清晰、你明确可接受的构建

参考：
- FFmpeg legal: https://ffmpeg.org/legal.html

### 3. `qqmusic_decrypt` 参考项目

本项目 README 已明确声明：

- `QQ 音乐` 解密模型思路参考自 [`qqmusic_decrypt`](https://github.com/luyikk/qqmusic_decrypt)

当前工程上建议这样处理：
- 只把它当作**思路来源与致谢对象**
- 不要简单把对应来源代码视为“天然可并入 MIT 再授权”
- 如果未来要更严格发布，应再次核验该来源项目的许可证、代码引用边界和归属情况

## 建议采用的对外说法

建议在 README、发布页和关于界面统一使用下面这类表述：

> 本项目仓库中由作者自行编写的源码部分采用 MIT 协议。  
> 当前分发包包含 PySide6 / Qt 运行库与 FFmpeg 等第三方组件，整体分发形态不是 MIT-only；第三方组件仍受其各自许可证约束。

## 不建议的表述

不建议直接写：

> 整个项目就是 MIT

或者：

> 所有分发包都是 MIT

这两种说法在当前打包结构下都不严谨。
