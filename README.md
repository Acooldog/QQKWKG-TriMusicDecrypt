# QKKDecrypt

`QKKDecrypt` 是 `A_QKKd` 的统一项目。

- 中文名：`QQ酷狗酷我音乐解密工具`
- 英文名：`QKKDecrypt`
- 项目地址：`O:\A_python\A_QKKd`
- QQ：`2622138410`

严禁倒卖，严禁商用。仅供学习交流使用。

## 分支

- `main`
  - 控制台版本
  - 薄 `main.py` 入口
  - 三层架构：`Presentation / Application / Infrastructure`
  - 打包形态：`onefile`
- `main-ui`
  - Flet 桌面 UI 版本
  - Win10 风格界面
  - UI 打包形态：`onedir + _internal + setup`

## 平台能力

- `QQ音乐`
  - 运行期解密
  - 要求 QQ 音乐保持运行
- `酷我音乐`
  - 运行期解密
  - 要求酷我保持运行
- `酷狗音乐`
  - 文件级离线解密
  - 不要求 KuGou 保持运行

## 架构

根目录只保留薄 `main.py`。

核心代码统一放在 `src/`：

- `src/Presentation`
  - CLI
  - 交互式控制台
  - UI 分支中的 Flet 界面
- `src/Application`
  - 批处理编排
  - 平台调度
  - timing 聚合
  - 输出冲突处理
- `src/Infrastructure`
  - 平台适配器
  - 配置仓储
  - 路径解析
  - 进程检测
  - 转码与内部资源定位

## 运行

### CLI

```powershell
O:\A_python\A_QKKd\.venv\Scripts\python.exe O:\A_python\A_QKKd\main.py qq decrypt --input "D:\QQMusic" --output "O:\A_python\A_QKKd\output"
```

```powershell
O:\A_python\A_QKKd\.venv\Scripts\python.exe O:\A_python\A_QKKd\main.py kuwo decrypt --input "D:\Kuwo" --output "O:\A_python\A_QKKd\output"
```

```powershell
O:\A_python\A_QKKd\.venv\Scripts\python.exe O:\A_python\A_QKKd\main.py kugou decrypt --input "O:\KuGou\KugouMusic" --output "O:\A_python\A_QKKd\output"
```

### 交互式控制台

```powershell
O:\A_python\A_QKKd\.venv\Scripts\python.exe O:\A_python\A_QKKd\main.py
```

交互模式会：

- 显示项目地址、QQ 与禁止倒卖/商用声明
- 询问是否直接使用配置
- 让用户选择平台
- 对 `QQ/酷我` 做阻断式进程检测
- 所有退出路径统一走 `按任意键退出`

## 配置

外部配置文件：`plugins/plugins.json`

命名空间：`decrypt_cli`

核心字段：

- `shared.output_dir`
- `shared.cli_collision_policy`
- `shared.recursive`
- `qq.input_dir`
- `qq.format_rules`
- `qq.process_match`
- `kuwo.input_dir`
- `kuwo.process_name`
- `kuwo.exe_path`
- `kuwo.signature_file`
- `kuwo.format_kwm`
- `kugou.input_dir`
- `kugou.kgg_db_path`
- `kugou.key_file`
- `kugou.target_format_kgma`
- `kugou.target_format_kgg`

## 输出冲突

三平台共享一个输出根目录。

- CLI：默认自动加平台后缀
  - 例如 `花海.qq.flac`
  - `花海.kuwo.mp3`
  - `花海.kugou.flac`
- 交互模式：运行时询问用户

## timing

三平台统一输出 timing。

- 单文件：`scan`、`dedupe`、`decrypt`、`transcode`、`publish`、`total`
- 批量：`batch_total`、`batch_avg`、`batch_hotspot`

## 转码

转码只允许调用内部包中的 `assets/ffmpeg*.exe`。

- 禁止调用系统 `ffmpeg`
- `QQ` 保留源格式级规则
- `酷我` 使用 `format_kwm`
- `酷狗` 使用 `target_format_kgma / target_format_kgg`

## 打包

```powershell
cd O:\A_python\A_QKKd
npm run package
```

打包行为：

- `main` 分支构建 `QKKDecrypt.exe`（onefile）
- `main-ui` 分支构建 `QKKDecrypt-UI-setup.exe`
- `release` 目录只保留本次新版本产物

运行时目录规则：

- 外部自动生成：
  - `plugins`
  - `_log`
  - `output`
- 内部：
  - 其他代码与非自动生成资源全部打入 onefile 或 `_internal`

## Flet 说明

`main-ui` 分支使用 `Flet`。

Flet 的桌面模式采用本地 Python 后端与 Flutter 前端协同运行，通过本地进程/本地通信通道驱动界面状态同步。UI 分支只替换 `Presentation` 层，不改业务核心层。

## 来源与致谢

- `QQ音乐` 解密模型思路参考项目：
  - [`qqmusic_decrypt`](https://github.com/luyikk/qqmusic_decrypt)
- 其他平台模型为自主逆向学习实现，基于学习交流与尊重正版的目的整理。

## 合规声明

- 仅供学习交流使用
- 请仅处理你本人拥有合法访问权限的本地文件
- 请遵守版权、平台协议与适用法律
- 禁止商用
- 禁止倒卖
- 倒卖者将举报平台并持续追责
