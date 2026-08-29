# 包逗固件修改、编译与烧录指南

这份文档同时面向维护者和后续 Coding Agent。目标是让接手者在不泄露本地密钥、不误伤舵机机械结构的前提下，快速理解当前烧录版本、修改固件、完成编译烧录并判断机器人是否正常启动。

## 1. 当前机器人运行的是什么

当前实机使用 **ESP32 直连豆包 Realtime** 的固件路径：

```text
PDM 麦克风
  -> ESP32 PCM16 16 kHz 上行
  -> 豆包 Realtime（ASR + 对话模型 + 流式 TTS）
  -> ESP32 PCM16 24 kHz 流式播放
  -> MAX98357 + 喇叭
```

它不依赖自建语音服务器完成日常对话。设备启动后会直接建立：

```text
wss://openspeech.bytedance.com/api/v3/duplex/realtime/dialogue
```

当前能力包括：

- 全双工连续对话和语音打断；
- 豆包 Realtime 直接返回流式语音；
- 大模型通过工具控制头部、表情、音量和收音灵敏度；
- 用户询问镜头前物体时，Realtime 调用 `inspect_camera`，设备拍摄 OV2640 画面并交给 Seed VLM；
- 播放 PCM 音频时按实际音量驱动嘴型；
- 空闲时保留眨眼、左右看、困倦、打哈欠、睡觉和打鼾等端侧微表情。

当前默认角色是“豆包的妹妹包逗”，固定自我介绍为：

> 我是豆包的妹妹包逗，可以天天逗你开心。

### 当前模型与音频参数

| 项目 | 当前值/来源 |
|---|---|
| Realtime 模型 | `DESKBOT_DOUBAO_MODEL`，当前默认 `1.2.6.1` |
| Realtime 音色 | `zh_female_xiaohe_jupiter_bigtts` |
| 视觉模型 | `doubao-seed-2-1-turbo-260628` |
| 麦克风上行 | PCM16 单声道，16 kHz，20 ms/帧，100 ms/次 WebSocket 写入 |
| 语音下行 | PCM16LE 单声道，24 kHz |
| 播放预缓冲 | 默认 600 ms，见 `DESKBOT_DIRECT_AUDIO_PREBUFFER_MS` |
| Realtime 会话 | `input_mod=keep_alive`，连续对话无需每轮重新叫名字 |

## 2. 后续 Coding Agent 先读这里

固件入口是 [`hardware/firmware/deskbot_rom.ino`](../hardware/firmware/deskbot_rom.ino)。建议按下列顺序了解代码：

1. [`hardware/firmware/deskbot_config.h`](../hardware/firmware/deskbot_config.h)：模式开关、模型、引脚、音频阈值和公共参数；
2. [`hardware/firmware/deskbot_rom.ino`](../hardware/firmware/deskbot_rom.ino)：完整启动顺序和任务创建顺序；
3. [`hardware/firmware/direct_realtime.cpp`](../hardware/firmware/direct_realtime.cpp)：当前主链路、模型提示词、工具调用、视觉、下行播放和空闲表情；
4. [`hardware/firmware/head.h`](../hardware/firmware/head.h) 与 [`head.cpp`](../hardware/firmware/head.cpp)：舵机范围、校准、镜像、相对/绝对运动、点头和摇头；
5. [`hardware/firmware/mic.cpp`](../hardware/firmware/mic.cpp)、[`speaker.cpp`](../hardware/firmware/speaker.cpp)：音频采集、回声抑制、音量和流式播放；
6. [`hardware/firmware/camera.cpp`](../hardware/firmware/camera.cpp)：OV2640 初始化及实时 JPEG 拍摄；
7. [`hardware/platformio.ini`](../hardware/platformio.ini)：开发板、分区、依赖和编译补丁。

### 常见需求对应的修改位置

| 想修改什么 | 首选文件 | 注意事项 |
|---|---|---|
| 名字、人设、自我介绍、工具调用策略 | `direct_realtime.cpp` 的 `send_session_create()` | 提示词会随每次 Realtime 会话创建发送 |
| Realtime 工具定义 | `direct_realtime.cpp` 的 `add_tool_schemas()` | 同时检查 `run_local_tool()` 和工具完成事件 |
| 视觉提问和回答风格 | `direct_realtime.cpp` 的 `call_seed_vlm()` | 视觉会暂时释放 Realtime TLS 内存，见下文 |
| 表情、空闲微表情、嘴型阈值 | `direct_realtime.cpp` 的 `kFace*`、`pump_idle_expression()`、`render_mouth_level()` | 表情是 PB 矢量 JSON，不是图片资源 |
| X/Y 中位、边界和速度 | `head.h`、`head.cpp` | 实机机械风险最高，必须小步测试 |
| LCD、舵机、I2S、PDM 引脚 | `deskbot_config.h` | 以此文件为唯一真值，不要只看旧接线图 |
| 麦克风增益和门控 | `mic.cpp`、`deskbot_config.h` | 默认增益 5，模型工具可切换 3/5/8 |
| 播放音量、队列和 I2S | `speaker.cpp`、`speaker.h` | 嘴型依赖实际写入 I2S 的 PCM 幅度 |
| 摄像头方向、亮度、画质 | `camera.cpp` | 当前 VGA、JPEG quality 10、垂直翻转 |
| 启动任务顺序 | `deskbot_rom.ino` | 摄像头必须先初始化，舵机再永久 attach |

### 当前视觉链路为什么会有等待

ESP32-S3 的内部 SRAM 无法稳定同时容纳 Realtime WSS 和 Ark HTTPS 两套 mbedTLS 缓冲。执行 `inspect_camera` 时当前逻辑会：

1. 先说“等一下，我仔细看看”；
2. 暂时断开 Realtime WSS，释放 TLS 内存；
3. 拍摄最新一帧 VGA JPEG；
4. 调用 Ark Responses API 的 Seed VLM；
5. 重连 Realtime，再播放视觉回答。

因此视觉调用比普通语音问答慢是当前架构特征。不要在不了解内部 SRAM 余量时同时保留两条 TLS 连接。

## 3. 当前硬件和舵机标定

主控为 **Seeed XIAO ESP32S3 Sense**，PlatformIO 环境名为 `seeed_xiao_esp32s3`，串口波特率为 `115200`。

| 外设 | 当前连接 |
|---|---|
| LCD ST7789 | MOSI D10/GPIO9，SCK D8/GPIO7，CS D1/GPIO2，DC D2/GPIO3 |
| 左右 X 舵机 | D9/GPIO8 |
| 上下 Y 舵机 | D3/GPIO4 |
| MAX98357 | DIN D0/GPIO1，BCLK D5/GPIO6，LRC D4/GPIO5 |
| PDM 麦克风 | CLK GPIO42，DATA GPIO41 |

当前代码中的舵机逻辑范围：

| 轴 | 最小 | 中位 | 最大 | 特殊逻辑 |
|---|---:|---:|---:|---|
| X 左右 | -20 | 40 | 100 | `X_OUTPUT_GAIN=2` 补偿约 2:1 齿轮；Realtime 工具下行还会做 X 镜像 |
| Y 上下 | -10 | 50 | 80 | 数值减小为抬头，增大为低头 |

X 轴在 `head.cpp` 中以“逻辑 30° = 1167 µs”作为实机校准点，再围绕它按 2 倍舵机输出增益换算脉宽。不要把模型工具坐标、逻辑角和最终舵机 PWM 角混为一谈。

修改舵机参数时：

- 先记录当前 `X/Y` 中位和边界；
- 每次只改一个轴，边界每次最多扩 5–10°；
- 先执行 `head_move_abs_ex` 慢速靠近边界；
- 听到持续堵转声立刻断电，并收紧对应边界；
- X 方向错误时先检查 Realtime 工具中的镜像，不要同时反转底层 PWM 和工具映射。

## 4. 首次准备本地配置

真实 Wi-Fi 和火山凭证不得写入受 Git 跟踪的文件。仓库根目录执行：

```powershell
Copy-Item .\hardware\firmware\deskbot_local_config.example.h `
  .\hardware\firmware\deskbot_local_config.h
Copy-Item .\service\.env.example .\service\.env
```

编辑被忽略的 `hardware/firmware/deskbot_local_config.h`：

- 填写 `WIFI_DEFAULT_SSID`、`WIFI_DEFAULT_PASSWORD`；
- 保持 `DESKBOT_DIRECT_CLOUD=1`；
- 自建服务模式才需要 `DESKBOT_WS_HOST` 和 `DESKBOT_API_KEY`。

编辑被忽略的 `service/.env`，直连固件至少需要：

```dotenv
DOUBAO_REALTIME_APP_ID=
DOUBAO_REALTIME_ACCESS_TOKEN=
ARK_API_KEY=
```

`hardware/scripts/build_direct.ps1` 会在编译期间把这三个值临时变成编译宏，不会改写受 Git 跟踪的源码，也不会主动打印凭证。火山 Secret Key 不参与当前设备端 Realtime 链路，不应编进固件。

## 5. Windows 环境准备

推荐使用 Python 3 + PlatformIO Core。先确认 Python：

```powershell
python --version
```

安装 PlatformIO：

```powershell
python -m pip install --upgrade platformio
python -m platformio --version
```

如果机器上有多个 Python，请固定使用装有 PlatformIO 的解释器：

```powershell
$pioPython = python -c "import sys; print(sys.executable)"
& $pioPython -m platformio --version
```

工程路径含中文时不要手工搬目录。`build_direct.ps1` 会临时把 `hardware` 映射到 ASCII 盘符 `H:`，编译结束后自动释放。

## 6. 找到机器人的串口

插入机器人 USB 后执行：

```powershell
Get-CimInstance Win32_SerialPort | Format-Table DeviceID, Name
python -m platformio device list
```

记住设备对应端口，例如 `COM4`。烧录前关闭 Arduino IDE、串口监视器或其他占用该端口的程序。

## 7. 编译和烧录当前直连版

以下命令均从仓库根目录运行。

### 只编译

```powershell
.\hardware\scripts\build_direct.ps1 -BuildOnly
```

如果 `python` 不是装有 PlatformIO 的解释器：

```powershell
.\hardware\scripts\build_direct.ps1 -BuildOnly -PythonPath $pioPython
```

### 编译并烧录

把 `COM4` 换成实际端口：

```powershell
.\hardware\scripts\build_direct.ps1 -UploadPort COM4
```

或指定 Python：

```powershell
.\hardware\scripts\build_direct.ps1 `
  -UploadPort COM4 `
  -PythonPath $pioPython
```

成功时 PlatformIO 最后应显示 `SUCCESS`。烧录会让设备自动复位。

### 打开串口日志

```powershell
python -m platformio device monitor `
  -d .\hardware `
  -e seeed_xiao_esp32s3 `
  -p COM4 `
  -b 115200
```

退出串口监视器使用 `Ctrl+C`。

> `hardware/flash_rom.sh` 主要服务 Linux/macOS 和旧的服务端模式。当前直连版需要从 `service/.env` 注入 Realtime/Ark 凭证，Windows 上优先使用 `build_direct.ps1`，不要直接用普通 `pio run` 误烧成缺少直连凭证的版本。

## 8. 烧录后的启动判断

正常启动日志大致依次出现：

```text
[BOOT] device_id=...
[CAMERA] setup_camera ok framesize=VGA quality=10
[DIRECT] setup ok ...
[DIRECT] connecting wss://openspeech.bytedance.com:443/...
[DIRECT] session ready; microphone open
[BOOT] ready ... mode=direct-cloud ... wifi_ip=...
```

建议完成以下冒烟测试：

1. 叫“包逗”，确认能回应“我在”；
2. 连续问两轮，不重复叫名字，确认能连续对话；
3. 让它抬头、低头、向左看、向右看；
4. 说一句明确肯定或否定的话，确认点头/摇头时仍有语音；
5. 让它切换开心或睡觉表情；
6. 让它调大/调小音量、调整远场收音；
7. 把物体放到镜头前问“这是什么”，确认先有等待提示，再返回视觉结果；
8. 让它说一段长句，确认声音连续、后半句音量正常、嘴型持续跟随。

### 串口直接测试舵机

串口监视器支持纯文本 factory 命令：

```text
head_pos
head_move_abs_ex 40 50 1 300
head_nod
head_shake
head_center
reboot
```

其中 `head_move_abs_ex <x> <y> <step> [hold_ms]` 适合慢速标定。命令仍会受代码中的硬限位约束，但硬限位不保证机械一定安全。

## 9. 常见问题

### 找不到 PlatformIO

确认安装到当前 Python：

```powershell
python -m pip show platformio
python -m platformio --version
```

若只有另一个 Python 安装了 PlatformIO，把那个解释器的绝对路径传给 `-PythonPath`。

### 找不到串口或上传失败

- 换一根支持数据的 USB 线；
- 关闭占用 COM 口的软件；
- 重新插拔并再次执行 `platformio device list`；
- 必要时按住 BOOT、短按 RESET、松开 BOOT，使 ESP32-S3 进入下载模式后重试。

### 启动后没有语音

优先检查日志是否出现：

- `missing Doubao APP ID or Access Token`：直连凭证没有注入；
- `WiFi connect failed`：Wi-Fi 配置或热点不可用；
- `session ready timeout` / `disconnected`：网络、代理或火山端连接异常；
- `downlink queue full`：音频下行消费不及时。

### 摄像头一直回答没看清

检查：

- 启动时是否有 `[CAMERA] setup_camera ok`；
- 调用时是否有 `[DIRECT VLM] scheduling`、`direct capture` 和 `completed`；
- `ARK_API_KEY` 是否有效；
- 是否出现 `TLS handoff timeout` 或 Ark HTTP 状态码错误。

### 舵机持续响或顶住外壳

立即断电。缩小 `head.h` 中对应轴的 `*_MIN_LIMIT` / `*_MAX_LIMIT`，不要通过增加脉宽范围解决。重新上电前先核对齿轮装配和中位。

## 10. Coding Agent 的安全修改流程

每次修改固件建议遵循：

```powershell
git status --short
python .\scripts\check_no_local_secrets.py
.\hardware\scripts\build_direct.ps1 -BuildOnly -PythonPath $pioPython
```

接上机器人后再执行：

```powershell
.\hardware\scripts\build_direct.ps1 -UploadPort COM4 -PythonPath $pioPython
```

最后检查：

```powershell
python .\scripts\check_no_local_secrets.py
git diff --check
git diff
```

对后续 Coding Agent 的约束：

- 不读取、打印、提交或在文档中复制真实 Wi-Fi、Access Token、API Key；
- 不使用 `git add -f` 添加 `deskbot_local_config.h` 或 `service/.env`；
- 修改舵机前先读完 `head.h` 和 `head.cpp` 的坐标变换；
- 不在一次修改里同时调整 X 镜像、X 增益、中位和边界；
- 不把旧服务端链路的 PB/ASR 逻辑误认为当前实机主链路；
- 修改 Realtime 事件名或会话结构时，以火山当前接口规范和实机日志共同验证；
- 只有编译成功不代表实机通过，音频、视觉和舵机改动必须分别做冒烟测试。

本地凭证与 GitHub 发布的完整说明另见 [`local-config-and-github.md`](local-config-and-github.md)。
