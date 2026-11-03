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

⚠ **首次构建前必须先看 §7** —— `Drivers` / `Middlewares` / `USB_DEVICE` 三个目录
**不入库**（2026-11-03 猛哥拍板：第三方库与大文件不推远端），需要自备。

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

## 6. 当前状态（2026-11-03 18:00）

见 `docs/T200_UART5通道落码与上板卡_2026-11-03.md`（§1~§8 全过程记录）。

| 阶段 | 状态 |
|---|---|
| T198 通道抽象层落码 | ✅ `comm_port.c/.h`，`ECG_COMM_PORT` 单开关 |
| T200 烧录与上板卡 | ✅ TX 横幅逐字正确 |
| T202 根因（PD2 上拉） | ✅ 改 `GPIO_NOPULL` 即通；补 `HAL_UART_ErrorCallback` |
| T203 完整 485 链路 | ✅ `UART5→232→UT-2211→485转USB→PC`，WinApp 出 AECG100 波形 |
| T204 下行命令 | ✅ `SET_MODE 0x04` / `SET_NOTCH 0x05` 四条全部生效（STA 位跟随） |
| ESP32 侧命令通道 | ⬜ 未开始 |

实测（COM27 / 115200）：上行 **19.60 fps / 495.0 Hz**；下行四条命令全通；
485 上行偶发整帧丢失 1~2%（坏 CRC 0）。

## 7. 不入库的依赖（clone 后必须自备）

🛑 **2026-11-03 猛哥拍板：`Drivers` / `Middlewares` / `USB_DEVICE` 等第三方库与大文件
不推送到远端仓库。** 故 clone 下来后 `stm32/` **不能直接构建**，需按本节补齐。

| 目录 | 体积 | 内容 | 来源 |
|---|---|---|---|
| `stm32/Drivers/STM32L4xx_HAL_Driver` | 12 MB | ST HAL（Inc + Src） | STM32Cube FW_L4 包 |
| `stm32/Drivers/CMSIS` | 85 MB | Core 5.3 / Device L4 / DSP / NN（大部分用不到） | 同上 |
| `stm32/Middlewares/ST/STM32_USB_Device_Library` | 204 KB | USB Device 库（Core + CDC Class） | 同上 |
| `stm32/USB_DEVICE` | 72 KB | **项目自有** CDC 实现（`usbd_cdc_if.c` 等 8 个文件） | 本机原始工程 |

⚠ `stm32/USB_DEVICE` 严格说**不是第三方库**，是 CubeMX 生成后经本项目修改过的代码
（`usbd_cdc_if.c` 里有 `vcom_recv()` 调用）。它不入库是"大文件不入库"这条规则一并带上的，
**若将来要独立分发本仓库，应优先把它补进来**（只有 72 KB）。

### 7.1 补齐后的验收条件

1. **以 `stm32/EWARM/demo_ads1293/.eide/eide.yml` 为唯一真源** —— 它逐条枚举了
   全部参与编译的源文件（`srcDirs: []` 不是扫目录）。缺哪个文件，链接期就会报未定义符号，
   按那个符号回 `eide.yml` 里找路径即可定位。
2. 🛑 **必须含 `stm32l4xx_hal_uart.c` 与 `stm32l4xx_hal_uart_ex.c`** —— 这两个是 T198
   打开 `HAL_UART_MODULE_ENABLED` 后新增的依赖；较旧的 HAL 包缺 `_uart_ex.c`，会链接失败。
3. `incList` 引用了 `../../Drivers/CMSIS/DSP/Include`（`eide.yml:131`）⇒ **CMSIS/DSP 的
   `Include` 目录要留**（`Lib` / `Examples` / `Source` 可以删，本项目没链接 DSP 库）。
4. CMSIS Core 版本：**5.3**（`Drivers/CMSIS/Include/cmsis_version.h`）。
5. 补齐后先跑语义门禁，再构建：

```bash
cd stm32 && python tools/_t200_comm_port_check.py     # 通道抽象层 9 条断言 + 阴性对照
python tools/_fw_build.py                             # 全量重建
```

### 7.2 仓库里现有的"大文件"（供参考，未动）

| 文件 | 大小 | 判断 |
|---|---|---|
| `esp32/485tools/batch_mit_result.txt` | 753 KB | MIT-BIH 批量测试**结果数据** |
| `esp32/485tools/tools/batch_mit_result.txt` | 753 KB | 同上，**两份内容重复** |
| `esp32/cardiolite-ecg/sdcard/ecg_dual_5min.bin` | 639 KB | SD 卡抓的**采集数据** |
| `esp32/cardiolite-ecg/sdcard/ecg.bin` | 104 KB | 同上 |
| `stm32/tools/INDEX.md` | 392 KB | 工具检索表（自动生成，有用但大） |

⇒ 真正的"数据"约 **2.2 MB**，占仓库体积（2.87 MiB）的大头。
🛑 这几项**没有动**：可能是测试基准数据，删了不可再生。要不要清理由猛哥决定。
