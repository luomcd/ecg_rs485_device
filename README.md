# ecg_rs485_device —— ADS1293 心电 RS485 双端工程

> **本仓库是 T198 传输改造的独立工作区**，2026-11-03 从 `C:\work2026\VsPro\ads1293_demo` 复制而来。
> 老工程**保持原样不动**，后续 RS485 双端联调全部在这里做。

---

## 1. 目录

```
ecg_rs485_device/
├── docs/           T198 评估 · T199 施工单（本处为权威副本）
├── stm32/          ADS1293 + STM32L476 固件（原 ads1293_demo 全量快照）
└── esp32/          CardioLite ESP32 侧工程 + 485 工具脚本
    ├── cardiolite-ecg/   ESP-IDF 工程
    └── 485tools/         上位/调试脚本（python）
```

## 2. 基线

| 项 | 值 |
|---|---|
| 来源仓库 | `C:\work2026\VsPro\ads1293_demo` |
| 来源提交 | `51d84dc` fix(fw): T170-A 写表失败诊断与 START 失败回显；T196p 伪迹空白登记 |
| 取数方式 | 复制**工作区现状**（含老仓库当时未提交的 `tools/*` 改动） |
| 复制时间 | 2026-11-03 |

⇒ 本仓库第一次提交 = 上述快照。老仓库后续改动**不会**自动流入本仓库，反之亦然。

## 3. 记忆位置（单一真相，勿复制）

项目记忆仍**只有一份**，在 **`C:\work2026\VsPro\ads1293_demo\.workbuddy\memory\`**
（`MEMORY.md` 铁律 / `DEVICE.md` 设备事实 / `NOW.md` 交接锚点 / 每日日志）。

🛑 **不要在本仓库再建 `MEMORY.md` 或日报副本** —— 两份必然分叉，这正是 09-17 踩过的坑。
本仓库只放代码与文档；把「按哪个方向改、为什么」写回老工程的当日日志。

## 4. 构建

**STM32（IAR EWARM 9.70.2，入口是 EIDE 不是 .ewp）**

```bash
cd stm32
python tools/_fw_build.py --inc       # 增量
python tools/_fw_build.py             # 全量重建 + 镜像三账核对
# 产物：stm32/EWARM/demo_ads1293/build/demo_ads1293/demo_ads1293.hex
```
⚠ 构建参数在 `stm32/EWARM/demo_ads1293/build/demo_ads1293/builder.params`，
内含**绝对路径**；本工作区已把其中的根目录改指到 `ecg_rs485_device\stm32`（2026-11-03）。
若再整体搬迁目录，这 7 处必须同步改，否则 EIDE 会去编老工程。

**ESP32（ESP-IDF）**

```bash
cd esp32/cardiolite-ecg
idf.py build
```

## 5. 目标拓扑

```
ADS1293 ──SPI3── STM32L476JGY6TR ──UART5(PC12/PD2)── ST2378 ── CN9
                                                              │
                                              3.3V TTL ───────┘
                                                    ↓
                                     TTL转232 ── UT-2211(232转485) ──→ ESP32 485
```

- **波特率 115200 8N1**，无硬件流控。
- 上行：`AA 55 | TYPE | LEN | SEQ | STA | int16×N | CRC8`
- 下行：`AA 55 | 0x80 | LEN | CMD | [ARG] | CRC8`
- 🛑 USB OTG FS 与 UART5 **物理互斥**（模组内 `PA11≡PC12`、`PA12≡PD2`）⇒ 编译期宏二选一。

## 6. 当前状态

见 `docs/T199_串口通道改造施工单_2026-11-03.md`。**尚未开始落码**。
