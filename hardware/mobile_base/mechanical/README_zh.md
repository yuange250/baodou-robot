# Brufik × N3 移动底座外壳

本目录是针对淘宝 N3 三轮全向底盘的参数化机械设计。设计目标是保留现有
Brufik 机身和内部机构，只在下方增加可拆卸移动底座。

## 设计组成

- `N3_upper_cowl_v0_1`：覆盖上层亚克力板和电子仓的低矮外壳，带三个轮拱。
- `Brufik_to_N3_adapter_v0_1`：现有机身与 N3 的转接座。
- `N3_58x49_fit_jig_v0_1`：先打印的孔位验证片。
- `Head_touch_electrode_holder_v0_1`：粘在头壳内侧的触摸电极固定片。
- `Brufik_N3_mobile_base_v0_1.step`：装配参考模型。

## 打印与验证顺序

1. 先打印 `N3_58x49_fit_jig_v0_1.stl`，确认卖家所说的 58 × 49 mm 孔距。
2. 用 M3 螺钉检查四个长圆孔均可自由通过。
3. 核对三个轮心到中心的实际距离。脚本暂按 57 mm 设计。
4. 孔距正确后再打印转接座和上盖。

推荐参数：0.4 mm 喷嘴、0.20 mm 层高、4 道墙、20% Gyroid 填充。转接座和
孔位片不需要支撑；上盖大平面朝向打印板，轮拱区域使用树状支撑。

## 当前需要实物复核的参数

卖家图片只给出了整体尺寸和标准孔位，没有给出三个轮心坐标。所有待复核尺寸
集中在 `generate_cad.py` 的 `N3Dimensions` 中，底盘到货后只需修改参数并重新
执行脚本即可生成全部 STL/STEP。

生成命令：

```powershell
$env:PYTHONPATH='C:\Users\Chen\AppData\Local\Temp\codex-brufik-cadquery'
python .\generate_cad.py
```
