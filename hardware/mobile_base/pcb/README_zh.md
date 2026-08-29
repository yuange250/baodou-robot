# Brufik N3 移动底盘控制板

这是与现有 Brufik 主板并行工作的底盘副板，不替换已经调通的屏幕、音频、
摄像头和舵机主板。

## 主要功能

- XIAO ESP32-C3 独立执行三轮全向运动控制。
- 两颗 DRV8833 驱动三只 N20 电机。
- 三路 0.20Ω 电流采样电阻将斩波电流设在约 1A。
- 2S 受保护锂电池输入，5A 自恢复保险和外接总开关。
- TPS54302 产生 5V/3A，给现有机器人上半身供电。
- 一个四针上半身接口：5V、GND、UART TX、UART RX。
- 一个三针数字触摸接口。
- J2 外接自锁总开关，既是电源开关也是机械急停。

## 供电边界

电机 VM 直接来自 2S 电池。对于套件内常见的 6V N20，固件必须把最大 PWM
限制在 70%，并保留约 1A 的硬件限流。若卖家提供 9V N20，则可将最大 PWM
上调，但仍需测量堵转电流和驱动温度。

J2 必须连接自锁开关；台架调试时可以暂时用跳线帽短接。连接 USB 给底盘 XIAO
刷机前，先关闭电池总开关并拔掉 J6 上半身接口，避免 USB 5V、板载 5V 与上半身
USB 互相反向供电。

## PCB 规格

- 外形：92 × 74 mm，四角倒角。
- 安装孔：58 × 49 mm，M3。
- 4 层板：F.Cu 信号/功率、In1.GND、In2.VBAT（局部 SLEEP 走线）、B.Cu 信号。
- 推荐：1.6 mm、1 oz 外层/内层铜、HASL-LF 或 ENIG。

## 当前验证状态

- KiCad 10 DRC：0 违规。
- 未连接网络：0。
- `production/BrufikMobileBase_gerbers.zip` 可直接用于裸板下单。
- BOM 与贴片坐标已经导出，但不是一键 PCBA 文件；下贴片单前仍需在嘉立创
  核对每个 LCSC 编号、封装方向和缺料替代。

## 下单前仍需实物确认

这是根据卖家尺寸和原厂数据手册生成的 Rev 0.1 工程。底盘到货后，仍需确认：

1. N20 电机额定电压与堵转电流；
2. 58 × 49 mm 孔距；
3. 电池包是否自带 2S BMS；
4. 嘉立创元件库中的 DRV8833PWPR、TPS54302DDCR 和连接器封装。

裸板可以按生产包下单；首次只下 5 片并手焊 1 片验证。开关降压部分应按 TI
推荐布局复核输入电容、SW 节点、L1 和输出电容，通电前先用限流电源测试。

生成与检查：

```powershell
& 'C:\Users\Chen\AppData\Local\Programs\KiCad\10.0\bin\python.exe' .\generate_pcb.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\export_production.ps1
```

原厂参考：

- TI DRV8833 datasheet: https://www.ti.com/lit/ds/symlink/drv8833.pdf
- TI TPS54302 datasheet: https://www.ti.com/lit/ds/symlink/tps54302.pdf
