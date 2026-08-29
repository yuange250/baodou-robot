# Brufik — Bambu Lab P2S 打印文件

本目录保存 Brufik 机身结构件的两盘 P2S 切片文件，以及可继续编辑的两盘 Bambu Studio 工程源文件。

## 打印配置

- 打印机：Bambu Lab P2S
- 喷嘴：0.4 mm
- 打印板：纹理 PEI
- 工艺预设：`0.20mm Standard @ BBL P2S`
- 耗材：Generic PLA
- 支撑：树状（自动），临界角 30°，不限于仅在热床上生成

## 文件

- `Brufik_P2S_plate_01_main_0.20mm_PLA.gcode.3mf`
  - 第一盘主要外壳与底座结构件
  - 210 层，切片估算 75.02 g，约 3 小时 3 分钟
- `Brufik_P2S_plate_02_remaining_0.20mm_PLA.gcode.3mf`
  - 第二盘剩余小件与机构件
  - 110 层，切片估算 12.17 g，约 44 分 51 秒
- `Brufik_P2S_two_plate_source.3mf`
  - 两盘可编辑工程；用于重新排版、改耗材或重新切片

`.gcode.3mf` 已包含切片后的 G-code，适用于上述打印机与配置。再次使用前，仍应在 Bambu Studio 中核对打印机、喷嘴、打印板和耗材是否一致。
