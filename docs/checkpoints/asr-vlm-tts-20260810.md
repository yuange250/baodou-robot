# 包逗 ASR + VLM + TTS 稳定基线

保存日期：2026-08-10

## 链路

1. ESP32 上传麦克风 Opus 音频。
2. 服务端完成本地唤醒、VAD 与 ASR。
3. 文本 LLM 负责对话和工具调度。
4. 视觉问题通过摄像头快照与 VLM 工具处理。
5. 独立 TTS 合成 24 kHz 音频并下发 ESP32。
6. 舵机、表情、音量、收音配置继续使用现有工具链。

`service/config.yaml` 中 `realtime.enabled` 在此基线固定为 `false`。Doubao Realtime 代码可以保留，但不会进入默认语音链路。

## 本地私有配置

凭证和设备网络配置不进入 Git。当前机器上的副本位于：

- `.codex-backups/asr-vlm-tts-20260810/private/service.env`
- `.codex-backups/asr-vlm-tts-20260810/private/deskbot_config.h`

恢复时将它们分别复制回 `service/.env` 和 `hardware/firmware/deskbot_config.h`。

## 回归结果

稳定链路相关测试共 69 项：

- 66 项通过。
- 2 项合成音频 VAD 用例在本机 Silero 模型下未产生句末事件。
- 1 项在 Windows 下因 SQLite 测试连接未及时释放，临时数据库清理失败。

真机已经完成 ASR 问答、摄像头识物、TTS 下行、舵机与表情控制验证。上述三个测试问题作为已知测试环境问题保留，不等同于真机链路失效。

## 启动

在 PowerShell 中运行：

```powershell
cd service
.\start_windows.ps1
```
