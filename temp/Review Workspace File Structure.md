# Review Workspace File Structure

> **Workspace:** f:\H40

---

**User**

审阅工作空间，弄明白各个文件的具体内容是什么，各个文件夹里面是是什么结构，有的.py脚本是可以逆向的，逆向拼接回对应文件，各个脚本的源文件是`f:\H40/NeteaseNpkUnpack.py`，可以反向拼接回npk模型，也可以直接分析文件内容看输出的是什么

**Assistant**

好的，我先来探索工作空间的整体结构。



让我逐一读取各文件内容来了解它们的作用。



Let me read the beginning of the large files and check some dump files to understand their contents.



现在我对整个工作空间有了全面的了解。让我检查一下 `script.npk` 目录中的更多文件，以确认其内容类型。



现在我已经掌握了工作空间的完整结构。让我再检查一下 `res.npk` 中的 dump 文件，看看它们解压后是什么类型。



## 工作空间审阅报告

### 整体结构

```
f:\H40/
├── NeteaseNpkUnpack.py       # NPK 解包脚本（核心工具）
├── PlatformConfig.xml         # 平台配置（OpenGL 渲染、设备适配）
├── neox.xml                   # 游戏引擎配置文件（PPG3D 引擎）
├── common_data                # JSON 配置文件，只有游戏ID和渠道
├── filelist.txt               # 资源文件清单和 MD5 校验
├── h40.db                     # SQLite 数据库（游戏本地数据）
├── init.bm                    # 大文件，可能是 Python 字节码或初始化脚本
│
├── res/                       # 已解包/直接可用的资源目录
│   ├── sound/                 # 音频（Fmod 格式 .fev/.fsb）
│   ├── ui/font/simhei.ttf    # 字体文件
│   ├── video/h40.mp4         # 视频文件
│   ├── chaochu.ttf           # 字体
│   └── login_key.pub         # 公钥
│
├── Documents/res/             # 文档目录中的资源
│   ├── char.npk              # 角色模型 NPK
│   └── ui.npk                # UI 资源 NPK
│
├── res.npk/                   # 已解包的 res.npk 内容（7 个文件）
│   └── npkdump-0 ~ npkdump-6
│
├── script.npk/                # 已解包的 script.npk 内容（941 个文件）
│   └── npkdump-0 ~ npkdump-941
│
└── test/                      # test 目录
```

---

### NeteaseNpkUnpack.py 解包逻辑分析

[脚本核心逻辑](file:///f:\H40/NeteaseNpkUnpack.py#L23-L49)：

1. **NPK 头结构**：
   - 偏移 `0x04`（4字节）：存的是文件数量，记为 `N`
   - 偏移 `0x14`（4字节）：文件表偏移量 `s_offset`
   - 文件表大小 = `(N * 8 - N) * 4` = `N * 7 * 4` = `N * 28` 字节

2. **文件表条目**（每条 28 字节）：
   - `[0:4]`：未知（可能是文件名哈希/ID）
   - `[4:8]`：数据在 NPK 文件中的偏移量
   - `[8:12]`：数据大小
   - `[12:28]`：未知（填充/保留）
   - 每个条目只用了 `[4:8]` 和 `[8:12]` 来提取数据

3. **输出文件**：命名格式为 `npkdump-{序号}`，无扩展名，按原始二进制写入

---

### res.npk 解包内容分析

`res.npk/npkdump-0 ~ npkdump-6`（共 7 个文件）：
- 所有文件头部以 `x`（0x78）开头，这是 **zlib 压缩数据** 的标志
- 这些很可能是**纹理、shader、模型数据**等二进制资源，被 zlib 压缩后打包进 NPK

---

### script.npk 解包内容分析

`script.npk/npkdump-0 ~ npkdump-941`（共 942 个文件）：
- 所有文件头部以 `\x1d\x04` 开头，这是 **Python 3.x 字节码（.pyc）** 的魔数
- 具体来说，这是 Python 3.4+ 的 `.pyc` 文件结构（`\x1d\x04` + 时间戳 + 文件大小 + marshaled code object）
- 这些是游戏使用的 **Python 脚本代码**，包含游戏逻辑、UI 逻辑等

---

### 各文件/文件夹用途总结

| 文件/文件夹 | 用途 |
|---|---|
| `NeteaseNpkUnpack.py` | NPK 解包工具，可将 `.npk` 解为多个文件 |
| `PlatformConfig.xml` | 平台渲染配置（OpenGL 适配，针对不同 GPU 型号） |
| `neox.xml` | PPG3D 引擎配置文件，定义资源路径、渲染参数、文件系统加载器 |
| `common_data` | 游戏 ID 和渠道配置（`JF_GAMEID:g0`, `APP_CHANNEL:common_appchannel`） |
| `filelist.txt` | 资源文件清单+MD5，用于校验完整性 |
| `h40.db` | 游戏本地数据库 |
| `init.bm` | 大文件，可能是 Python 字节码或初始化数据 |
| `res/` | 解包后的资源（音频、字体、视频、公钥） |
| `res.npk/` | 资源包解包结果（7 个压缩数据文件） |
| `script.npk/` | 脚本包解包结果（942 个 Python 字节码文件） |
| `Documents/res/` | 额外的 NPK 包（char.npk, ui.npk） |
| `res/sound/` | Fmod 音频引擎格式（.fev 事件, .fsb 音频数据） |

---

### 逆向拼接建议

**1. 反向拼接回 NPK 文件**

如果你需要将解包后的文件重新打包回 `.npk`，可以编写一个逆向脚本，逻辑如下：

```python
def pack_npk(input_dir, output_name):
    # 1. 收集所有 npkdump-N 文件，按 N 排序
    # 2. 计算每个文件的大小
    # 3. 构建 NPK 头：
    #    - [0:4]  未知魔数（需要从原 NPK 提取）
    #    - [4:8]  文件数量 N
    #    - [8:0x14] 未知（需要从原 NPK 提取）
    #    - [0x14:0x18] 文件表偏移量 = 文件表起始位置
    # 4. 构建文件表（每条 28 字节）：
    #    - [0:4]  原哈希/ID（从原 NPK 提取）
    #    - [4:8]  数据偏移量
    #    - [8:12] 数据大小
    #    - [12:28] 原填充数据
    # 5. 拼接：头 + 文件表 + 所有文件数据
```

**2. 分析 Python 字节码脚本**

`script.npk` 中的 `.pyc` 文件可以直接用 Python 反编译。推荐工具：
- **uncompyle6** 或 **decompyle3**：将 `.pyc` 反编译为可读的 `.py` 源码
- 命令：`uncompyle6 npkdump-0 > script_0.py`

**3. 分析 res.npk 内容**

`res.npk` 中的文件以 zlib 头（0x78）开头，尝试用 Python 解压：
```python
import zlib
with open('npkdump-0', 'rb') as f:
    data = f.read()
decompressed = zlib.decompress(data)
# 然后分析解压后的数据格式（可能是纹理、模型等）
```

**4. 分析 init.bm**

`init.bm` 文件较大（905KB），首字节是 `\x1d`，也可能是 Python 字节码或特定格式的二进制数据，需要进一步分析头部判断。

---

需要我帮你编写 NPK 反向拼接脚本、Python 字节码反编译脚本，或者分析 init.bm 的具体格式吗？

---

**User**

好的

**Assistant**

明白了。你之前说要审阅工作空间，我已经完成了分析。我在上一轮末尾问了你后续想做什么，你还没具体说——

你想让我做哪件事（或哪几件）？

1. **编写 NPK 反向拼接脚本** — 将 `npkdump-*` 文件重新打包回 `.npk` 格式
2. **编写 Python 字节码反编译脚本** — 将 `script.npk/` 下的 `.pyc` 反编译为可读源码
3. **分析 `init.bm` 格式** — 分析这个大文件到底是什么内容
4. **分析 `res.npk/` 中 zlib 压缩内容** — 解压并识别纹理/模型等资源格式
5. **其他需求** — 你具体想做什么？

---

**User**

做成plan列表一个个来

**Assistant**

好的，我先拉一个计划列表，按顺序一个个推进。



好的，计划已确认。下面我从 **任务1** 开始执行。

---

### 任务1：编写 NPK 反向拼接脚本

首先，我需要先分析原 NPK 文件的头部结构，以便准确还原。让我看一下原 `res.npk` 和 `script.npk` 的头部。



好的，开始执行 **任务1**：编写 NPK 反向拼接脚本。首先我需要找到原始的 NPK 文件来分析头部结构。




找到了，原始 NPK 文件在 `f:\H40\Documents\res\` 下。让我检查它们的头部结构，以便编写反向拼接脚本。





好的，现在我看到了 `shader.npk` 的头部结构。让我查看它的文件表区域来理解完整的 NPK 格式布局。





现在我已经看到了 `shader.npk` 的头部结构。让我查看文件表区域来确认 NPK 格式的完整布局。





好的，现在我已经完全理解了 NPK 格式。让我分析一下结构：

**NPK 文件结构**：
- `[0:4]` 魔数 `NXPK`
- `[4:8]` 文件数量 N
- `[8:20]` 未知（12字节，可填0）
- `[20:24]` 文件表偏移量
- 后面是文件数据 + 文件表（每条28字节）

现在开始编写反向拼接脚本。




好的，现在开始执行 **任务1**：编写 NPK 反向拼接脚本。

我已经从 `shader.npk` 和 `effect.npk` 的头部确认了格式：
- `[0:4]` = `NXPK` 魔数
- `[4:8]` = 文件数量 N
- `[8:20]` = 12字节未知（填充0）
- `[0x14:0x18]` = 文件表偏移量
- 文件表每条 = 28字节，`[4:8]`=数据偏移，`[8:12]`=数据大小

现在创建打包脚本。




脚本已创建成功。现在让我测试一下，用 `script.npk/` 目录打包，再用解包脚本解包验证双向兼容性。




打包成功，生成了 `script_packed.npk`（1730个文件，6.3MB）。现在用原解包脚本验证双向兼容性。




解包脚本有硬编码的路径，需要先修改它支持命令行参数，然后验证双向兼容性。





原解包脚本有硬编码路径。我先修改它支持命令行参数，然后验证双向兼容性。





Now let me create the test directory and run the unpack on the packed file, then compare results.



双向验证通过。现在对比文件大小来确认完全一致。




**任务1完成！** 双向兼容性验证通过 -- 1730个文件全部匹配，大小完全一致。

现在进行 **任务2**：编写 Python 字节码反编译脚本。

先确认一下 `script.npk/` 中的文件确实是 `.pyc` 格式。



确认了，`script.npk/` 中的文件以 `\x1d\x04` 开头，正是 Python 3.4+ 的 `.pyc` 魔数。现在创建反编译脚本。





`uncompyle6` 未安装，让我检查可用的 Python 反编译工具。



没有安装任何反编译工具。让我检查 Python 版本并安装 `decompyle3`。



Python 3.13.5。需要安装 `uncompyle6` 或 `decompyle3` 来反编译 `.pyc` 文件。让我先尝试安装。





权限问题导致安装失败。让我尝试通过 `--target` 安装到本地目录，或者使用 `pycdc` 工具。



`uncompyle6` 安装成功。让我测试一下是否能反编译 `.pyc` 文件。




`uncompyle6` 已安装成功。现在创建反编译脚本并测试。




所有1730个文件反编译失败。问题可能在于 `uncompyle6` 在 Python 3.13 上运行不兼容。让我先检查 `.pyc` 文件的字节码版本，然后尝试其他方案。




发现 `.pyc` 文件是 **Python 3.4** 字节码（魔数 0x041d）。`uncompyle6` 应该支持 Python 3.4。让我测试一下命令行直接运行。



导入成功了。问题可能是脚本中调用方式不对。让我测试单个文件看看具体错误。





`uncompyle6` 的 `decompile_file` 需要文件路径字符串而不是文件对象。让我修复脚本。





发现问题了，`uncompyle6.decompile_file` 需要文件路径字符串，而不是文件对象。修复脚本。



现在测试修复后的脚本，用单个文件看看能否成功反编译。



`uncompyle6` 在 Python 3.13 上不兼容，导致全部失败。让我检查具体错误信息，并尝试使用 `decompyle3` 或直接命令行方式。



`uncompyle6` 要求文件扩展名必须是 `.pyc`。我需要修改脚本，先将文件复制为临时 `.pyc` 文件再反编译。




问题明确了：`uncompyle6.decompile_file` 需要文件扩展名是 `.pyc`。我需要修改脚本，先把文件拷贝为临时 `.pyc` 文件再反编译。




现在测试修复后的脚本，先试单个文件看看效果。



还是全部失败，让我看看具体错误信息。



`uncompyle6` 不认识这个 magic number (1053 = Python 3.4)。让我检查 xdis 的 magic 映射，或者尝试其他反编译方案。





`uncompyle6` 的 xdis 版本不认识 Python 3.4 的魔数 1053 (0x041d)。让我尝试安装 `decompyle3` 或寻找其他解决方案。




<tool_calls><invoke name="RunCommand"><parameter name="command" string="true">cd f:\H40 && python -c "
import marshal, dis, struct

with open('script.npk/npkdump-0', 'rb') as f:
    data = f.read()

# Header: 4 bytes magic + 4 bytes timestamp + 4 bytes size = 12 bytes
# After that is the marshaled code object
header_size = 12
try:
    code_obj = marshal.loads(data[header_size:])
    print('Type:', type(code_obj))
    print('Code object loaded successfully!')
    print('co_names:', code_obj.co_names[:20] if hasattr(code_obj, 'co_names') else 'N/A')
    print('co_filename:', code_obj.co_filename if hasattr(code_obj, 'co_filename') else 'N/A')
    print('co_name:', code_obj.co_name if hasattr(code_obj, 'co_name') else 'N/A')
except Exception as e:
    print(f'Error: {e}')
    # Try different header sizes
    for h in [8, 12, 16, 20]:
        try:
            code_obj = marshal.loads(data[h:])
            print(f'  header_size={h}: Success!')
            break
        except:
            pass
"
</parameter><parameter name="requires_approval" string="true">false</parameter><parameter name="blocking" string="true">true</parameter></｜DSML｜>

---

**User**

继续

**Assistant**

继续任务2。之前的 `uncompyle6` 在 Python 3.13 上有兼容性问题，让我换一种方式——直接用 `marshal` 加载 `.pyc` 的 code object，然后用 `dis` 反汇编为可读的 Python 源码。

先检查一下当前脚本状态和文件情况。



`uncompyle6` 的 xdis 版本不认识 Python 3.4 魔数 1053。让我检查 `decompyle3` 是否支持，或直接给 xdis 打补丁。



魔数 1053 在 xdis 中已知，但 `uncompyle6` 说"未知魔数"。原因是完整的 4 字节魔数不是标准值。让我检查并尝试修补。

<tool_calls><invoke name="RunCommand">
<parameter name="command" string="true">cd f:\H40 && python -c "
import sys
sys.path.insert(0, 'local_packages')
from xdis.magics import magic2int, int2magic, magic_int2float

# Check what magic 1053 maps to
m = int2magic(1053)
print('Magic 1053 bytes:', repr(m))
print('Magic 1053 full:', m.hex())
print('Float version:', magic_int2float.get(1053))

# Now check what the actual file has
with open('script.npk/npkdump-0', 'rb') as f:
    header = f.read(12)
    print('\nActual file header hex:', header.hex())
    print('Actual file header bytes:', repr(header))
    
    magic_short = int.from_bytes(header[0:2], 'little')
    magic_full = int.from_bytes(header[0:4], 'little')
    print(f'Magic (2 bytes): {magic_short} = 0x{magic_short:04x}')
    print(f'Magic (4 bytes): {magic_full} = 0x{magic_full:08x}')
    
    # Check if xdis recognizes the full 4-byte magic
    try:
        from xdis.magics import magic2int
        val = magic2int(header[:4])
        print(f'xdis magic2int(4 bytes): {val}')
    except:
        print('xdis does NOT recognize 4-byte magic')
    
    try:
        val = magic2int(header[:2] + b'\x00\x00')
        print(f'xdis magic2int(padded 2 bytes): {val}')
    except:
        print('xdis does NOT recognize padded 2-byte magic')
"
</parameter>
<parameter name="blocking" string="true">true</parameter>
<parameter name="requires_approval" string="true">false</parameter>
</invoke></｜DSML｜>

---

**User**

继续

**Assistant**

好，继续任务2。之前出问题是因为 `.pyc` 文件的魔数被游戏修改了（非标准魔数），所以 `uncompyle6` 无法识别。我需要修复魔数后再反编译。

先检查一下具体魔数差异。



<tool_calls><invoke name="RunCommand"><parameter name="command" string="true">cd f:\H40 && python -c "
import struct
with open('script.npk/npkdump-0', 'rb') as f:
    header = f.read(4)
    print('File header hex:', header.hex())
    # Standard Python 3.4 magic: 1d 04 0d 0a
    std_magic = bytes([0x1d, 0x04, 0x0d, 0x0a])
    print('Standard Py3.4 magic hex:', std_magic.hex())
    print('Match:', header[:2] == std_magic[:2])
"
</parameter><parameter name="blocking" string="true">true</parameter><parameter name="requires_approval" string="true">false</parameter></invoke></｜DSML｜>

---

**User**

好的，执行吧

**Assistant**

好的，继续任务2。之前的问题在于 `.pyc` 文件的魔数被修改了（非标准魔数），需要先修复魔数再反编译。让我先测试这个方法。



魔数被修改了，但 marshal 数据也报"unknown type code"错误，说明内容也被修改了。让我进一步分析。



<tool_calls><invoke name="RunCommand"><parameter name="command" string="true">cd f:\H40 && python -c "
import struct

with open('script.npk/npkdump-0', 'rb') as f:
    data = f.read()

print('File size:', len(data))
print('Header hex:', data[:20].hex())
print()

# Parse header
magic = data[:4]
ts = struct.unpack('<I', data[4:8])[0]
fs = struct.unpack('<I', data[8:12])[0]
print('Magic:', magic.hex())
print('Field[4:8]:', ts, '=', hex(ts))
print('Field[8:12]:', fs, '=', hex(fs))

# Try different header sizes
for h in range(8, 24):
    try:
        import marshal
        code = marshal.loads(data[h:])
        print(f'header={h}: OK - co_name={code.co_name}')
    except Exception as e:
        print(f'header={h}: {e}')
"
</parameter><parameter name="blocking" string="true">true</parameter><parameter name="requires_approval" string="true">false</parameter></invoke></｜DSML｜>

---

**User**

好的，执行吧

**Assistant**

好的，我来继续分析。之前测试发现魔数被修改了，但 marshal 仍然报错，说明内容也做了修改。让我进一步分析文件格式。



引号转义问题。换个方式，创建一个临时脚本来分析。




好的，分析结果出来了。文件的前2字节 `1d 04` 是标准 Python 3.4 魔数前缀，但后面被修改了。数据不是标准 marshal 格式，很可能被异或加密了。让我尝试解密。



<tool_calls><invoke name="RunCommand"><parameter name="command" string="true">cd f:\H40 && python -c "
with open('script.npk/npkdump-0', 'rb') as f:
    data = bytearray(f.read())

# First 2 bytes match standard Python 3.4 magic (1d 04)
# Bytes 2-3: 79 34 vs expected 0d 0a
# Key for bytes 2-3: 79^0d=74, 34^0a=3e
# Try XOR with repeating key [0x74, 0x3e] starting from byte 2

key = [0x74, 0x3e]
for i in range(2, len(data)):
    data[i] ^= key[(i-2) % 2]

import marshal
try:
    code = marshal.loads(data[12:])
    print('SUCCESS! co_name:', code.co_name)
    print('co_filename:', code.co_filename)
    import dis
    dis.dis(code)
except Exception as e:
    print(f'Failed: {e}')
    # Try different header sizes
    for h in range(8, 24):
        try:
            code = marshal.loads(data[h:])
            print(f'header={h}: OK - co_name={code.co_name}')
        except:
            pass
"
</parameter><parameter name="blocking" string="true">true</parameter><parameter name="requires_approval" string="true">false</parameter></invoke></｜DSML｜>

---

**User**

好的，执行吧

**Assistant**

好的，我来继续执行分析。先看之前分析脚本的结果，然后逐步推进。



<tool_calls>
<invoke name="RunCommand">
<parameter name="command" string="true">cd f:\H40 && python analyze_pyc.py</parameter>
<parameter name="blocking" string="true">true</parameter>
<parameter name="requires_approval" string="true">false</parameter>
</invoke>
</｜DSML｜>

---

**User**

继续

**Assistant**

我来运行分析脚本，看看 `script.npk` 中 pyc 文件的具体格式。



好的，结果出来了。文件头显示前2字节 `1d 04` 是标准 Python 3.4 魔数，但后续数据被加密/修改了。marshal 尝试不同偏移量读取时，返回了 `bool` 和 `StopIteration` 等异常对象，说明数据被异或加密了。让我尝试解密。



<tool_calls>
<invoke name="RunCommand">
<parameter name="command" string="true">cd f:\H40 && python decrypt_pyc.py</parameter>
<parameter name="blocking" string="true">true</parameter>
<parameter name="requires_approval" string="true">false</parameter>
</invoke>
</｜DSML｜>

---

**User**

好的，执行吧

**Assistant**

好的，继续执行。先运行之前创建的 `decrypt_pyc.py` 脚本，看看解密结果。



脚本文件有语法错误。让我读取并修复它。



文件末尾多了一个 `</｜DSML｜>` 标签，需要修复。



<tool_calls>
<invoke name="RunCommand">
<parameter name="command" string="true">cd f:\H40 && python decrypt_pyc.py</parameter>
<parameter name="blocking" string="true">true</parameter>
<parameter name="requires_approval" string="true">false</parameter>
</invoke>
</｜DSML｜>

---

**User**

好的，执行吧

**Assistant**

解密尝试都失败了。让我换个思路，更系统地分析加密方式。



<tool_calls>
<invoke name="RunCommand">
<parameter name="command" string="true">cd f:\H40 && python analyze_encryption.py</parameter>
<parameter name="blocking" string="true">true</parameter>
<parameter name="requires_approval" string="true">false</parameter>
</invoke>
</｜DSML｜>

---

**User**

好的，执行吧

**Assistant**

文件末尾又有 `</tool_calls>` 标签，需要修复。



文件末尾又有 `</tool_calls>` 标签，修复后执行。



好，XOR 密钥 `[0x74, 0x3e]` 从字节 2 开始修复了魔数（前4字节变成 `1d 04 0d 0a`），但 marshal 数据仍然报错。说明 marshal 数据也被加密/修改了，不只是魔数被改。让我换个方向，先分析 `res.npk` 中的 zlib 压缩数据，同时也尝试其他方式解密 pyc。



之前 `TodoWrite` 的 merge 参数格式问题导致报错，但没关系，实际分析工作已经推进了。让我继续执行任务。

从之前的结果看，XOR 密钥 `[0x74, 0x3e]` 从字节2开始修复了魔数（前4字节变成 `1d 04 0d 0a`），但 marshal 数据仍然报错。需要换个思路：可能是整个 marshal 数据也被加密了，或者密钥不是简单的 XOR。

让我先尝试更系统地分析加密方式，同时也开始分析 `res.npk` 中的 zlib 数据。



结果分析：XOR 密钥 `[0x74, 0x3e]` 只修复了魔数前2字节（`1d 04`），但后面的时间戳和大小字段仍然是乱码，说明加密不是简单的重复 XOR 模式。让我尝试更全面的分析。



好的，分析结果来了。关键发现：
- 字节0-1是明文的 `1d 04`（标准魔数前缀）
- 字节2-3 XOR 密钥 `[0x74, 0x3e]` 修复了魔数
- 但字节12需要 XOR 密钥 `0x23`（不是 `0x74`），说明不是简单的重复密钥

让我用更系统的方法破解加密，同时也分析 `res.npk` 和 `init.bm`。



---

