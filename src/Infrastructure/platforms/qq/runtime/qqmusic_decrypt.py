"""QQ音乐解密器 - Python版本

使用Frida调用QQMusicCommon.dll中的EncAndDesMediaFile类来解密加密音频文件
"""

import os
import hashlib
import shutil
import tempfile
import logging
import time
import frida


logger = logging.getLogger("qqmusic_decrypt.manager")


def is_ascii_path(path: str) -> bool:
    try:
        path.encode("ascii")
        return True
    except Exception:
        return False


def pick_safe_tmp_dir(output_dir: str) -> str:
    abs_output_dir = os.path.abspath(output_dir) if output_dir else output_dir
    drive, _ = os.path.splitdrive(abs_output_dir)

    candidates = []
    if drive:
        candidates.append(os.path.join(drive + os.sep, "_qqmusic_tmp"))

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    candidates.append(os.path.join(project_root, "_qqmusic_tmp"))
    candidates.append(tempfile.gettempdir())

    for candidate in candidates:
        if not candidate or not is_ascii_path(candidate):
            continue
        try:
            os.makedirs(candidate, exist_ok=True)
            return candidate
        except Exception:
            continue

    logger.warning("未找到可用的ASCII临时目录，回退到输出目录: %s", output_dir)
    return output_dir


class QQMusicDecryptor:
    """QQ音乐解密器"""

    def __init__(self, session):
        """初始化解密器

        Args:
            session: Frida session对象
        """
        self.session = session
        self.target_dll = "QQMusicCommon.dll"
        self.functions = {}
        self._initialize_functions()

    def _initialize_functions(self):
        """查找并初始化QQMusicCommon.dll中的解密函数"""
        logger.info("正在查找QQMusicCommon.dll...")

        # 由于Session对象没有get_module_by_name方法，我们需要在JavaScript中查找模块
        # 创建一个临时脚本来枚举模块和导出函数
        script_code = """
        var targetModule = Process.findModuleByName("QQMusicCommon.dll");
        if (!targetModule) {
          send({ type: "error", message: "未找到QQMusicCommon.dll" });
        } else {
          send({ type: "found_module", base: targetModule.base.toString(), size: targetModule.size });
          
          // 枚举导出函数
          var exports = targetModule.enumerateExports();
          var exportList = [];
          exports.forEach(function(exp) {
            if (exp.name.indexOf("EncAndDesMediaFile") !== -1) {
              exportList.push({ name: exp.name, address: exp.address.toString() });
            }
          });
          send({ type: "exports", data: exportList });
        }
        """

        script = self.session.create_script(script_code)

        # 用于接收JavaScript消息的变量
        module_info = {}
        export_functions = []

        def on_message(message, data):
            msg_type = message.get("type")
            if msg_type == "send":
                payload = message.get("payload", {})
                if payload.get('type') == 'found_module':
                    module_info['base'] = int(payload['base'], 16)
                    module_info['size'] = payload['size']
                elif payload.get('type') == 'exports':
                    for exp in payload['data']:
                        exp['address'] = int(exp['address'], 16)
                        export_functions.append(exp)
                elif payload.get("type") == "error":
                    logger.error("Frida模块枚举错误: %s", payload.get("message"))
                else:
                    logger.debug("Frida消息: %s", payload)
            elif msg_type == "error":
                logger.error("Frida脚本错误: %s", message.get("stack", message))
            else:
                logger.debug("Frida脚本消息: %s", message)

        script.on('message', on_message)
        script.load()

        # 等待脚本执行
        time.sleep(0.5)

        if not module_info:
            raise RuntimeError(f"未找到{self.target_dll}")

        logger.info("找到%s @ %s", self.target_dll, hex(module_info['base']))
        logger.info("正在查找相关导出函数...")

        if not export_functions:
            raise RuntimeError("未找到任何EncAndDesMediaFile相关函数")

        logger.info("找到 %s 个相关函数", len(export_functions))

        # 可能的函数名列表（考虑不同编译器版本）
        possible_names = {
            'constructor': [
                "??0EncAndDesMediaFile@@QAE@XZ",
                "??0EncAndDesMediaFile@@QEAA@XZ",
                "??0EncAndDesMediaFile@@IAAE@XZ"
            ],
            'destructor': [
                "??1EncAndDesMediaFile@@QAE@XZ",
                "??1EncAndDesMediaFile@@QEAA@XZ",
                "??1EncAndDesMediaFile@@IAAE@XZ"
            ],
            'open': [
                "?Open@EncAndDesMediaFile@@QAE_NPB_W_N1@Z",
                "?Open@EncAndDesMediaFile@@QEAA_NPEB_W_N1@Z"
            ],
            'getSize': [
                "?GetSize@EncAndDesMediaFile@@QAEKXZ",
                "?GetSize@EncAndDesMediaFile@@QEAAKXZ"
            ],
            'read': [
                "?Read@EncAndDesMediaFile@@QAEKPAEK_J@Z",
                "?Read@EncAndDesMediaFile@@QEAAKPEAEK_J@Z"
            ]
        }

        # 遍历从JavaScript获取的导出函数，查找目标函数
        for exp in export_functions:
            name = exp['name']
            address = exp['address']

            # 检查构造函数
            if 'constructor' not in self.functions:
                for possible_name in possible_names['constructor']:
                    if name == possible_name:
                        self.functions['constructor'] = address
                        logger.info("找到构造函数: %s @ %s", name, hex(address))
                        break

            # 检查析构函数
            if 'destructor' not in self.functions:
                for possible_name in possible_names['destructor']:
                    if name == possible_name:
                        self.functions['destructor'] = address
                        logger.info("找到析构函数: %s @ %s", name, hex(address))
                        break

            # 检查Open函数
            if 'open' not in self.functions:
                for possible_name in possible_names['open']:
                    if name == possible_name:
                        self.functions['open'] = address
                        logger.info("找到Open函数: %s @ %s", name, hex(address))
                        break

            # 检查GetSize函数
            if 'getSize' not in self.functions:
                for possible_name in possible_names['getSize']:
                    if name == possible_name:
                        self.functions['getSize'] = address
                        logger.info("找到GetSize函数: %s @ %s", name, hex(address))
                        break

            # 检查Read函数
            if 'read' not in self.functions:
                for possible_name in possible_names['read']:
                    if name == possible_name:
                        self.functions['read'] = address
                        logger.info("找到Read函数: %s @ %s", name, hex(address))
                        break

        # 检查是否所有函数都找到了
        required = ['constructor', 'destructor', 'open', 'getSize', 'read']
        missing = [f for f in required if f not in self.functions]

        if missing:
            raise RuntimeError(f"未找到所有必要的函数: {', '.join(missing)}")

        logger.info("所有函数都已找到！")
        logger.info("创建解密脚本...")

        # 创建解密脚本
        self._create_decrypt_script()

    def _create_decrypt_script(self):
        """创建并加载解密脚本"""
        script_code = f"""
        // 目标函数地址
        var constructorAddr = ptr("{hex(self.functions['constructor'])}");
        var destructorAddr = ptr("{hex(self.functions['destructor'])}");
        var openAddr = ptr("{hex(self.functions['open'])}");
        var getSizeAddr = ptr("{hex(self.functions['getSize'])}");
        var readAddr = ptr("{hex(self.functions['read'])}");
        
        // 创建NativeFunction包装器
        var Constructor = new NativeFunction(constructorAddr, "pointer", ["pointer"], "thiscall");
        var Destructor = new NativeFunction(destructorAddr, "void", ["pointer"], "thiscall");
        var Open = new NativeFunction(openAddr, "bool", ["pointer", "pointer", "bool", "bool"], "thiscall");
        var GetSize = new NativeFunction(getSizeAddr, "uint32", ["pointer"], "thiscall");
        var Read = new NativeFunction(readAddr, "uint", ["pointer", "pointer", "uint32", "uint64"], "thiscall");
        
        // 导出解密函数
        rpc.exports = {{
          decrypt: function (srcFileName, tmpFileName) {{
            try {{
              console.log("开始解密: " + srcFileName);
              
              // 1. 分配对象内存（0x28字节）
              var obj = Memory.alloc(0x28);
              
              // 2. 调用构造函数
              Constructor(obj);
              
              // 3. 转换路径为UTF-16
              var fileNameUtf16 = Memory.allocUtf16String(srcFileName);
              
              // 4. 打开加密文件
              var openResult = Open(obj, fileNameUtf16, 1, 0);
              console.log("打开文件结果: " + openResult);
              
              // 5. 获取文件大小
              var fileSize = GetSize(obj);
              console.log("文件大小: " + fileSize + " 字节");
              
              // 6. 分配缓冲区
              var buffer = Memory.alloc(fileSize);
              
              // 7. 读取解密后的数据
              var readResult = Read(obj, buffer, fileSize, 0);
              console.log("读取字节数: " + readResult);
              
              // 8. 转换为字节数组
              var data = buffer.readByteArray(fileSize);
              
              // 9. 释放对象资源
              Destructor(obj);
              
              // 10. 写入临时文件
              var tmpFile = new File(tmpFileName, "wb");
              tmpFile.write(data);
              tmpFile.close();
              
              console.log("解密完成: " + tmpFileName);
              return true;
            }} catch (e) {{
              console.log("解密出错: " + e);
              console.log("错误堆栈: " + e.stack);
              return false;
            }}
          }}
        }};
        
        console.log("解密脚本已加载");
        """

        # 创建并加载脚本
        self.decrypt_script = self.session.create_script(script_code)

        # 定义消息处理器
        def on_message(message, data):
            msg_type = message.get("type")
            if msg_type == "send":
                logger.info("[Frida] %s", message.get("payload"))
            elif msg_type == "error":
                logger.error("[Frida] %s", message.get("stack", message))
            elif msg_type == "log":
                logger.info("[Frida/log] %s", message.get("payload"))
            else:
                logger.debug("[Frida/other] %s", message)

        self.decrypt_script.on("message", on_message)
        self.decrypt_script.load()
        logger.info("解密脚本加载成功！")

    def decrypt(self, src_file, dest_file):
        """解密QQ音乐加密文件

        Args:
            src_file: 源文件路径（加密文件）
            dest_file: 目标文件路径（解密后文件）

        Returns:
            bool: 解密是否成功
        """
        try:
            # 调用解密函数
            result = self.decrypt_script.exports_sync.decrypt(
                src_file, dest_file)
            return result
        except Exception:
            logger.exception("解密出错: src=%s, dest=%s", src_file, dest_file)
            return False


def Decryptor_main(input_dir="", output_dir="", del_original=False):
    """主程序：解密QQ音乐下载的加密音频文件

    Args:
        input_dir: 输入目录（QQ音乐下载目录）
        output_dir: 输出目录
        del_original: 是否删除原始加密文件（.mflac/.mgg）
    """
    start_time = time.perf_counter()
    logger.info("输入目录: %s", input_dir)
    logger.info("输出目录: %s", output_dir)
    logger.info("删除原文件: %s", del_original)

    # 获取Frida版本和本地设备
    logger.info("Frida version: %s", frida.__version__)
    device_manager = frida.get_device_manager()
    device = device_manager.get_local_device()
    logger.info("Device name: %s", device.name)

    # 查找QQ音乐进程
    try:
        processes = device.enumerate_processes()
        qq_music_process = next(
            (p for p in processes if "qqmusic" in p.name.lower()),
            None
        )
        if not qq_music_process:
            raise RuntimeError("请先启动QQ音乐")

        logger.info("找到QQ音乐进程: PID=%s", qq_music_process.pid)
    except Exception as e:
        raise RuntimeError(f"查找QQ音乐进程失败: {e}")

    # 附加到QQ音乐进程
    session = device.attach(qq_music_process.pid)

    # 初始化解密器（会自动查找并加载所需的函数）
    try:
        decryptor = QQMusicDecryptor(session)
    except Exception:
        logger.exception("初始化解密器失败")
        return

    # 构造QQ音乐下载目录路径
    qq_music_dir = input_dir

    if not os.path.exists(qq_music_dir):
        raise RuntimeError(f"QQ音乐下载目录不存在: {qq_music_dir}")

    logger.info("QQ音乐目录: %s", qq_music_dir)

    # 创建输出目录
    output_dir_path = output_dir
    if not os.path.exists(output_dir_path):
        os.makedirs(output_dir_path)
    logger.info("输出目录: %s", output_dir_path)
    tmp_base_dir = pick_safe_tmp_dir(output_dir_path)
    if tmp_base_dir != output_dir_path:
        logger.info("使用临时目录写入: %s", tmp_base_dir)

    # 遍历QQ音乐下载目录中的文件
    processed_count = 0
    skipped_count = 0
    failed_count = 0

    for entry in os.listdir(qq_music_dir):
        file_path = os.path.join(qq_music_dir, entry)
        if not os.path.isfile(file_path):
            continue

        # 处理mflac和mgg文件
        _, ext = os.path.splitext(entry)
        if ext.lower() in [".mflac", ".mgg"]:
            # 映射文件扩展名：mflac→flac, mgg→ogg
            new_ext = "flac" if ext.lower() == ".mflac" else "ogg"
            base_name = os.path.splitext(entry)[0]
            new_file_name = base_name + "." + new_ext
            new_file_path = os.path.join(output_dir_path, new_file_name)
            logger.info("开始处理文件: %s -> %s", file_path, new_file_path)

            # 跳过已存在的文件
            if os.path.exists(new_file_path):

                logger.info("文件已存在，跳过处理: %s", new_file_path)
                # 如果开启删除原文件
                if del_original:
                    try:
                        os.remove(file_path)
                        logger.info("已删除原文件: %s", file_path)
                    except Exception as e:
                        logger.warning("删除原文件失败: %s, %s", file_path, e)
                skipped_count += 1
                continue

            # 生成MD5哈希作为临时文件名
            md5_hash = hashlib.md5(new_file_name.encode()).hexdigest()
            tmp_file_path = os.path.join(tmp_base_dir, md5_hash)

            try:
                # 调用解密器进行解密
                success = decryptor.decrypt(file_path, tmp_file_path)

                if success:
                    # 将临时文件移动为最终文件名
                    try:
                        shutil.move(tmp_file_path, new_file_path)
                        logger.info("处理文件完成: %s", new_file_path)
                    except Exception as e:
                        logger.error(
                            "移动临时文件失败: %s -> %s, %s",
                            tmp_file_path,
                            new_file_path,
                            e,
                        )
                        if os.path.exists(tmp_file_path):
                            os.remove(tmp_file_path)
                        failed_count += 1
                        continue

                    # 如果开启删除原文件
                    if del_original:
                        try:
                            os.remove(file_path)
                            logger.info("已删除原文件: %s", file_path)
                        except Exception as e:
                            logger.warning("删除原文件失败: %s, %s", file_path, e)

                    processed_count += 1
                else:
                    logger.error("解密失败: %s", file_path)
                    failed_count += 1
                    # 清理临时文件
                    if os.path.exists(tmp_file_path):
                        os.remove(tmp_file_path)

            except Exception as e:
                logger.exception("处理文件失败: %s, %s", file_path, e)
                failed_count += 1
                # 清理临时文件
                if os.path.exists(tmp_file_path):
                    os.remove(tmp_file_path)

    elapsed = time.perf_counter() - start_time
    logger.info(
        "处理完成！成功: %s, 跳过: %s, 失败: %s, 耗时: %.2f 秒",
        processed_count,
        skipped_count,
        failed_count,
        elapsed,
    )
    return (
        True,
        f"成功处理 {processed_count} 个文件，跳过 {skipped_count} 个文件，失败 {failed_count} 个文件",
    )
