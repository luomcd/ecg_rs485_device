# CardioLite ECG 显示终端 UI 实施计划

> **项目英文代号：** CardioLite
> **项目英文全称：** CardioLite — ESP32-S3 Based ECG Waveform Display Terminal
> **项目中文名称：** 基于 ESP32-S3 的心电波形显示终端
> **项目对象：** Waveshare ESP32-S3-Touch-LCD-5（800×480 SKU），ESP32-S3-WROOM-1-N16R8，LVGL 8.4.0
> **文档版本：** V1.0
> **整理日期：** 2026-10-23
> **开发基线：** ESP-IDF `C:\esp\v6.1\esp-idf` v6.1.0、LVGL 8.4.0、esp_lvgl_adapter 0.5.3（2026-10-22 实测点亮）
> **被修订文档：** 无（首版）
> **配套需求文档：** `ECG显示终端_嵌入式UI_需求描述_v1.2_2026-10-22.md`（下称「需求 v1.2」，**UI 需求以它为准**）
> **本版主题：** 把「已验证的硬件平台 + 已定稿的 UI 需求」落成可逐项执行、逐项验收的工程计划

---

## 0. 修订说明

本版为首版。它的存在理由是一个具体缺口：**需求 v1.2 解决了「做成什么样」，但没有解决「怎么干、按什么顺序干、每一步做到什么程度算过」**。

具体缺三块，本文档补齐：

1. **架构落地**：需求 v1.2 §6 只给了「Core 0 渲染 / Core 1 数据」两行分工，没有代码模块划分、没有文件清单、没有数据流、没有内存预算。S0 开工当天必须知道要建哪些文件、每个文件负责什么。⇒ 本文档 §4.3 / §4.4 / §4.5。
2. **环境固化**：v1.2 §13 只写「构建走 `tools/idf.sh`」，没写这套环境里哪些版本是锁死的、哪些坑会导致构建静默失败、为什么不能随便改 sdkconfig。⇒ 本文档 §5。
3. **验收闸门**：v1.2 §13 的 S0~S10 有「验收方式」列，但那是**单个步骤**的验收；没有定义「哪些步骤构成一个可交付的阶段」「阶段之间靠什么量化条件放行」。⇒ 本文档 §6 / §7。

**本文档不重复需求 v1.2 的内容。** 凡两处冲突，一律以需求 v1.2 为准（本文档只补工程面，不改需求面）。凡本文档新增的技术判断，一律标注来源等级：【实测】【推算】【待实测】。

### 关于「三份文档的分工」

| 文档 | 管什么 | 不管什么 |
|---|---|---|
| 需求 v1.2 | UI 长什么样、按钮语义、标定公式、验收判据 | 怎么实现、分几步、代码放哪 |
| **本文档** | 架构、模块、环境、顺序、闸门、风险 | 按钮叫什么、波形画多高 |
| `NOW.md`（工作区根） | 跨会话交接锚点、明天第一件事 | 任何细节 |

⚠ **不做三份重复**。需求里的数值（1 mV = 20 px、mmX = 6.016、双画布 216 px）本文档只引用不重定义；本文档的模块清单需求里不出现。

---

## 1. 文档目的与实施原则

本计划把已验证的硬件底座与已定稿的 UI 需求，细化为可按步骤执行、按阶段验收的工程计划，覆盖：需求边界、总体原理、系统架构、模块划分、开发环境、工作目录、实施顺序、决策门、风险与对策。

### 1.1 已有资产（2026-10-22 实测可用）

| 资产 | 状态 | 在本项目中的角色 |
|---|---|---|
| `bsp_ws_lcd5\` | ✅ 已点亮验收 | 共享 BSP 组件：RGB 面板 + GT911 触摸 + CH422G。**唯一一份，不复制** |
| `lcd-bringup\` | ✅ 已点亮验收 | 硬件验证工程（LVGL widgets demo）。**作为 S0 的骨架模板** |
| ESP-IDF v6.1.0 | ✅ 构建通过 | 编译环境。v6.1 迁移已完成，不再回退 |
| 出厂固件备份 | ✅ 16 MB 整片备份 | `lcd-bringup\factory_backup\factory_flash_16MB.bin`，MD5 `2fea3165…`，回滚用 |
| 录制数据 | ✅ 8,033 帧 CRC 全过 | `WinApp_ADSECG\...\recordings\*.bin`，演示数据源 |
| 需求 v1.2 | ✅ 定稿 | UI 需求唯一依据 |
| `ui-mockup.html` | ✅ 可交互仿真稿 | S4 / S6 的像素级比对基准 |

### 1.2 核心实施原则

- **硬件底座不动。** BSP 已验证点亮（`GT911: TouchPad_ID:0x39,0x31,0x31`、PSRAM XIP 生效），本项目的所有改动只发生在 `cardiolite-ecg\` 内；**除 S1 需要补 CH422G 输出 API 外，不改 BSP 的既有逻辑**。
- **需求与实现分离。** 需求 v1.2 定稿后不再改需求去迁就实现；实现做不到的先登记为「待确认」，不偷偷降级。
- **每一步可独立验证，前一步不过不进下一步。** 这是 S0~S10 拆分的全部意义。不接受「先全写完再一起调」。
- **一次只引入一个新的不确定因素。** S0 只有环境、S1 只有 SD、S2 只有解析……任何一步同时引入两个新变量都视为计划违规。
- **构建成本是第一约束。** 改一次 sdkconfig = ccache 全 miss = 全量重编 40~50 min【实测】。因此 S0 必须一次性把 sdkconfig 定死，之后不再动。
- **颜色 / 尺寸 / 时限一律走常量。** 绘制代码禁止内联颜色字面量（否则两套配色无法切换），布局禁止内联魔数（否则 §3.1 像素比对无从谈起）。

### 1.3 平台能力边界（决定架构，不可绕）

| 事实 | 后果 |
|---|---|
| ESP32-S3 **无 GPU** | 所有绘制是 CPU 算 + DMA 搬。800×480 RGB565 一帧 768 KB |
| PSRAM 8 MB **OCT @80 MHz** | framebuffer 与画布只能放 PSRAM，写到 20~40 MB/s【实测口径】 |
| **仅单精度硬件 FPU** | C# 参考实现的 `double` 必须全部改为 `float`。双精度会走软件浮点，慢一个量级 |
| 240 MHz 双核 | Core 0 = LVGL 渲染 + 触摸；Core 1 = 数据管线 + R 峰 |
| LVGL 8.4.0 + esp_lvgl_adapter 0.5.3 | 升 9.x 要重写显示适配层，第一阶段不做 |

---

## 2. 设计需求

> 完整需求见需求 v1.2。本节只列**实施必须知道的约束级条目**，以及需求里没有的工程性需求。

### 2.1 业务需求（一句话）

在 800×480 的嵌入式屏上，把 SD 卡里的 ECG 录制数据以标准心电图纸样式显示出来，支持冻结 / 清屏 / 增益 / 走纸 / 回放控制，并实时显示心率。

### 2.2 技术需求（约束级）

1. **双画布，纵向各 216 px**，可绘制区 752×200。`1 mV = 20 px`（增益 5），`mmX = 6.016 px/mm`，`mmY = 4.0 px/mm`。
2. **横纵解耦不得回退**：横向由纸密度常数定（`plotW ÷ 125`），纵向由恒 ±5 mV 满量程定。参考工程 `EcgWaveformControl.cs:136-145` 对此有明确警告。
3. **9 个按钮，85×40 px**：采集 / 冻结 / 清屏 / 增益 / 走纸 / 模式 / 陷波 / 通道 / 回放·记录。
4. **两套配色并存**：浅色纸（默认）/ 深色黑底白波，**长按 BPM 浮层 2 s 切换**，NVS 持久化。
5. **数据源**：SD 卡 `/sdcard/ecg.bin`，按 `AA 55 + LEN` 解析，**严禁按 58 字节硬切**。
6. **帧型**：LEN = 52 / 53 / 103 三型，`N = (LEN−3)/2`。演示只用 53 / 103。
7. **心率由 ESP32 单核计算**，移植 C# 的 Pan-Tompkins 变体，`double` → `float`。
8. **中文显示**：约 40 个汉字的子集字库，按钮主行 13 px / 副行 10 px。
9. **第一阶段无实时数据源** ⇒ 「记录」只仿真界面形态，不实现写入。

### 2.3 暂不承诺的指标

- **不承诺 20 FPS 是可达的。** 需求 v1.2 §4.5 的 20 FPS 是**推算值**【推算】：假设 PSRAM 写到 20~40 MB/s、假设全屏重绘。真实带宽受 LVGL 合成路径、anti-tear **三缓冲**策略（见 §4.5）、canvas blit 次数共同影响。**S4 实测前不得对外承诺任何帧率。**
- **不承诺 BPM 精度。** 需求 v1.2 §2.4 表里的 BPM 是 PC 端简化阈值法粗估，**不是**固件输出的基准（见 §10 边界第 6 条）。
- **不承诺 SD 卡兼容范围。** 只按「FAT32 + SPI 模式 + ≤32 GB」验证，不做兼容性矩阵。

### 2.4 需求边界：第一阶段 vs 第二阶段

⚠ 本节**不修改**需求 v1.2，只界定实施范围。凡与本节冲突的表述以本节为准。

| | **第一阶段（本计划覆盖）** | **第二阶段（不在本计划）** |
|---|---|---|
| 数据来源 | SD 卡 `ecg.bin` 回放 | 实时链路（I2C / RS485） |
| 采集按钮 | **恒灰显不可点** | 可点，启停采集 |
| 记录（写 SD） | **不实现**，仅仿真按钮形态 | 实现覆盖写 + REC 计时 |
| 模式 / 陷波 / 通道 | **回放阶段恒灰显** | RUN 态可点（协议 `0x04/0x05`），STOP 态可点通道（`0x06`） |
| 画布 2 | 恒显示「无数据」（见 §2.5） | 第二路信号到位后启用 |
| 下行命令 | 无采集端可发 | 实现 |

**三条派生边界（同样有约束力）：**

1. 🛑 **不许把「回放跑得动」当成「实时也跑得动」**。回放的数据是 SD 卡顺序读 + 定速喂入，实时链路是 I2C 中断 + 不定时到达，两者的缓冲压力模型不同。第一阶段验收全部通过，**不代表**第二阶段的数据管线不用重做。
2. 🛑 **不许把画布 2 的重合线说成「两个导联」**。实测 `ecg_20260928_113120.bin` 的 `CH1 ≡ CH2` 逐位 100% 相同（1775/1775），是固件复制，不是两路信号【实测】。第一阶段画布 2 必须显示「无数据」。
3. ⚠ **第一阶段的「记录」按钮只验界面状态机，不验落盘**。任何「记录功能已实现」的表述都不成立。

### 2.5 双通道的真实情况（影响画布 2 的实现）

协议 v2.2 §3.1 原文口径：`channel=I&II` 的「数据上传 → 显示」不验收（无输入源）。

⇒ 实施口径：**LEN=103 帧照常解析两路样本，但画布 2 不绘制**，覆盖文案按需求 v1.2 §4.4 显示「单通道文件（导联 I）— 画布 2 无数据」。这样做的原因是解析层与绘制层解耦：第二阶段真第二路信号到位时，只需打开画布 2 的绘制开关，解析层零改动。

### 2.6 起步前待拍板决策项

| # | 决策 | 默认倾向 | 影响 |
|---|---|---|---|
| D1 | 中文子集字号终值（13/10/12/14 px） | 按需求 v1.2 §7，实机看效果微调 | 字库重生成，不影响架构 |
| D2 | 预置演示文件 | `ecg_20260929_093408.bin`（89.8 s，最干净） | 影响演示观感，不影响代码 |
| D3 | SD 卡容量 / 文件系统 | ✅ **已定：FAT32 + MBR + 512 B 扇区 + ≤32 GB SDHC**。🛑 **exFAT 不可用**——`components/fatfs/src/ffconf.h:294` 是 `#define FF_FS_EXFAT 0`（硬编码，Kconfig 无开关） | 见 `cardiolite-ecg\sdcard\README.md` |
| D4 | 波形层重绘策略 | 先 `lv_canvas` 全量重绘（接口优先级①），S4 实测 < 15 FPS 再退脏矩形 | 影响 S4 的实现路线与 S10 的帧率结论 |

**已定不再讨论**：九按钮布局、配色两套并存、长按 1.5 s 删除、模式/陷波/通道回放灰显、记录属第二阶段、数据只用 LEN=53/103、双画布 216 px。

---

## 3. 总体技术原理

### 3.1 数据源：SD 卡回放

第一阶段不接实时链路。SD 卡里只有一个文件 `ecg.bin`（固定名，再存覆盖）。回放的本质是**用文件读取速度模拟 500 Hz 的样本到达节拍**。

关键设计：**回放必须走与实时相同的数据管线。** 即 `读文件 → 帧解析 → 环形缓冲 → 绘制` 这条链路，在两个阶段完全一致，只有最前端的「样本来源」不同。这样第二阶段接实时链路时，只替换生产者，消费者零改动。

如果为了图快让回放直接读文件画图，第二阶段等于全部重写。这是架构红线。

### 3.2 帧解析

协议 T101 v2.2 帧格式：

```
AA 55 | TYPE | LEN(2, LE) | PAYLOAD | CRC8
```

- CRC8 多项式 0x07，初值 0x00，范围 = `TYPE + LEN + PAYLOAD`
- **帧总长 `= 6 + LEN`**（`AA 55` 2 B + TYPE 1 B + LEN 2 B + CRC8 1 B）。⚠ 这条是 S2 的命门，见下
- LEN 三型：52 / 53 / 103，样本数 `N = (LEN−3)/2`（LEN 奇数）；`N = (LEN−2)/2`（LEN 偶数）
  - LEN=52：总长 **58**，`N=25`（v1.1 旧帧，无 STA，**解析器要兼容但演示不用**）
  - LEN=53：总长 **59**，`N=25`，`SEQ(2) + STA(1) + 25×int16`
  - LEN=103：总长 **109**，`N=50`，CH1 25 点 + CH2 25 点，**块布局不交错**
- STA 状态字节：`bit0` mode / `bit1` notch / `bit2` channel / `bit3` raw853 / `bit4` LOD

**`帧总长 = 6 + LEN` 已用 4 个文件 4185 帧实测确证**【实测 2026-10-23】：

| 文件 | 帧数 | LEN | 文件字节 | 整除校验 |
|---|---|---|---|---|
| `ecg_20260929_093408.bin` | 1796 | 53 | 105,964 | 1796 × 59 = 105,964 ✓ |
| `ecg_20261010_153738_S4Lod-S7.bin` | 1182 | 53 | 69,738 | 1182 × 59 = 69,738 ✓ |
| `ecg_20261010_150845_S4LOD-S7b.bin` | 1136 | 53 | 67,024 | 1136 × 59 = 67,024 ✓ |
| `ecg_20260928_113120.bin` | 71 | 103 | 7,739 | 71 × 109 = 7,739 ✓ |

四个文件 CRC 全过、零尾字节。**参考工程的 `frameSize = 58` 只是 LEN=52 的特例**，照抄到 53/103 上必然错位。

⚠ **最大风险点：不得按固定字节数切帧。** 参考工程 `Core/ReplaySource.cs` 里 `const int frameSize = 58` 只对 LEN=52 成立，双通道 109 B 会整体错位。解析器必须**按 LEN 字段自适应**，帧长从帧里读、不从常量猜。

### 3.3 波形绘制与坐标变换

横纵解耦（引用需求 v1.2 §4.1 / §4.2，此处只给实现形态）：

```
x_px(i) = plotW - (newest_index - i) * px_per_sample    // 最新样本贴右边界
y_px(v) = center_y - v_mV * (gain * mmY)                // 纵向恒 ±5 mV
```

`px_per_sample` 随走纸变：12.5→0.150、25→0.301、50→0.602。增益只影响 `y_px`，**不影响 `x_px`**——这是解耦的判据。

实现上分两层：**网格层单独缓存**（只在换配色/换增益/换尺寸时重画），**波形层每帧全量重绘**。

### 3.4 R 峰检测

Pan-Tompkins 变体，链条与参数（引用需求 v1.2 §5.1）：

```
低通 α=0.25 → 高通 α=0.05 → 三点微分 → 平方 → 60 样本积分 → 自适应阈值 → 不应期 80 样本
```

移植要点：

- C# 的 `double` 全部改 `float`（S3 无硬件双精度）。
- 状态量（积分窗、阈值、不应期计数）必须**属于 Core 1 任务私有**，不跨核共享可变游标。
- 输出只有两种：R 峰样本索引（进定长队列）与 BPM（单变量 + 版本号防撕裂）。

---

## 4. 系统架构与硬件设计

### 4.1 分层架构

```
┌─────────────────────────────────────────────────────┐
│ 应用层    app_state   阶段机 / 按钮事件 / 全局状态      │
├─────────────────────────────────────────────────────┤
│ UI 层     ui_root     三层容器布局                    │
│           ui_waveform 网格 + 波形 + 标尺 + 角标        │
│           ui_button_bar  九按钮 + 长按手势             │
│           ui_theme    两套配色 + NVS 持久化            │
│           ui_bpm      BPM 浮层 + 长按 2s 切配色        │
├─────────────────────────────────────────────────────┤
│ 数据层    ecg_pipeline  Core 1 数据管线任务            │
│           ecg_ringbuf   单产单消环形缓冲               │
│           ecg_frame     帧解析 + CRC8                 │
│           ecg_rpeak     R 峰 + BPM                    │
├─────────────────────────────────────────────────────┤
│ 存储层    storage_sd    SD 挂载（CH422G CS）+ 读文件    │
│           storage_nvs   配置持久化                     │
├─────────────────────────────────────────────────────┤
│ 平台层    bsp_ws_lcd5   RGB + GT911 + CH422G（共享）    │
│           LVGL 8.4.0 + esp_lvgl_adapter               │
└─────────────────────────────────────────────────────┘
```

**依赖方向严格向下，不得反向**：数据层不许 `#include` UI 层头文件；UI 层通过 `app_state` 的只读快照取值。

### 4.2 双核任务边界

| | Core 0 | Core 1 |
|---|---|---|
| 任务 | LVGL 渲染 + 触摸事件 + UI 状态机 | 数据源（读 SD / 帧解析 / 写环缓）+ R 峰检测 |
| 优先级 | LVGL task（adapter 管理，默认） | 数据管线任务，**低于 LVGL** |
| 阻塞纪律 | 🛑 **绘制路径不得有任何阻塞操作**：不读 SD、不申请大块内存、不等待信号量超时 > 1 tick | 允许阻塞（SD 读、文件 IO） |
| 共享数据 | 只读环缓（原子读指针）、只读 BPM 快照 | 写环缓、写 R 峰队列、写 BPM |

**核归属的落地方式（⚠ 2026-10-23 纠正，原判断有误）**

不要用 `CONFIG_EXAMPLE_LVGL_PORT_TASK_CORE`。查证结论：

| 事实 | 出处 |
|---|---|
| 它是**厂家 demo 的 Kconfig**，不是 adapter 的 | `lcd-bringup/main/Kconfig.projbuild` |
| `esp_lvgl_adapter` 的 Kconfig **没有**任何 TASK_CORE 选项 | `managed_components/espressif__esp_lvgl_adapter/Kconfig` |
| `lcd-bringup/main/main.c` **零引用**它 ⇒ 旧工程里也是死配置 | grep 无命中 |
| adapter 真正的控制点是 `esp_lv_adapter_config_t.task_core_id` | `esp_lv_adapter.h:90` |
| 其默认值 `ESP_LV_ADAPTER_DEFAULT_TASK_CORE_ID = -1` ⇒ `tskNO_AFFINITY` ⇒ **任意落核** | `esp_lv_adapter.h:104` + `esp_lv_adapter.c:690` |

⇒ 必须**在代码里显式赋值**：

```c
adapter_config.task_core_id = 0;   /* 不用 sdkconfig，不依赖 Kconfig 是否存在 */
```

**运行期验证方式**：在 LVGL 任务上下文里放探针 —— `lv_timer` 的回调由 `lv_timer_handler()` 调用，而后者在 adapter 的 `"lvgl"` worker 任务里执行，所以回调里的 `xPortGetCoreID()` 就是 LVGL 任务**实际**所在的核。比读配置可靠。

> 同源问题：`CONFIG_EXAMPLE_LVGL_PORT_AVOID_TEAR_ENABLE` 也是死配置。抗撕裂模式由 `esp_lv_adapter_display.h:74` **硬编码**为 `TRIPLE_PARTIAL`（三缓冲），sdkconfig 改不动。详见 §4.5。

**跨核通信方式**（引用需求 v1.2 §6.2）：

| 数据 | 方式 | 理由 |
|---|---|---|
| 波形样本 | 环形缓冲 + 原子读写指针 | 单产单消，无锁 |
| R 峰索引 | 定长队列（深度 64） | 生产者低频（~1 Hz），队列足够 |
| BPM | 单变量 + 版本号 | 防撕裂（读到旧值可接受，读到半更新不可接受） |
| UI 命令 | FreeRTOS 队列 | 触摸事件到数据层的唯一通道 |

### 4.3 模块划分与文件清单

`cardiolite-ecg\` 的规划结构（S0 建骨架，后续步骤逐步填实）：

```text
cardiolite-ecg\
├─ CMakeLists.txt              EXTRA_COMPONENT_DIRS 指向 ..\bsp_ws_lcd5
├─ partitions.csv              抄 lcd-bringup（factory 4 MB + storage 1 MB）
├─ sdkconfig.defaults          抄 lcd-bringup，关 widgets demo、加 -O2 与 BPM 30px 字体
├─ docs\                       需求 v1.2、本文档、ui-mockup.html
└─ main\
   ├─ CMakeLists.txt
   ├─ idf_component.yml        显式锁版本（见 §5.3）
   ├─ main.c                   app_main：初始化顺序编排
   ├─ app_config.h             所有编译期常量（尺寸/阈值/时限/引脚）
   ├─ app_state.h / .c         阶段机、按钮事件、状态快照
   ├─ ui\
   │  ├─ ui_root.h / .c        三层容器、区域坐标
   │  ├─ ui_waveform.h / .c    双画布、网格层缓存、波形层重绘
   │  ├─ ui_button_bar.h / .c  九按钮创建、灰显规则、长按手势
   │  ├─ ui_theme.h / .c       ui_theme_t 两套配色 + NVS 读写
   │  └─ ui_bpm.h / .c         BPM 浮层 + 长按 2 s 切换
   ├─ ecg\
   │  ├─ ecg_frame.h / .c      帧解析 + CRC8 + 三型自适应
   │  ├─ ecg_ringbuf.h / .c    环形缓冲（原子指针）
   │  ├─ ecg_pipeline.h / .c   Core 1 任务主体
   │  └─ ecg_rpeak.h / .c      Pan-Tompkins（float）+ BPM
   └─ storage\
      ├─ storage_sd.h / .c     SD 挂载 + CH422G 片选 + 文件读取
      └─ storage_nvs.h / .c    配色 / 增益 / 走纸持久化
```

**划分判据**：一个 `.c` 文件对应一个可独立验收的能力。若某个文件在 S6 之前就超过 400 行，说明划分错了，先拆再写。

### 4.4 数据流

```text
                    Core 1                                     Core 0
┌──────────────┐   ┌──────────┐   ┌──────────┐   ┌───────────────────────┐
│ storage_sd   │──►│ecg_frame │──►│ecg_ringbuf│──►│ ui_waveform           │
│ 读 ecg.bin   │   │解析+CRC8 │   │ 单产单消  │   │ 取样本→坐标→canvas    │
└──────────────┘   └────┬─────┘   └──────────┘   └───────────────────────┘
                        │                                   ▲
                        ▼                                   │ BPM 快照
                   ┌──────────┐   ┌──────────┐   ┌─────────┴─────────────┐
                   │ecg_rpeak │──►│ R峰队列  │   │ ui_bpm / app_state    │
                   │float 移植│   │ BPM 变量 │   │ 浮层 + 状态机          │
                   └──────────┘   └──────────┘   └───────────────────────┘
                                                             ▲
                                                    ┌────────┴────────┐
                                                    │ ui_button_bar   │
                                                    │ 触摸 → 队列      │
                                                    └─────────────────┘
```

**回放与实时的唯一差别**在 `storage_sd` 与 `ecg_frame` 之间：回放是「文件读取 + 定速投喂」，实时是「I2C 中断 + 不定时到达」。`ecg_ringbuf` 之后的路径两者完全一致。

### 4.5 内存与带宽预算【推算，S4 实测】

| 项 | 大小 | 位置 | 依据 |
|---|---|---|---|
| LVGL framebuffer（anti-tear **三缓冲**） | 768,000 B × 3 = **2,304,000 B**（2,250 KiB） | PSRAM | 800 × 480 × 2 B（RGB565），三帧。模式硬编码为 `TRIPLE_PARTIAL`，见下方注 |
| 波形画布 2 层 | 800 × 216 × 2 B × 2 = **691,200 B**（675 KiB） | PSRAM | 需求 v1.2 §4.5 的 691 KB 即此字节量 |
| 环形缓冲（2 ch × 5000 样本 × 4 B） | **40,000 B**（39 KiB） | **内部 RAM 优先** | 500 Hz × 10 s 最长窗口（12.5 mm/s） |
| LVGL 任务栈 | **12 KiB** | PSRAM（`stack_in_psram=true`） | 沿用 lcd-bringup 实测可用值 |
| 中文字库子集 | **12~18 KiB** | Flash（非 RAM） | 需求 v1.2 §7 |
| R 峰队列 | **256 B** | 内部 RAM | 64 × 4 B |
| **PSRAM 合计** | **≈2,940 KiB（≈2.87 MiB）/ 8 MB** | | 2,250 + 675 + 12 ≈ 2,937 KiB。余量约 5.2 MiB，不是瓶颈 |
| **内部 RAM** | **≈40 KB 增量** | | 需在 S3 实测剩余 heap |

> **口径说明一（三缓冲是硬事实，不是选择）**：`ESP_LV_ADAPTER_TEAR_AVOID_MODE_DEFAULT_RGB` 在 `esp_lv_adapter_display.h:74` 被**硬编码**为 `TEAR_AVOID_MODE_TRIPLE_PARTIAL`，而 `display_manager.c:1430-1432` 对该模式 `return 3`。上层没有 Kconfig 能改它（`CONFIG_EXAMPLE_LVGL_PORT_AVOID_TEAR_ENABLE` 是死配置）。⇒ **framebuffer 一定是 3 个**。若要降到 2 个，得改 adapter 源码，不在本计划范围。
>
> **口径说明二（字节 / KiB 不混用）**：800×480×2 B = 768,000 B（= 750 KiB），三帧 = 2,304,000 B。需求 v1.2 §4.5 的「691 KB」按**字节**理解为 691,200 B ✓。

**带宽**：一帧全屏 768 KB。20 FPS ⇒ 15.4 MB/s 的持续写。需求 v1.2 §4.5 给的 20~40 MB/s 是 PSRAM 理论写速的保守区间，**实际可用受抗撕裂策略影响**——anti-tear 模式下 framebuffer 切换会让有效带宽打折。这是 S4 最需要实测的数字。

**降级路径**（预先定义，不临时拍脑袋）：① canvas 全量重绘 → ② 波形层脏矩形 → ③ 降帧率到 15 FPS 并接受。**不使用 `lv_chart`**（LVGL 的 chart 控件为折线做了大量通用处理，逐点更新开销远超 canvas 直写）。

### 4.6 硬件接口与 CH422G

| 项 | 事实 | 出处 |
|---|---|---|
| 屏 | 800×480 RGB565，pclk 16 MHz，ST7262 | BSP 实测点亮 |
| 触摸 | GT911 @ I2C 0x5D，SDA=GPIO8 / SCL=GPIO9，400 kHz | BSP 实测 |
| IO 扩展 | CH422G（0x24 模式 / 0x38 输出 / 0x26 输入），**输出不可回读** | `esp_io_expander_ch422g.c:216-223` |
| EXIO1~4 | bit1 触摸复位 / bit2 背光 / bit3 屏复位 / **bit4 SD 片选（0=选中）** | 原理图确认 2026-10-23 |
| CH422G 常量 | `0x1E` = 稳态（三复位释放 + SD 未选中）；`0x0E` = SD 选中 | 见 §4.6.1 |
| SD 引脚 | MOSI=11 / CLK=12 / MISO=13，**CS 走 CH422G，不是 GPIO** | 厂家 `waveshare_sd_card.h:12`、`CONFIG_EXAMPLE_PIN_CS=-1` |
| 串口 | COM35（原生 USB-Serial-JTAG） | 实测 |
| 芯片安全 | Secure Boot / Flash Encryption 均 Disabled | 实测 |

#### 4.6.1 CH422G 输出位定义

| EXIO | bit | 网络 | 稳态 |
|---|---|---|---|
| EXIO1 | bit1 | CTP RST | 1（释放） |
| EXIO2 | bit2 | LCD_BL | 1（点亮） |
| EXIO3 | bit3 | LCD RST | 1（释放） |
| EXIO4 | bit4 | **SD_CS** | 1=释放，**0=选中** |
| EXIO5 | bit5 | USB_SEL（推断） | — |
| — | bit0 | 未知 | 三组实测值恒 0 |

```
0x1E = 0b0001_1110   稳态：三复位释放 + SD 未选中（= backlight_on() 写入值）
0x0E = 0b0000_1110   SD 使能：同上，SD_CS=0
0x0A = 0b0000_1010   厂家 SD 示例：背光被关（bit2=0），🛑 禁止使用
```

⚠ **两条实现纪律**：
1. `0x38` 硬件不可回读 ⇒ BSP 内必须维护影子副本 `s_out_shadow`，对外只给位操作 API。业务代码**禁止出现裸 `0x0E` / `0x1E`**。
2. 必须以 `0x1E` 为基准做位运算，**不得从 `0x2E` 派生**（`0x2E` bit5=1、`0x1E` bit5=0，派生会把 USB_SEL 带上）。

详细证据链见需求 v1.2 §12.5.1。

### 4.7 UI 布局（像素级，供 S4/S6 比对）

| 区域 | 坐标 / 尺寸 |
|---|---|
| 画布 1（含网格+波形+角标） | y = 0 ~ 215，高 216 px |
| 画布 2 | y = 216 ~ 431，高 216 px |
| 按钮栏 | y = 432 ~ 479，高 48 px |
| 单按钮 | 85 × 40，间距 5 px，左起 x = 8 |
| BPM 浮层 | 画布 1 右上，压住角标 |
| 可绘制区 | 752 × 200（每画布，留边框与角标空间） |

⚠ 上表是**实施目标**，与 `ui-mockup.html` 的坐标存在 ≤2 px 差异（仿真稿按浏览器渲染对齐）。S4 以仿真稿为视觉基准，以上表为像素判据基准；两者冲突时**以仿真稿的观感优先，以本表记录实测值**。

---

## 5. 开发环境与工程组织

### 5.1 开发环境基线

| 项目 | 当前实测基线 | 实施要求 |
|---|---|---|
| ESP-IDF | `C:\esp\v6.1\esp-idf` **v6.1.0**（EIM 安装） | **固定，不中途升级**。v6.1 迁移已踩完，升版要重走 |
| 工具链 | `C:\Espressif\tools`，`xtensa-esp-elf` **esp-15.2.0_20251204** | 由 `tools/idf.sh` 注入 PATH |
| CMake / Ninja | 4.0.3 / 1.12.1 | 同上 |
| ccache | 4.12.1 | `IDF_CCACHE_ENABLE=1`，见 §5.4 |
| Python | `C:\Espressif\tools\python\v6.1\venv` | 由 `tools/idf.sh` 指定 |
| LVGL | **8.4.0** | 第一阶段不升 9.x |
| esp_lvgl_adapter | **0.5.3** | 抗撕裂 / 触摸注册 / 任务管理都靠它 |
| IDE | VS Code + ESP-IDF 插件 | 我只负责编译；**烧录由猛哥在 VS Code 执行** |
| 串口 | **COM35** | 原生 USB-Serial-JTAG |
| 目标板 | ESP32-S3-Touch-LCD-5，MAC `28:84:85:a2:55:74` | 单板，无多目标混淆风险 |

### 5.2 建议工作目录

以 `C:\work2026\CardioLite` 为工作目录（**非 git 仓库，当前未纳入版本管理**）：

```text
C:\work2026\CardioLite\              ← 工作目录
├─ NOW.md                            交接锚点（新对话开局只读它）
├─ bsp_ws_lcd5\                      共享 BSP 组件（唯一一份，勿复制）
│  ├─ CMakeLists.txt
│  ├─ include\waveshare_rgb_lcd_port.h
│  └─ waveshare_rgb_lcd_port.c
├─ lcd-bringup\                      硬件验证工程（S0 的骨架模板）
│  ├─ CMakeLists.txt / partitions.csv / sdkconfig.defaults
│  ├─ main\{main.c, CMakeLists.txt, idf_component.yml}
│  ├─ docs\idf-v6.1-migration.md     v6.1 迁移全记录（§8 结构 / §9 ccache）
│  └─ factory_backup\                出厂固件 16 MB 整片备份
├─ cardiolite-ecg\                   ★ 正式 UI 工程，本计划的实施对象
│  ├─ docs\                          需求 v1.2 / 本文档 / ui-mockup.html
│  ├─ sdcard\                        ★ 拷卡内容：ecg.bin + 5 个测试样本 + README
│  └─ main\                          （S0 起建，见 §4.3）
├─ tools\
│  ├─ idf.sh                         ★ 构建唯一入口
│  ├─ _idf_py.py                     idf.sh 调用的 python 包装（清 MSYSTEM）
│  └─ idf.cmd                        cmd 环境入口
├─ ESP32-S3-Touch-LCD-5\             厂家资料（PDF / 原理图 / demo，只读）
├─ .smoke\                           数据校验脚本（check_recordings.py 等）
└─ .workbuddy\memory\                项目记忆（MEMORY.md + 日日报）
```

**纪律**：

- `bsp_ws_lcd5\` 是**共享组件**，`lcd-bringup\` 与 `cardiolite-ecg\` 都通过 `EXTRA_COMPONENT_DIRS` 引用它。**不允许任何工程内出现 BSP 源码副本**——已发生过一次副本不同步问题。
- `ESP32-S3-Touch-LCD-5\` 是**厂家资料，只读**，不改不搬。
- `cardiolite-ecg\docs\` 只放本工程产出的 md/html；厂家 PDF 不放这里。
- 每个工程各自的 `build\` 目录独立，不共用、不纳入备份。

### 5.3 组件依赖与版本锁定

`cardiolite-ecg\main\idf_component.yml` 必须**显式锁版本**，不用 `*`：

```yaml
dependencies:
  idf:
    version: ">=5.5.0"
  lvgl/lvgl:
    version: ">8.3.9,<9"          # 8.4.0 实测
    public: true
  espressif/esp_lvgl_adapter:
    version: "^0.5.2"             # 实装 0.5.3
  espressif/esp_lcd_touch_gt911:
    version: "^1"                 # 实装 1.2.1
```

⚠ `esp_lvgl_adapter` 会传递引入 8 个依赖（`button` 4.2.2、`knob` 1.1.0、`esp_lv_fs` 1.0.1、`esp_lv_decoder` 0.4.3、`freetype`、`libpng`、`zlib`、`esp_mmap_assets`）。这些**不是**本项目主动要的，但会被编进固件。第一阶段若 flash 吃紧，先查这些能不能裁（`esp_lv_decoder` 与 `freetype` 最可能可裁）。

### 5.4 构建纪律（三条硬约束，全是踩出来的）

**① 构建必须走 `tools/idf.sh`，不能直接 `idf.py`。**

Git Bash 会注入 `MSYSTEM` 环境变量，ESP-IDF v6.1 的 `idf.py` 入口检测到它就只打 warning、**不调用 `main()`**，表现为**静默空跑、无产物、退出码 0**。`idf.sh` 负责清 `MSYSTEM` 并把 Windows 风格 PATH 交给 python。

```bash
./tools/idf.sh -C C:/work2026/CardioLite/cardiolite-ecg build
```

**② ccache 偶发死锁：判据是「无 cc1 子进程」。**

症状：ninja 日志停 10 min 以上不动、`gcc.exe` CPU 不涨。判据：看进程树里**没有 `cc1.exe` 子进程**，且停滞的是 `ccache.exe`。

处置：`taskkill /F` 掉停滞的 `ccache.exe`，然后进入 build 目录直接续编：

```bash
cd C:/work2026/CardioLite/cardiolite-ecg/build && ninja -j 6
```

⚠ `idf.py build -j 6` **不可用**（v6.1 的 `build` 子命令无 `-j` 选项，报 `No such option '-j'`）。

**③ 改 sdkconfig = ccache 全 miss = 全量重编 40~50 min【实测】。**

⇒ **S0 必须一次性把 sdkconfig.defaults 定死。** S0 之后任何 sdkconfig 改动都要先算清代价：一次改动的成本等于一次冷构建加一次回退风险。宁可多花 10 分钟在 S0 把配置想全，也不要后面改三次。

冷构建 40~50 min、增量数分钟。这个数量级决定了**本项目的迭代节奏是「小步多次编译」，不是「大改一次编译」**。

### 5.5 版本管理策略

当前 `C:\work2026\CardioLite` **未纳入 git**。建议（非强制）：

- 若纳入：`.gitignore` 加 `build/`、`managed_components/`、`.workbuddy/`（记忆目录不公开）。
- 每个 S 步骤完成后做一次快照（`factory_backup` 已有整片备份，不重复）。
- S0 建骨架完成后打第一个标签 `s0_skeleton`；S4 画布可用后打 `s4_render_ok`。
- **不把 `lcd-bringup\build\`（3538 个文件）纳入任何版本控制。**

### 5.6 复用资产与外部参考基线

| 资产 | 用途 | 说明 |
|---|---|---|
| `C:\work2026\VsPro\WinApp_ADSECG\ADS1293_Ecg\` | **算法与 UI 设计参考**（只读） | `EcgWaveformControl.cs` 的横纵解耦公式（L136-145）、`Core/ReplaySource.cs` 的回放节奏。⚠ `frameSize = 58` 不可照抄 |
| 同上 `bin\Debug\net10.0-windows\recordings\*.bin` | 演示数据源 | 8,033 帧 CRC 全过【实测】 |
| `C:\work2026\VsPro\ads1293_demo\tools\_t141\` | 协议 T101 v2.2 依据 | 帧格式、STA 位定义、命令字有效性 |
| `lcd-bringup\` | **S0 骨架模板** | `CMakeLists.txt` / `partitions.csv` / `sdkconfig.defaults` / `main.c` 全部可抄 |
| `ESP32-S3-Touch-LCD-5\ESP-IDF\03_SD_Test\` | **S1 的 SD 挂载参考** | ⚠ `sd_card.c:94` 的 `0x0A` 会关背光，**只能参考流程，不能抄常量** |
| `cardiolite-ecg\docs\ui-mockup.html` | S4/S6 视觉比对基准 | 九按钮 + 三配色 + 回放状态机，可交互 |
| `bsp_ws_lcd5\` | 平台层 | 已验证点亮，**只增不改**（S1 需补 CH422G API） |

---

## 6. 实施计划（S0~S10）

按「每步可独立验证」拆分。**前一步不通过不进下一步。** 十步归为五个阶段，阶段末有决策门（见 §7）。

### 阶段 A · 平台基线

## S0：工程骨架

**目标**：在 `cardiolite-ecg\` 建出可编译、可点亮、可触摸的空工程，sdkconfig 一次定死。

**任务**

1. `CMakeLists.txt`：`set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../bsp_ws_lcd5")` + `project(cardiolite_terminal)`。抄 `lcd-bringup\CMakeLists.txt`，**只改 project 名**。
2. `partitions.csv`：**直接抄** `lcd-bringup\partitions.csv`（`nvs` 24K@0x9000 / `phy_init` 4K@0xf000 / `factory` 4M@0x10000 / `storage` 1M@0x410000）。分区表已与厂家对齐，**不重新设计**。
3. `sdkconfig.defaults`：抄 `lcd-bringup\sdkconfig.defaults`，改两处：
   - ⚠ **不写** `CONFIG_EXAMPLE_LVGL_PORT_TASK_CORE` / `..._AVOID_TEAR_ENABLE`（本工程无此 Kconfig，是死配置，见 §4.2）
   - 关 `CONFIG_LV_USE_DEMO_WIDGETS`
   - 加 `CONFIG_LV_FONT_MONTSERRAT_30=y`（BPM 数字）、`CONFIG_COMPILER_OPTIMIZATION_PERF=y`（-O2）、`CONFIG_ESP_MAIN_TASK_STACK_SIZE=8192`
   - `CONFIG_LV_USE_DEMO_WIDGETS=y` → **注释掉**（UI 工程不要 demo 占 flash）
   - 保留：`CONFIG_LV_COLOR_DEPTH_16=y`、`CONFIG_LV_MEM_CUSTOM=y`、`CONFIG_LV_ATTRIBUTE_FAST_MEM_USE_IRAM=y`、`CONFIG_SPIRAM_XIP_FROM_PSRAM=y`、`CONFIG_FREERTOS_HZ=1000`、`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`
4. `main\main.c`：抄 `lcd-bringup\main\main.c` 的初始化序列（BSP init → backlight → adapter init → register display → register touch → adapter start），把 `lv_demo_widgets()` 换成一个空的 `lv_obj_t` 页面 + 一行中文/英文标签。**核心改动：`adapter_config.task_core_id = 0`**（原因见 §4.2）。
5. `main\app_config.h`：把 §4.7 的布局常量、§4.6.1 的 CH422G 常量、走纸/增益档位全部定义进来。**这一步就要建**，后面所有文件只引用不写字面量。

**验收**

- `./tools/idf.sh -C .../cardiolite-ecg build` 通过，0 error（warning 允许存在但需记录）。
- 启动日志 `Project name:` 显示 `cardiolite_terminal`。
- 上板：背光亮、屏显示空页面、触摸可点（点一下打日志）。
- **LVGL 任务实机落在 Core 0**：日志出现 `[probe] LVGL worker task actually runs on core 0`（`lv_timer` 回调里读 `xPortGetCoreID()`，是运行期硬证据，见 §4.2）。

**交付物**：可编译可点亮的空工程 + 定死的 `sdkconfig.defaults`。

> ⚠ S0 是**唯一**允许改 sdkconfig 的步骤。S0 之后任何 sdkconfig 改动 = 全量重编 40~50 min。

---

### 阶段 B · 数据链路

## S1：SD 卡挂载与文件打开

**目标**：把 SD 卡挂上，打开 `ecg.bin`，读到首帧。

**任务**

1. **先补 BSP 的 CH422G 输出 API**（`bsp_ws_lcd5\include\waveshare_rgb_lcd_port.h` + `.c`）：
   ```c
   esp_err_t waveshare_ch422g_set_out_bits(uint8_t clr_mask, uint8_t set_mask);
   ```
   内部维护 `static uint8_t s_out_shadow = 0x1E;`（`0x38` 硬件不可回读，见 §4.6.1）。**不得在 SD 模块里另建 I2C 总线**——两条总线抢 GPIO8/9 必把触摸打死。
2. `storage\storage_sd.c`：
   - `spi_bus_initialize(SPI2_HOST, ...)`，MOSI=11 / MISO=13 / CLK=12
   - `sdspi_device_config_t.gpio_cs = -1`（CS 走 CH422G，**不是 GPIO**）
   - 挂载前调 `waveshare_ch422g_set_out_bits(1<<4, 0)`（写 `0x0E`，选中 SD）
   - `format_if_mount_failed = false`（🛑 **不能自动格式化**，不能因挂载失败洗掉用户数据）
   - `max_files = 5`、`allocation_unit_size = 16 KB`
3. 打开 `/sdcard/ecg.bin`，读首 8 字节打印 hex。

**验收**

- 串口打印 `ecg.bin` 文件大小与首帧帧头 `AA 55`。
- **背光保持点亮**（不被 `0x0A` 那类值误关）。
- **触摸仍可用**（证明没有额外占用 I2C、没有误清 bit1）。
- 三条全过 ⇒ CH422G 位定义定案，§4.6.1 从「推断」升为「实测确认」。

**交付物**：`storage_sd.c/.h` + BSP 的 CH422G API。

> ⚠ SD_CS 拉低后 SD 卡在整个挂载周期都被选中。本板 SPI2 上只有它一个从设备，可行；**将来要挂第二颗器件必须先写回 `0x1E` 释放**。

## S2：帧解析（52 / 53 / 103 三型）

**目标**：把字节流切成帧，CRC 零误判，三型自适应。

**任务**

1. `ecg\ecg_frame.c`：实现 `AA 55 | TYPE | LEN(2,LE) | PAYLOAD | CRC8` 解析，CRC8 多项式 **0x07**、初值 **0x00**。
2. 三型自适应：`N = (LEN−3)/2`，按 LEN 分支，**禁止按固定字节切帧**。
3. STA 位解析：`bit0` mode / `bit1` notch / `bit2` channel / `bit3` raw853 / `bit4` LOD。
4. 无 STA 的 LEN=52 旧帧：三参数状态填哨兵 `0xFF`（「未知」），**不得显示成「诊断/关/I」**。

**验收**

- 用需求 v1.2 §2.4 的 **6 个真实文件**跑一遍，帧数与 CRC 结果与表中一致。
- `ecg_20260929_093408.bin` = **1796 帧 / 0 错帧**；`ecg_20261010_153738_S4Lod-S7.bin` = **1182 帧**。
- `ecg_20260928_113120.bin`（LEN=103）解析出双通道各 1775 样本，**CH1 ≡ CH2** 逐位相同（这是正确行为，不是 bug）。

**交付物**：`ecg_frame.c/.h` + 一份解析自检日志。

## S3：环形缓冲 + 双核数据管线

**目标**：Core 1 产、Core 0 消，500 Hz 灌 30 min 不丢不泄。

**任务**

1. `ecg\ecg_ringbuf.c`：单产单消环形缓冲，容量 2 ch × 5000 样本 × `int32_t`（= 40 KB，内部 RAM 优先）。读写指针原子操作，**不共享可变游标**。
2. `ecg\ecg_pipeline.c`：Core 1 任务，`xTaskCreatePinnedToCore(..., 1)`，优先级**低于 LVGL task**。
3. 回放投喂：按 500 Hz 节拍从文件读→解析→写环缓。用 `vTaskDelayUntil` 定时，不用忙等。
4. 溢出策略：写满时**丢最旧**并计数（丢帧计数要能打出来），不阻塞生产者。

**验收**

- 30 min 连续灌入，丢帧计数 = 0，`esp_get_free_heap_size()` 前后差 < 4 KB（无泄漏）。
- 生产者平均阻塞率 < 10%（说明消费端跟得上）。
- 内部 RAM 剩余在 §4.5 的预算范围内（约 40 KB 增量内）。

**交付物**：`ecg_ringbuf` + `ecg_pipeline` + 长跑日志。

---

### 阶段 C · 渲染

## S4：画布绘制（网格 + 基线 + 标尺 + 波形）

**目标**：画出一屏像心电图纸的东西，像素级对上仿真稿。

**任务**

1. `ui\ui_root.c`：三层容器（画布 1 / 画布 2 / 按钮栏），坐标见 §4.7。
2. `ui\ui_waveform.c`：
   - 网格层：小格 1 mm、大格每 5 小格；毫米基准 < 1 px 时只画大格。**单独缓存**，只在换配色/增益/尺寸时重画。
   - 波形层：`lv_canvas`，从环缓取样本 → `x_px/y_px` 变换 → 画点。**每帧全量重绘**（接口优先级①，见 §2.6 D4）。
   - 定标标尺：左上 1 mV 方波，高度 = `增益 × mmY`（**随增益变，不得写死**）。
   - 角标：左上 `导联 I · CH1 = IN2−IN1`；右上 `5.0 s 窗口 / 500.0 Hz`。
   - 画布 2 覆盖文案：`单通道文件（导联 I）— 画布 2 无数据`。
3. `ui\ui_theme.c`：`ui_theme_t` 结构体 + 两套配色。**绘制代码禁止内联颜色字面量**。

**验收**

- 截图与 `ui-mockup.html` 比对，布局差异 ≤ 2 px。
- 默认增益下 **1 mV = 20 px ± 1 px**（画已知幅度方波量像素）。
- **帧率实测并记录**。若 < 15 FPS，切接口优先级②（脏矩形）后重测。
- 画布 2 恒显示「无数据」，**没有与画布 1 重合的线**。

**交付物**：`ui_root` / `ui_waveform` / `ui_theme` + 一屏截图 + 帧率实测数字。

> ⚠ 本步是**唯一**可能推翻 §4.5 预算的地方。实测帧率低于 15 FPS 时，先查是不是「网格层没缓存 / 每帧重画了整屏」，**不要**直接降需求。

## S5：走纸 / 增益换档

**目标**：三档走纸 × 四档增益全部可用，标定关系不破。

**任务**

1. 走纸 12.5 / 25 / 50 mm/s ⇒ `px_per_sample` 0.150 / 0.301 / 0.602，可见窗口 10 / 5 / 2.5 s。
2. 增益 2.5 / 5 / 10 / 20 ⇒ 10 / 20 / 40 / 80 px/mV。
3. 换档时机：**只改下一点的变换参数，不重排已有样本**（历史波形横向位置不动，纵向整体缩放——这是心电图纸的语义）。

**验收**

- 25 mm/s 下可见窗口 **5.0 s ± 0.1 s**（量两 R 峰间距）。
- 1 mV 标尺高度随增益变：20 / 40 / 80 px。
- 切换走纸后，**横向 `px/sample` 变、纵向 `px/mV` 不变**（横纵解耦未被破坏）。

**交付物**：换档逻辑 + 三档走纸截图。

---

### 阶段 D · 交互与算法

## S6：九按钮 + 状态机

**目标**：九个按钮全部可用，状态转移与仿真稿逐态对齐。

**任务**

1. `ui\ui_button_bar.c`：9 个 85×40 按钮，单行排布。主行 13 px / 副行 10 px。
2. `app_state.c`：阶段机（idle / playing / paused / ended / frozen）+ 按钮事件分发。
3. 灰显规则（需求 v1.2 §8.2 / §8.5）：
   - 采集按钮：第一阶段**恒灰显**
   - 模式 / 陷波 / 通道：回放阶段**恒灰显**
   - 回放·记录：SD 无文件时灰显
4. 长按「回放」**1.5 s** 弹删除确认（不用双击——LVGL 无原生双击，做双击会让单击迟滞 300 ms）。
5. 回放结束自动转「重播」，点击从头播（缓冲先清）。

**验收**

- 逐态与仿真稿对齐：灰显 / 回放 / 暂停 / 重播 / 删除弹窗。
- 9 个按钮全部可点，**无误触相邻按钮**（手指粗测）。
- 长按 **1.5 s ± 0.3 s** 触发删除确认。
- 冻结后波形停、但解冻瞬间显示的是**最新**样本（管线未停）。

**交付物**：`ui_button_bar` + `app_state` + 状态机转移图（可贴进文档）。

## S7：R 峰 + BPM

**目标**：BPM 显示正确，误差 ≤ 2 bpm。

**任务**

1. `ecg\ecg_rpeak.c`：Pan-Tompkins 变体，**`double` 全改 `float`**。参数见 §3.4。
2. 运行在 Core 1，状态量任务私有。
3. 输出：R 峰索引进定长队列（深度 64）；BPM 单变量 + 版本号。

**验收**

- 与 WinForms 版对**同一文件**，R 峰位置误差 **≤ 2 样本**。
- BPM 误差 **≤ 2 bpm**（60~120 bpm 区间）。⚠ 基准是**固件输出 vs 参考实现**，不是需求 v1.2 §2.4 表里的粗估值。
- `ecg_20260929_093408.bin` 实测约 42 bpm（63 峰 / 89.8 s）可作为冒烟参考值。

**交付物**：`ecg_rpeak.c/.h` + 比对报告（固件 vs C# 的 R 峰位置表）。

## S8：配色切换

**目标**：两套配色即时切换 + 断电保持。

**任务**

1. `ui\ui_bpm.c`：BPM 浮层长按 **2 s** 触发配色切换（9 个按钮已满，不占按钮位）。
2. `storage\storage_nvs.c`：配色 key 写入 NVS，上电读取。
3. 切换时**网格层必须重画**（颜色变了），波形层自然跟上。

**验收**

- 切换即时生效（无闪烁残留、无颜色错位）。
- **断电重启保持上次选择**（切深色 → 拔电 → 上电仍是深色）。
- 荧光绿第三套**不在切换循环里**（只能改 `DEFAULT_THEME` 编译期常量启用）。

**交付物**：`ui_bpm` + `storage_nvs` + 两套配色的截图各一张。

## S9：raw853 降级

**目标**：`STA.bit3=1` 时正确降级，不输出错误的时间相关量。

**任务**（需求 v1.2 §8.6）

| 项 | 处理 |
|---|---|
| 横幅 | 画布 1 顶部 `raw853 模式 — 波形时基未经标定` |
| BPM | 强制 `--`，停止计算 |
| R 峰标记 | 不绘制 |
| 右上角标 | 改 `raw853 / LEN={n}`，**不显示秒数** |
| 波形 | 仍按 500 Hz 解释绘制（**不做 853 重采样**） |

**验收**

- 用 `ecg_20261010_150845_S4LOD-S7b.bin`（唯一 bit3=1 的样本）：横幅出现、BPM 为 `--`、无 R 峰标记、角标无秒数。

**交付物**：降级路径 + 一张降级态截图。

---

### 阶段 E · 验收

## S10：长跑与全表验收

**目标**：需求 v1.2 §11 全部判据通过。

**任务**

1. 连续回放 30 min，观察 heap 曲线与帧率。
2. 逐条走需求 v1.2 §11 的 17 项判据（含配色切换、画布 2、冻结、清屏、回放结束→重播）。
3. 记录所有实测数字，回填本文档 §4.5 的预算表（把【推算】改成【实测】）。

**验收**：§11 全表通过 + 一份完整的实测数据表。

**交付物**：验收报告 + §4.5 预算表的实测回填。

---

## 7. 步骤总览与决策门

| 阶段 | 步骤 | 核心任务 | 主要交付物 | 进入下一阶段的条件（量化） |
|---|---|---|---|---|
| **A** | S0 | 工程骨架 / sdkconfig 定死 | 空工程 + 定版 sdkconfig | 编译 0 error；`Project name` 正确；上板点亮 + 触摸可点；**LVGL 任务探针实测在 Core 0** |
| **B** | S1 | SD 挂载 | `storage_sd` + BSP CH422G API | 读到首帧 `AA 55`；**背光不灭 + 触摸可用**（三条全过 ⇒ 位定义定案） |
| B | S2 | 帧解析 | `ecg_frame` | 6 个真实文件帧数/CRC 与表一致；1796 帧 0 错 |
| B | S3 | 环缓 + 双核管线 | `ecg_ringbuf` + `ecg_pipeline` | 30 min 丢帧 0、heap 差 < 4 KB、生产者阻塞率 < 10% |
| **C** | S4 | 画布绘制 | `ui_root` / `ui_waveform` / `ui_theme` | 布局差 ≤ 2 px；**1 mV = 20 px ± 1 px**；帧率实测有数字；画布 2 恒「无数据」 |
| C | S5 | 走纸 / 增益 | 换档逻辑 | 25 mm/s 窗口 5.0 s ± 0.1 s；标尺 20/40/80 px；横纵解耦未破 |
| **D** | S6 | 九按钮状态机 | `ui_button_bar` + `app_state` | 逐态与仿真稿一致；9 按钮可点无误触；长按 1.5 s ± 0.3 s |
| D | S7 | R 峰 + BPM | `ecg_rpeak` | R 峰位置误差 ≤ 2 样本；BPM 误差 ≤ 2 bpm（对参考实现） |
| D | S8 | 配色切换 | `ui_bpm` + `storage_nvs` | 切换即时生效；**断电重启保持** |
| D | S9 | raw853 降级 | 降级路径 | 横幅 + BPM `--` + 无 R 峰 + 角标无秒数（4 项全过） |
| **E** | S10 | 长跑验收 | 验收报告 | 需求 v1.2 §11 全 17 项通过；30 min 无重启无泄漏 |

**三条硬闸门**（不满足则停，不许「先往下走回头再补」）：

1. **S0 未过不进 S1。** sdkconfig 未定死就往下走，后面每改一次配置多花 40 min。
2. **S1 未过不进 S2。** SD 都挂不上，解析写得再好也验不了。
3. **S4 未过不进 S6。** 波形画不出来，按钮状态机没有可观察的反馈对象。

---

## 8. 主要风险与对策

| 风险 | 可能表现 | 对策 |
|---|---|---|
| **照抄厂家 SD 示例关掉背光** | SD 挂上了，屏是黑的，误判为「屏挂了」 | 用 `0x0E` 不用 `0x0A`（§4.6.1）；S1 验收加「背光不灭」一条 |
| **CH422G 输出不可回读导致位覆盖** | 某次写 `0x38` 后触摸死了 / 背光灭了 | BSP 维护 `s_out_shadow`，对外只给位操作 API；禁止裸常量 |
| **自建第二条 I2C 总线** | 触摸完全失效（GPIO8/9 被抢） | SD 模块复用 BSP 的 I2C 句柄；S1 验收强制验触摸 |
| **按 58 字节切帧** | 双通道文件整体错位、CRC 全红 | 按 LEN 自适应（§3.2）；S2 用 LEN=103 文件专门验 |
| **PSRAM 带宽不够 20 FPS** | 波形刷新一顿一顿、撕裂 | S4 实测帧率；三级降级（全量→脏矩形→15 FPS）；不预先承诺帧率 |
| **网格层没缓存，每帧重画整屏** | 帧率莫名只有 8 FPS | 网格层单独缓存，只在配色/增益/尺寸变化时重画 |
| **中文字库缺字** | 按钮上出现方框 | 字库按需求 v1.2 §7 的 40 字表生成；S6 逐按钮核对 |
| **sdkconfig 反复改** | 每次 40~50 min 全量重编，进度不可控 | S0 一次定死；之后改配置要先评估代价 |
| **ccache 死锁** | ninja 停 10 min 不动，无 cc1 子进程 | `taskkill` ccache 后 `cd build && ninja -j 6`（§5.4） |
| **raw853 数据被当正常数据解** | BPM 偏低约 41%，用户以为设备坏了 | S9 强制降级；`STA.bit3` 检查必须在解析层完成，不能留给 UI |
| **双通道重合被当成两个导联** | 演示时说错，误导评审 | 画布 2 恒「无数据」；§2.4 边界第 2 条 |
| **`double` 未改 `float`** | R 峰检测吃满 CPU，帧率塌 | S7 代码审查时 grep `double`，见一个查一个 |
| **回放路径与实时路径分叉** | 第二阶段全部重写 | 红线：回放必须走同一条 `ringbuf → 绘制` 链路（§3.1） |

---

## 9. 每步自检记录模板

每个 S 步骤完成后，按此模板记一条（追加进 `.workbuddy\memory\YYYY-MM-DD.md`）：

```text
step_id / date / git_tag
idf_version / lvgl_version / adapter_version
sdkconfig_hash                      # S0 之后应恒定
build_time / build_result           # 冷构建 or 增量
flash_used / ram_used / psram_used
screenshot_file                     # 有 UI 变化的步骤必填
measured_fps / measured_layout_delta_px
acceptance_items                    # 逐条打勾，未过的写明原因
known_issues / deferred_items
next_step_blockers                  # 空 = 可进下一步
```

**纪律**：

- 未过的验收项**不许打勾**，写「未过 + 原因」。
- 「已知问题」与「延后项」分开记——前者是 bug，后者是计划内的缺席。
- 没有实测数字的验收项视为未过（例如「帧率还不错」不算过）。

---

## 10. 关键技术边界

1. **回放 ≠ 实时。** 两条链路只有最前端不同，`ringbuf` 之后必须共用。第一阶段通过不代表第二阶段不用重做前端。
2. **双通道是假的。** `CH1 ≡ CH2` 逐位相同，是固件复制。画布 2 第一阶段显示「无数据」。
3. **画布 2 的 P 波只有 2~4 px。** `1 mV = 20 px` 与双画布 216 px 是已拍板结构，这是它的必然代价，不是 bug。
4. **20 FPS 是推算不是承诺。** S4 实测前不得对外说任何帧率数字。
5. **raw853 只降级不重采样。** 支持 853 Hz 要重做 R 峰检测器的全部标定（不应期、积分窗、BPM 换算 4 处触点），不做。
6. **BPM 有两个口径，不可混用。** 需求 v1.2 §2.4 表里的是 PC 端粗估（简化阈值法）；§11 的「≤ 2 bpm」判据必须用 S7 固件输出去对参考实现。
7. **`0x38` 不可回读。** 任何「先读回来改一位」的写法都是错的，必须走软件影子。
8. **S3 只有单精度 FPU。** `double` 一律改 `float`；这条没有折中方案。
9. **不加硬件、不换屏、不升 LVGL 9。** 全部是本计划外的变更，需重新立计划。
10. **我只编不烧。** 烧录与调试由猛哥在 VS Code 执行；屏幕观感（颜色/触摸/撕裂）只有人眼能判。

---

## 11. 参考资料

**本项目产出（首要依据）：**

1. `cardiolite-ecg\docs\ECG显示终端_嵌入式UI_需求描述_v1.2_2026-10-22.md` —— **UI 需求的唯一依据**（§2.4 数据实测 / §4 标定 / §8 状态机 / §11 验收 / §12.5.1 CH422G 位定义）
2. `cardiolite-ecg\docs\ui-mockup.html` —— 可交互仿真稿（九按钮 + 三配色 + 回放状态机）
3. `cardiolite-ecg\docs\recordings-late-files.png` / `recordings-verification.png` / `recordings-zoom.png` —— 数据波形核对图
4. `lcd-bringup\docs\idf-v6.1-migration.md` —— v6.1 迁移全记录（§2 报错清单 / §7 烧录验证 / §8 结构重构 / §9 ccache 配方）
5. `lcd-bringup\docs\logs\boot_v6.1_20261022.log` —— 首次点亮完整启动日志
6. `NOW.md`（工作区根）—— 跨会话交接锚点

**参考工程（只读）：**

1. `C:\work2026\VsPro\WinApp_ADSECG\ADS1293_Ecg\` —— WinForms 上位机。`EcgWaveformControl.cs:136-145`（横纵解耦警告）、`Core/ReplaySource.cs`（回放节奏，⚠ `frameSize=58` 不可照抄）
2. `...\bin\Debug\net10.0-windows\recordings\*.bin` —— 10 个有效录制文件，8,033 帧 CRC 全过
3. `C:\work2026\VsPro\ads1293_demo\tools\_t141\` —— 协议 T101 v2.2（帧格式 / STA 位 / 命令有效性）

**平台与厂家资料（`ESP32-S3-Touch-LCD-5\`，只读）：**

1. `ESP-IDF\03_SD_Test\main\sd_card.c` —— SD 挂载流程参考（⚠ L94 的 `0x0A` 会关背光）
2. `ESP-IDF\08_lvgl_v8_demo\` —— 厂家 LVGL 示例（BSP 即由此迁移）
3. `Arduino\examples\03_SD_Test\waveshare_sd_card.h:12` —— `#define SD_CS 4`（EXIO4）
4. `Arduino\libraries\ESP32_IO_Expander\src\port\esp_io_expander_ch422g.c` —— CH422G 驱动，**L216-223 证明输出不可回读**；**L196/L205 的 `if (value)` 会在写 0 时跳过**
5. `ESP32-S3-Touch-LCD-5-Sch.pdf` —— 原理图（EXIO1~5 网络定义）
6. `CH422DS1_CN.pdf` / `ST7262.pdf` / `GT911.pdf` —— 器件手册

**上游文档：**

1. `https://docs.waveshare.net/ESP32-S3-Touch-LCD-5` —— 厂家参考指南
2. ESP-IDF v6.1 Programming Guide（`esp_lcd_panel_rgb` / `i2c_master` / `sdspi` / `esp_vfs_fat`）
3. LVGL 8.4 文档（`lv_canvas` / `lv_obj` / `lv_indev` / NVS 无关）

---

## 12. 当前执行起点

**下一步唯一动作：S0。**

具体四项（详见 §6 S0）：

1. `cardiolite-ecg\CMakeLists.txt`（抄 `lcd-bringup`，改 project 名）
2. `cardiolite-ecg\partitions.csv`（直接抄）
3. `cardiolite-ecg\sdkconfig.defaults`（抄 + 关 widgets demo + 加 -O2 / BPM 30px / 主任务栈 8192；**不写两个死配置项**）
4. `cardiolite-ecg\main\`（`main.c` + `app_config.h` + `CMakeLists.txt` + `idf_component.yml`），`main.c` 里 `adapter_config.task_core_id = 0`

**S0 完成的标志**：`./tools/idf.sh -C .../cardiolite-ecg build` 出 0 error，上板背光亮、空页面显示、触摸可点。

⚠ **S0 开工前必须先确认**：`sdkconfig.defaults` 里该改的两处都改了没有。S0 之后改配置的代价是 40~50 min 全量重编。

随后按 §7 的闸门顺序推进：S1 → S2 → S3 →（阶段 B 门）→ S4 → S5 → S6 → S7 → S8 → S9 → S10。

---

## 13. 命名约定

### 13.1 项目名称

统一使用英文代号 **CardioLite**，中文名称「基于 ESP32-S3 的心电波形显示终端」。含义：Cardio（心脏相关）+ Lite（轻量终端，不是完整监护仪）。**不把项目限定为「医疗设备」**——当前是显示终端 POC，不涉及诊断资质。

若作为正式产品发布，另行检查商标与医疗器械注册路径。

### 13.2 工程与固件命名

| 用途 | 名称 |
|---|---|
| 项目总称 | CardioLite |
| 工作目录 | `C:\work2026\CardioLite` |
| 正式 UI 工程 | `cardiolite-ecg` |
| 固件 project 名 | `cardiolite_terminal` |
| 硬件验证工程 | `lcd-bringup`（project 名 `cardiolite_lcd_bringup`） |
| 共享 BSP 组件 | `bsp_ws_lcd5` |
| 构建入口 | `tools/idf.sh` |

**目录 ↔ project 名对照（§5.2 结构）：**

| 目录 | `project()` 名 |
|---|---|
| `cardiolite-ecg\` | `cardiolite_terminal` |
| `lcd-bringup\` | `cardiolite_lcd_bringup` |
| `bsp_ws_lcd5\` | —（组件，无独立固件） |

### 13.3 文档与数据命名

按「项目名 + 内容 + 日期」命名，例如 `CardioLite_ECG显示终端UI_实施计划_v1.0_20261023.md`。

需求文档保留中文全名 + 版本号 + 日期（`ECG显示终端_嵌入式UI_需求描述_v1.2_2026-10-22.md`），不改成英文——它是与人沟通的文档，不是工程资产。

**版本号规则**：

- 需求变更 → 需求文档升小版本（v1.2 → v1.3），保留旧版。
- 实施计划变更 → 本文档升版本，§0 记录变更范围与拍板原话。
- **不就地改历史段落**，新增「XXXX-XX-XX 就地增补」小节登记。

### 13.4 项目短描述

> CardioLite is an ESP32-S3 based ECG waveform display terminal. It renders recorded ECG data from an SD card onto an 800×480 RGB panel in a standard ECG-paper layout (decoupled horizontal paper-density and vertical ±5 mV full-scale calibration), with freeze / clear / gain / paper-speed controls, on-device heart-rate estimation, and dual light/dark color schemes. The platform is designed so that the SD replay front end can later be replaced by a real-time acquisition link without touching the rendering pipeline.
