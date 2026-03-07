# QKKDecrypt

`QKKDecrypt` 是 `A_QKKd` 的统一解密项目，整合了：
- `QQ音乐`
- `酷我音乐`
- `酷狗音乐`

中文名：`QQ酷狗酷我音乐解密工具`

项目地址：`O:\A_python\A_QKKd`

联系 QQ：`2622138410`

严禁倒卖，严禁商用。

## 特性
- 薄 `main.py` 入口
- 三层架构：`Presentation / Application / Infrastructure`
- CLI 与无参数交互式控制台
- 三平台分别保存输入目录，共享一个输出目录
- 共享输出目录支持跨平台同名冲突处理
- 统一 timing 日志与批量报告
- 转码只调用内部包中的 `assets/ffmpeg*.exe`
- 打包后 `plugins`、`_log`、`output` 保持外部目录自动生成

## 平台说明
- `QQ音乐`：运行期解密，要求 QQ 音乐保持运行
- `酷我音乐`：运行期解密，要求酷我保持运行
- `酷狗音乐`：文件级离线解密，不要求 KuGou 保持运行

## CLI

```powershell
O:\A_python\A_QKKd\.venv\Scripts\python.exe O:\A_python\A_QKKd\main.py qq decrypt --input "D:\QQMusic" --output "O:\A_python\A_QKKd\output"
```

```powershell
O:\A_python\A_QKKd\.venv\Scripts\python.exe O:\A_python\A_QKKd\main.py kuwo decrypt --input "D:\Kuwo" --output "O:\A_python\A_QKKd\output"
```

```powershell
O:\A_python\A_QKKd\.venv\Scripts\python.exe O:\A_python\A_QKKd\main.py kugou decrypt --input "O:\KuGou\KugouMusic" --output "O:\A_python\A_QKKd\output"
```

## 交互式

```powershell
O:\A_python\A_QKKd\.venv\Scripts\python.exe O:\A_python\A_QKKd\main.py
```

交互式流程：
- 显示项目信息与免责声明
- 询问是否直接使用配置
- 选择平台
- 若是 `QQ/酷我`，运行前检测进程
- 未检测到时进入阻断等待，用户输入 `y` 后再次验证
- 所有交互式退出路径都会要求 `按任意键退出`

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
- CLI：默认自动加平台后缀，例如 `花海.qq.flac`
- 交互式：冲突时询问
  - 加平台后缀
  - 分平台子目录
  - 覆盖

## 转码
- 所有平台都只允许使用内部包中的 `assets/ffmpeg*.exe`
- 禁止调用系统 `ffmpeg`
- `QQ` 保留源格式级规则
- `酷我` 使用 `format_kwm`
- `酷狗` 使用 `target_format_kgma` / `target_format_kgg`

## 打包
打包脚本：`script/package.js`

```powershell
cd O:\A_python\A_QKKd
npm run package
```

打包结构：
- 外部：`plugins`、`_log`、`output`
- 内部：`_internal` 中的代码与运行资源

内部资源至少包括：
- `assets/ffmpeg-win-x86_64-v7.1.exe`
- `assets/kugou_key.xz`
- `assets/kudog_native.dll`
- `src/Infrastructure/platforms/kuwo/runtime_m/...`

## timing
统一 timing 字段：
- 单文件：`scan`、`dedupe`、`decrypt`、`transcode`、`publish`、`total`
- 批量：`batch_total`、`batch_avg`、`batch_hotspot`

## 免责声明
- 仅用于处理你本人拥有合法访问权限的本地文件
- 请遵守版权、平台协议与适用法律
- 严禁倒卖，严禁商用
