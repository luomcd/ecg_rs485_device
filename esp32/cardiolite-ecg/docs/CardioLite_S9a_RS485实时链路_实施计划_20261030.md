# CardioLite S9a —— RS485 实时链路打通 + PC 波形显示 实施计划

**日期**：2026-10-30　**拍板人**：猛哥（三件事全部同意）
**定位**：真机路线（ADS1293 EVM + STM32L475 → RS485 → ESP32）的第一步。
本轮 S9a = ① 把 485 链路在 CardioLite 固件里打通；② PC 端发送波形数据在屏上显示。
STM32L475 + ADS1293 接入为 **S9b**（另立文档）。

---

## 1. 背景与目标

### 1.1 为什么走真机

当前固件是**回放机**：`ecg_pipeline.c` 的 producer 任务 `fopen` SD 卡上的
`/sdcard/ecg.bin`，逐帧解析后按 2 ms 节拍推入环缓——ESP32 侧没有 AFE 通路，
「真机 Se」在方法上不可得。真机路线把数据源换成真实前端：

```
ADS1293 EVM ──SPI──> STM32L475 ──RS485(板载SP3485)──> ESP32-S3 (CardioLite 同款板)
                                                       └→ 解帧 → 环缓 → 检测器 + UI
```

### 1.2 本轮目标（S9a）

1. ESP32 固件新增 **RS485 数据源**：UART1 收字节流 → 解 T101 v2.2 帧 → 走与 SD 回放
   **完全相同**的 push/检测器/UI 路径；
2. PC 端（USB-RS485 转换器，COM27）发送 `ecg.bin` 帧流，屏上显示连续波形与 BPM；
3. 全部验收判据落在可对账的数字上。

### 1.3 已验证的前置事实（来源：`C:\work2026\ESP-HRV\ADS1293-HRV-Node`）

该工程在同一块 Waveshare ESP32-S3-Touch-LCD-5 上完成了 485 通道测试（链路 OK），
以下硬件事实直接沿用，不重复验证：

| 项 | 值 | 来源 |
|---|---|---|
| 收发器 | SP3485EN（U7），VCC=3V3，RE/DE 短接由 Q1 **硬件自动切换方向**，固件不碰 | 原理图 U7 区 |
| 引脚 | TX=**IO44**（→DI）、RX=**IO43**（←RO），与 UART0 默认（T43/R44）**交叉** | `app_config.h:30-31` |
| 差分出线 | A = P1-10（TX+）、B = P1-9（TX−）；120Ω 终端由 SW2 拨码选通 | 同上 |
| UART | **UART1**（不是 UART0），115200 8N1 | `app_config.h:29` |
| PC 侧 485 串口 | **COM27**（USB-RS485 转换器）；ESP32 USB 侧 = COM35 | `tools/watch485.py` |

**初始化顺序坑**（ESP-HRV 实测）：先 `uart_param_config` + `uart_set_pin`，**再**
`uart_driver_install`——UART1 默认引脚 GPIO17/18 是本板 LCD 数据线，顺序反了中间
一瞬会把 LCD 数据线拉成 UART 输出。

**已实测不可用的验证手段**（别再试）：
- A/B 短接自环：发送期间接收器被 RE 关闭，自己发的自己收不到；
- S3 芯片内 loopback：`LOOPBACK` 置位成功但一个字节都收不回（2026-10-30 实测，
  `rs485_port.h:44-48`）。

---

## 2. 现状衔接点（当前固件）

### 2.1 现有数据流（`cardiolite-ecg/main/ecg/ecg_pipeline.c`）

```
producer_task (Core 1, prio 5)
  fopen(/sdcard/ecg.bin)
  → ecg_frame_read(FILE*)            ← ecg_frame.c，T101 v2.2 帧解析
  → 2 ms 节拍（微秒累计器，:511-524，支持倍速）
  → 逐样本 ecg_ring_push(v1, v2)      ← :465
  → ecg_rpeak_process(v1)             ← :470，只在 push 成功后喂
  → R 峰全局绝对样本号入队 + BPM 200 ms 发布
消费者：ui_waveform pull_samples（S4 起）画双画布 + BPM 浮层
```

### 2.2 帧格式现成（`app_config.h` §四）

```
AA 55 | TYPE(1) | LEN(2, LE) | PAYLOAD(LEN) | CRC8(1)      帧总长 = LEN + 6
LEN=53 单通道：SEQ(2)+STA(1)+25×int16（总长 59）——PC 发波形用这个
LEN=103 双通道：SEQ(2)+STA(1)+50×int16（总长 109），块布局
CRC8：poly 0x07，init 0x00，范围 = TYPE+LEN+PAYLOAD
STA 位：bit0 mode / bit1 notch / bit2 channel / bit3 raw853 / bit4 LOD
```

`ecg.bin`（SD 载荷）与 MIT `_500.bin` 均为该格式。**S9a 不发明新帧**，与 WinApp
录制格式、将来的 STM32 发送格式三方同源，解析器一份。

### 2.3 引脚与 console（无冲突）

- console = USB Serial JTAG（`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`，COM35），
  **不占用 UART0 引脚** ⇒ IO43/44 完全空闲；
- SD 走 SPI2（IO11/12/13），触摸 GPIO4，均与 485 无关。

### 2.4 带宽核算（115200 bps = 11.52 KB/s）

| 流 | 速率 | 占用 |
|---|---|---|
| 单通道实时（59 B × 20 帧/s = 25 样本 × 500 Hz） | 1.18 KB/s | 10.2% ⇒ 余量 9.8× |
| 双通道实时（109 B × 20 帧/s） | 2.18 KB/s | 18.9% ⇒ 余量 5.3× |
| S9b 预估（853.24 Hz raw853 双通道） | ≈3.5 KB/s | 30% ⇒ 仍充足 |

---

## 3. 拍板记录（2026-10-30）

1. **UI 状态机**：`485-start` 后 UI 直接进入与 SD 回放相同的绘制态（波形 + BPM 浮层
   照常工作）；九按钮暂不接管、维持原语义。正式"实时模式"按钮入口放 S9b 之后。
2. **测试数据**：用 `cardiolite-ecg/sdcard/ecg.bin`（真实显示流，0.1526 µV/LSB 域，
   BPM 可对账）；MIT bin 是 raw code 域、显示幅值不对，S9a 不用。
3. **阶段编号**：本轮 = S9a；STM32L475 + ADS1293 接入 = S9b（届时先配 LOD 写进
   STA bit4，衔接 Q1 定案的"S9 前置"）。

---

## 4. 设计（四个新/改部件）

### 4.1 A1 —— 移植传输层 `rs485_port.c/h`（新文件 ×2）

从 ESP-HRV 移植，**裁剪**：

| 保留 | 裁掉（原因） |
|---|---|
| `rs485_init`（含先 param+pin 后 install 的顺序）、`rs485_set_baud/baud` | 行命令解析 / PING-PONG / 空闲提示（那是给"裸串口助手"用的交互面；CardioLite 有自己的 CLI 与 UI） |
| `rs485_send`（S9b 要回发 ACK/命令）、统计 `rs485_stat_t` | `rx_task` **整个删除**——ESP-HRV 用后台任务持续消费；CardioLite 里 485 数据由 producer 任务自己 `uart_read_bytes` 阻塞读，**避免两个消费者抢数据** |
| echo 开关（链路排障还用得上） | `rs485_selftest_internal`（S3 实测不可用）、`rs485_send_pattern`（本轮无示波器场景，S9b 需要时再恢复） |

数字并入本项目 `app_config.h`（项目规矩：引脚/时序数字只出现在这一个文件）：
`APP_485_UART_NUM=1 / APP_485_TX_GPIO=44 / APP_485_RX_GPIO=43 /
APP_485_BAUD_DEFAULT=115200 / APP_485_RX_BUF=4096 / APP_485_TX_BUF=2048`。

**自检方式**：编译 0 err/0 warn；`status` 类命令确认 init 成功。

### 4.2 A2 —— 流式帧解析 `ecg_stream.c/h`（新文件 ×2）

现有 `ecg_frame_read` 是 **FILE\* 接口**，只适用于文件；UART 是字节流，需要状态机版：

```
状态：SYNC0 → SYNC1 → TYPE → LEN_L → LEN_H → PAYLOAD(n) → CRC → 整帧
输入：uint8_t 单字节（feed 式）     输出：整帧回调（复用 ecg_frame_t 结构）
```

- 结构体、LEN 白名单（52/53/103）、STA 位、CRC 全部**复用 `ecg_frame.h` 的宏与
  `ecg_crc8()`**，不复制一份口径（两套口径迟早打架）；
- 状态机天然处理上电垃圾：ROM boot 期间灌进总线的几十毫秒日志会被当作
  "非同步字节"滑过，与 SD 解析的"逐字节再同步"语义一致；
- 统计：帧数、crc_err、resync 字节数、seq 断档数（`seq != prev+1`）。

**自检方式**：PC 侧用既有工具先发 100 帧，`485-stat` 对账（J1 的一部分）。

### 4.3 A3 —— 数据源层改造 `ecg_pipeline.c` + CLI（改 1 + CLI 3 命令）

producer_task 加源类型 `ECG_SRC_FILE | ECG_SRC_RS485`（新 API
`ecg_pipeline_start_rs485(uint32_t baud)`，与现有 `ecg_pipeline_start(path, loop)`
并列，互斥运行）：

| 项 | FILE 源（现状不动） | RS485 源（新） |
|---|---|---|
| 取数据 | `fread` + `ecg_frame_read` | `uart_read_bytes`（50 ms 阻塞）→ `ecg_stream_feed` |
| 节拍 | 2 ms 微秒累计器（支持倍速） | **无节拍**——PC/STM32 按实时速率发，数据自带时序；到帧即推 |
| EOF | 有（EOF 后等 restart） | 无（持续等数据）；`485-stop` 才停 |
| 启动对齐 | 换文件时 `rp_restart()` + ring 清 + file_epoch | 同样：`485-start` 时 `rp_restart()` + ring 清 + epoch 复位 |
| 丢样本语义 | push 失败两边同步跳过（计数器等价） | 完全一致，复用同一段 push/喂检测器代码 |

**push 之后的代码一条不改**：环缓、R 峰绝对样本号换算（:466-489）、BPM 发布、
UI 取样绘制——这是"485 源显示与 SD 回放等价"的结构保证。

CLI 新增三命令（`cli_console.c`，遵循现有命令注册方式）：

```
485-start [baud]   启动 485 源（默认 115200）；已在跑则拒绝并提示
485-stop           停止并回到空闲态
485-stat           打印 rx_bytes / tx_bytes / frames / crc_err / resync /
                   seq_gaps / ring pushed / dropped / bpm
```

**自检方式**：`485-start` → `485-stat` 空闲态计数为 0 且不崩；`485-stop` →
`485-start` 可反复（无资源泄漏，J4）。

### 4.4 A4 —— PC 发送工具 `tools/rs485_stream.py`（新）

```
python tools/rs485_stream.py --port COM27 --file cardiolite-ecg/sdcard/ecg.bin
                             [--speed 1.0] [--loop] [--duration 60] [--stats-interval 5]
```

- 读 `ecg.bin`，按帧（LEN→帧总长 LEN+6）切分，逐帧写入 COM27 @115200；
- **实时节流**：每帧样本数 / 500 Hz = 该帧应耗时（25 样本 = 50 ms），`--speed` 可加速；
- 上电垃圾容忍：发送前可选发一个回车 + 读干输入缓冲（ESP-HRV 的 banner 机制已裁掉，
  总线在 ESP32 侧无输出，PC 只管发）；
- 统计：发送帧数 / 字节数 / 耗时，与 ESP32 `485-stat` 对账用。

### 4.5 UI 侧改动量：**0 行**（2026-10-30 实施时逐行确认）

原计划里留了个"UI 播放态挂接"的尾巴，实测代码后**取消**，依据：

- `ui_waveform.c:579 on_frame()` 的定时器**不读任何播放状态**，每帧无条件
  `pull_samples()`（`:594`，注释写明"冻结时也要取，否则环缓会满"）⇒ **数据一进环缓，
  下一帧就画**；
- 上电 `ecg_pipeline_pause()` 只是"生产者不推样本"，不是"UI 不画"——所以现在屏上
  静止是环缓没数据，不是 UI 被状态挡住；
- 全工程唯一读播放状态的地方是 `main.c:117`（`play==APP_PLAYING && eof()` → 置 ended）
  ⇒ 485 源只要**永不置 `s_eof`** 就不会被误判成"播完"。

⇒ 管线侧只守三条：485 源不置 EOF、起 485 前先停 FILE 源（`s_running` 互斥）、
切换时 reset 环缓+检测器。

### 4.6 A5 —— 端到端联调工具 `tools/s9a_link_check.py`（新，三种跑法）

**必须一个进程同时管两个口**：`open/close COM35` 会因 DTR/RTS 让 ESP32 复位，板子
重来一遍就丢掉"485 模式"这个**运行时状态**；分成两个脚本跑，发完数据再去抓 stat 时
板子已被复位，看到的永远是 `源=FILE, rx=0`。

| 模式 | 命令 | 数据路径 | 用途 |
|---|---|---|---|
| 回放 | 默认 / `--file ecg.bin` | 脚本按 500 Hz 节流从 `--bus`(COM27) 发 | S9a 现状验收（J1~J5） |
| 转发 | `--from-port COM12` | 另一口读 → **原样字节**转发到 `--bus` | S9b 接 STM32（两个转换器） |
| 旁听 | `--from-port COM12 --passive` | 只读不写；PC 转换器与 ESP32 同挂一条总线 | 只有一个转换器时的旁路观察 |

判据口径按模式区分（这一点是实测发现的坑，不是设计选择）：

- **转发模式**：源端计数与板侧**增量**（结束 − 起跑前快照）逐字节可比 ⇒ 帧数差 ≤2、
  字节差 ≤2 帧、`crc_err`/`resync` 两侧皆 0、`dropped=0`；
- **旁听模式**：PC 是**中途加入总线**，加入前板子已经在收 ⇒ 绝对帧数天然比板子少，
  **只能比帧率**（差 ≤5%），并用「板侧增量 < 源端帧数」当丢帧探测器。
  🛑 别把旁听模式的绝对帧数差当链路丢帧 —— 那是加入时刻之差，不是链路问题。

**已做的离线验证**（无硬件也能覆盖的部分）：

| 项 | 方法 | 结果 |
|---|---|---|
| 流式分帧 ≡ 文件切帧 | `ecg.bin` 按随机分块 / 逐字节 / 整份 / 两圈回绕 / 带前导垃圾 五种切法喂 `StreamParser` | 帧数 **1796**、样本 **44,900** 逐位一致；`crc_err=0`；两圈回绕恰好记 1 次 `seq_gaps`（与端到端实测吻合） |
| 转发保字节 | 假串口驱动 `run_from_port`，37 B/次（非 59 约数，强制切断每帧） | 转发字节与原流**逐字节相同**；709 帧全部跨读取边界拼对 |
| 旁听不开 bus | `--passive` 下统计 `serial.Serial` 调用次数 | 只开 1 个口 ✓ |

⚠ 真机 `--from-port` 需要一个第三方数据源（STM32 或第二只 USB-485），本轮硬件不具备，
**端到端未实跑**；离线部分已覆盖到"字节不变 + 分帧正确"，剩下的是接线与波特率一致性。

---

## 5. 实施顺序与构建策略

| 步 | 内容 | 产物 | 自检 |
|---|---|---|---|
| A1 | 移植 rs485_port（裁剪版）+ app_config 数字 | 2 新文件 + app_config.h 追加 | 编译 0 err |
| A2 | ecg_stream 状态机 | 2 新文件 | 随 A3 一起编译 |
| A3 | pipeline 源抽象 + CLI 三命令（UI 零改动，见 §4.5） | 改 ecg_pipeline.c / cli_console.c / app_config.h | 编译 0 err |
| A4 | PC 工具 rs485_stream.py | 1 新文件 | 本机串口冒烟 |
| A5 | 联调工具 s9a_link_check.py（两源三模式，见 §4.6） | 1 新文件 | 离线分帧/转发自检 |
| V  | 上板验收（烧录由猛哥执行） | J1~J4 | §6 |

构建策略：A1 引入 uart 驱动 + A3 改组件依赖，大概率触发一次接近全量的重编译
（改 sdkconfig 级别的冷构建 40~50 min；只改源码为增量）。**A1+A2+A3 一次构建
到位**，A4 并行准备，不单独为 A1/A2 各烧一次。

---

## 6. 验收判据（全部可对账）

| # | 判据 | 通过标准 |
|---|---|---|
| J1 | 链路收发对账 | PC 发 60 s `ecg.bin`（1x，≈1200 帧 ≈70.9 KB）；ESP32 `485-stat`：`rx_bytes` 与 PC 发送字节数差 ≤1 帧长（59 B），`frames` 对账一致 |
| J2 | 数据完整性 | `crc_err=0`、`seq_gaps=0`、ring `dropped=0` |
| J3 | 显示 | 屏上波形连续滚动无断流；BPM 浮层过稳定窗（6.5 s）后显示与数据心率一致（±1 bpm） |
| J4 | 等价性 | 同一份 `ecg.bin`：SD 回放 vs 485 实时，BPM 中位数一致（±1 bpm）；两者峰数同量级 |
| J5 | 生命周期 | `485-stop → 485-start` 反复 ≥3 次无泄漏（stat 归零重计、UI 正常、无重启） |

产出物：`test_logs/CardioLite_S9a_RS485实时链路测试报告_YYYYMMDD.md`（按惯例
过程/原理/结论三段 + J1~J5 判据表）。

---

## 7. 风险与已知坑（带缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 上电 ROM 往 IO43 打日志灌总线（几十 ms，软件关不掉） | 帧状态机自动再同步；PC 工具发前读干缓冲；对账判据 J2 的 `crc_err=0` 在**发送开始后**计时 |
| R2 | UART 读阻塞 50 ms × 环缓水位：若 PC 节流抖动大，突发的帧可能推不过去 | 485 实时速率仅占带宽 10%，UART 驱动自带 4 KB 缓冲 ≈ 68 帧 ≈ 1.7 s 实时数据，远大于任务唤醒延迟 |
| R3 | 移植时引脚宏抄错（43/44 交叉） | 宏名直接沿用 ESP-HRV 语义（TX=44/RX=43），注释里保留原理图依据；烧录后第一步用 J1 对账，接反了 rx=0 一眼可见 |
| R4 | 2 ms 节拍代码被 485 源误用（过节流导致显示滞后） | 源类型分支在 producer 主循环顶部，485 路径不进节拍器；J3 波形连续性可暴露 |
| R5 | UI 播放态挂接点选择不当引发按钮状态机串线 | 拍板①已限死最小方案；若 `app_state` 挂接复杂，先只挂绘制标志位，按钮栏 S9b 再说 |

---

## 8. S9b 展望（不在本轮）

STM32L475 侧：ADS1293 853.24 Hz → 打 T101 v2.2 帧（raw853 位置 1、LOD 位接
Q1 定案"先配 LOD 写进 STA bit4"）→ 485 发送；ESP32 侧新增"显示流按 raw853
降频解释"（`APP_SAMPLE_RATE_HZ` 已有降级处理路径）。届时 PC 端工具继续作为
AFE 模拟器使用（已发的帧与 STM32 同格式，A/B 测试天然成立）。

联调工具已就位（§4.6），接 STM32 时两条路任选：

- **两个转换器**（STM32 的 USB-485 → COM12，ESP32 的 USB-485 → COM27）：
  `python tools/s9a_link_check.py --from-port COM12`
- **一个转换器**（STM32 485 直连 ESP32 485，PC 转换器并在同一对 A/B 上）：
  `python tools/s9a_link_check.py --from-port COM27 --passive`
  ⚠ 此模式判据只看帧率，不比绝对帧数（原因见 §4.6）。
