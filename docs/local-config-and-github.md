# 包逗本地配置、刷写与 GitHub 发布

本文档的目标是：真实 Wi‑Fi、设备鉴权和火山凭证只保留在本机，GitHub 仓库只保存空默认值、示例和代码。

## 1. 配置文件分层

| 文件 | 是否提交 | 用途 |
|---|---:|---|
| `hardware/firmware/deskbot_local_config.example.h` | 是 | 固件本地配置模板 |
| `hardware/firmware/deskbot_local_config.h` | 否 | Wi‑Fi、服务地址、设备 API Key、直连模式开关 |
| `service/.env.example` | 是 | 火山与服务端环境变量模板 |
| `service/.env` | 否 | Ark、Realtime、TTS 等真实凭证 |

不要使用 `git add -f` 强制添加两个本地文件。

## 2. 首次本地配置

在仓库根目录运行 PowerShell：

```powershell
Copy-Item .\hardware\firmware\deskbot_local_config.example.h `
  .\hardware\firmware\deskbot_local_config.h
Copy-Item .\service\.env.example .\service\.env
```

编辑 `deskbot_local_config.h`：

- `WIFI_DEFAULT_SSID` / `WIFI_DEFAULT_PASSWORD`：机器人连接的网络；
- `DESKBOT_WS_HOST` / `DESKBOT_API_KEY`：仅自建服务模式需要；
- `DESKBOT_DIRECT_CLOUD=1`：ESP32 直接连接豆包实时模型。

编辑 `service/.env`，直连版至少填写：

- `ARK_API_KEY`：Seed VLM 摄像头识别；
- `DOUBAO_REALTIME_APP_ID`；
- `DOUBAO_REALTIME_ACCESS_TOKEN`。

Secret Key 不参与当前 Realtime WebSocket 协议，不应写入固件。

## 3. 编译与刷写直连版

安装 PlatformIO 后运行：

```powershell
.\hardware\scripts\build_direct.ps1 -UploadPort COM4
```

只编译、不刷写：

```powershell
.\hardware\scripts\build_direct.ps1 -BuildOnly
```

脚本从 `service/.env` 临时生成编译宏，不会把值写入受 Git 跟踪的文件，也不会主动打印凭证。工程路径含中文时，脚本会临时映射 ASCII 盘符，规避 ESP32 链接器路径问题。

如果 `pio` 不在 PATH，可指定安装了 PlatformIO 的 Python：

```powershell
.\hardware\scripts\build_direct.ps1 -UploadPort COM4 `
  -PythonPath C:\path\to\python.exe
```

## 4. 提交前安全检查

```powershell
git check-ignore -v hardware/firmware/deskbot_local_config.h service/.env
python .\scripts\check_no_local_secrets.py
git status --short
git diff --cached
```

前两个本地文件必须显示为 ignored；凭证扫描必须返回 `ok`。

注意：密钥虽然不进入 Git，但直连版必须把必要凭证编译进 ESP32 固件。量产时应使用最小权限凭证、设置配额，并准备远程轮换或设备级临时令牌方案。

## 5. 发布到自己的 GitHub

当前 `origin` 指向上游 `OpenDeskBot/brufik_in_one`。建议先在 GitHub 创建一个私有仓库，然后把上游保留为 `upstream`：

```powershell
git remote rename origin upstream
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git add -A
python .\scripts\check_no_local_secrets.py
git diff --cached
git commit -m "feat: add Baodou direct realtime robot"
git push -u origin feature/realtime-direct-esp32
```

旧上游历史曾使用过非空的示例网络或鉴权值。发布为公开仓库前，应先轮换所有历史凭证；若要求历史中完全不含任何旧值，应另外创建无历史的干净发布分支，而不是直接公开完整历史。
