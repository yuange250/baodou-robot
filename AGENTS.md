# 包逗仓库协作说明

修改 ESP32 固件前，先完整阅读 [`docs/firmware-development-and-flashing.md`](docs/firmware-development-and-flashing.md)。

当前实机主链路是 `ESP32 -> 豆包 Realtime` 直连模式，入口为 `hardware/firmware/deskbot_rom.ino`，核心实现为 `hardware/firmware/direct_realtime.cpp`。旧的 `service/` 与 `asr_ws` 路径仍保留作回退和参考，但不要默认它们是当前烧录版本。

必须遵守：

- 真实配置只放在被忽略的 `hardware/firmware/deskbot_local_config.h` 和 `service/.env`；
- 不打印、提交或复制 Wi-Fi 密码、Realtime Access Token、Ark API Key；
- 提交前运行 `python .\scripts\check_no_local_secrets.py`；
- 直连版使用 `hardware/scripts/build_direct.ps1` 编译/烧录；
- 舵机修改前核对 `head.h`/`head.cpp` 的硬限位、中位、2:1 X 增益和 Realtime X 镜像；
- 听到舵机持续堵转声应立即断电并收紧边界；
- 音频、视觉或舵机改动需在编译后分别完成实机冒烟测试。
