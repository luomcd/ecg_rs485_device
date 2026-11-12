# NOW.md — ecg_rs485_device 交接锚点

> **更新：2026-11-12 12:40。** 新会话读完本文件即可接上；铁律见同目录 `MEMORY.md`；结论见 `docs/T224~T239`。

---

## 1 一句话现状（2026-11-12）

**LOD 线已收口；CH2 上传方案已拍板，`T239` 步1（滤波入口上下文重构）已完成并双向 A/B 验收通过。下一步＝步2 接 CH2。**

| 11-12 净增量 | 结论 |
|---|---|
| `T236` 回归 4 组 | A 5/6（A1 因日志级别抓不到）、**B 9/9**、C1/C3 过、**D1 30 min peaks 零增量 / 20.0 fps / crc 0** |
| 🛑 误判纠正 | "ch2 后流拉不回来"**不存在** —— 真因＝会话开头没 `485-start` ＋ ESP32 会话间重启过（`uptime≈125 s`） |
| `T237` 方案 / `T238` 评审 | 评审批准方案 B，**暂不批准直接落码**（8 条 P0/P1） |
| **`T239` 处置（已拍板）** | 驳回 P0-3、升级 P1-4、补 N1~N3，4 项拍板见下 |
| **`T240` 步1 已完成（11-12）** | `g_dc/f64`+5 static → `ecg_restore_ctx s_ctx1`(976 B)；**CH1 逐位不变**（板端 A/B `mismatch=0` ＋ 主机 40 万样本 `mismatch=0`）；SRAM **−16 B** / Flash **+249 B**；固件已回默认档 |

**4 项拍板**
1. **默认双通道**（`_bch2` 初值 → `1u`）＋ `ch2 off` 作回退 ⇒ **驳回 T238 P0-3**
2. 步 1 逐位回归＝**同固件 A/B 双跑 + `memcmp` double 位模式**（`mismatch==0` 且 `total≥100000`）
3. **853 模式一起做 CH2**（入队即算 LSB 存 `ch2_out`，打包不分支）
4. 看图用 **ESP32 双画布**（`ecg_pipeline.c:740` 已就绪，零改动）

**T239 三处硬约束**
- 🛑 `dtcycles` 是 **uint16**，819 µs 封顶，装不下 1172 µs 周期 ⇒ **升 uint32（零成本，吃 @6-7 pad）**；口径仍 CH1-only
- 🛑 CH2 **不做 24-bit 符号扩展**（照抄 `ads1293.c:604-607` 的故意注释口径）
- 🛑 `ch2_out` 落 `@18` 吃 pad ⇒ `ecg_filter_t` 仍 **32 B**

**三步**：~~步1 ctx 重构 + A/B 验证~~ ✅ → **步2 CH2 链 + 默认双通道** → 步3 阶段①/②看波形。
⚠ 步2 开工前先跑一次 A/B（CH1 必须仍 `mismatch=0`）当起点基线。
**押后**：C-3 门控（已拍"暂不做"）、`0x08` 电流档扫描。

**🛑 环境硬事实（11-12 实测，别再找错端口）**
- 本板 **ASCII 控制台不在 PC 侧**：`comm_port.h:49 ECG_COMM_PORT=UART5` ⇒ `printf_msg` 全走
  UART5→RS485→**ESP32**。COM3=ST-Link VCP（无字节）／COM33/34=J-Link／**COM35=ESP32 `cardiolite>`**；
  STM32 原生 USB 口**没插**。⇒ 要读 STM32 状态只能经 ESP32（`485-stat` 的 `raw=0x..`／STA 保留位）。
- 🛑 **`stop`→改 notch/mode→`start` 无效**：`filter_chain_init` 的 `f64_init` 会把 notch 写回"开"、
  mode 写回 diag（fail-safe）⇒ **必须先 START 再改设置**。
- 🛑 **`485-send` 每发一条会打坏上行流 1~3 个 `crc_err`** ⇒ 命令偶尔静默丢，重发即中。
- 烧录用 `STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe -c port=SWD mode=UR reset=HWrst -w <hex> -v -rst`
  （`ST-LINK_CLI.exe` 在本环境会被 SIGTERM 打断）。

<details><summary>11-11 / 11-12 前半（LOD 收口，已完成）</summary>

**LOD 已从"只显示"做到"端到端可用"，`docs/T234` 是权威机制文档。**

| 项 | 结论 |
|---|---|
| LOD-ON 噪声代价 | RMS +25%、峰峰 +8%、50 Hz 1.2 LSB、饱和 0 ⇒ **B 门限余量 6.8×/积分 46×，无需重标** |
| 台架首轮 | 8 nA（`0x08=0x01`）足够；置位 ≤1.6 s、恢复 ≤1.8 s；拔 IN3 只报警不停 QRS |
| C-2 屏上图标 | 亮灰=正常／红+叉=脱落／暗灰=未知（`has_sta=false`）；叉 14×14 线宽 2 |
| 抖动定性 | **= 人碰导联线**，非固件/ADC 暂态。8 nA 档**不改**；显示侧**拍板不加防抖** |
| **C-3 判据** | STM32 已出 `STA.bit5 = CH1_INVALID`（宏 `ECG_LOD_GATE_POLICY`：0=仅 IN1\|IN2 默认／1=任意 LOD）；拔 IN3 出红叉但**不停测** |
| **协议 v2.4** | `T101` 标题改 v2.4（文件名仍 v2.2）；bit4 数据源订正为 `s_lod_stable & ECG_LOD_MASK`；新增 bit5。镜像 `docs/protocol_ref/` |
| **上位机** | 修文案 bug ⇒ 三态「未知/脱落/正常」；`Protocol.cs` 加 `Ch1Invalid=0x20`（**未消费**） |

**C-3 门控（ESP32 侧抑制打点）已拍"暂不做"**，只备注在 `T234 §4.5` 与 `T237 §9`。

</details>

<details><summary>11-10 及以前（QRS fid 落点修复，已收尾）</summary>

**P4-b 完成**：48 条 MIT-BIH，主口径＝完整数据，**Se 99.087 / +P 99.025 / DER 1.889**，`fid` 零副作用；
**可疑现象（原缺陷 B）判定不做**（0.0055 次/min，加门代价 133 倍）。

</details>

---

## 3 待办（2026-11-11 15:00 重评，按"目标是否还成立"排序）

> **重评原则**：B 落地后，"空载乱打点"与"启动伪影"都已消除 ⇒ 依赖它们的旧待办**目的自动失效**。
> 下表每项都标了"为什么还值得做"。

### P0 · ✅ 已完成（2026-11-11 16:02）——B 闭环

| # | 项 | 结果 |
|---|---|---|
| T-1 | 起跳 | ✅ ≤2.8 s，无丢拍 |
| T-2 | 停打点 | ✅ ≤2 s，52 s 零新增 |
| T-3 | 空载 30 min | ✅ peaks 恒 0 / 20.00 fps / drop=0 / crc=0 / PSRAM 无增长 |
| T-4 | 低幅 | ✅ 0.5 mV（4.84×）、0.3 mV（1.69×） |
| T-5 | 削顶 | ⚠ 未削顶但余量仅 955 µV；**削顶行为未验**（需 >6.2 mV 信号源） |
| T-15 | BPM 失效 | ✅ 4 s 超时（新落码，30 min 内始终 `--`） |

### P1 · 一句话或一次校对

| # | 项 | 说明 |
|---|---|---|
| **T-6** | **主口径拍板**：完整数据（DER 1.889）vs padc0（DER 1.85） | 只需一句；决定 T227 主表怎么呈现 |
| **T-7** | **T227 / T228 / T229 交叉校对** | 三份文档都有"两套口径 + B 前后"的内容，需核对无矛盾 |

### P2 · 设计级，**收益已被 B 吃掉大半**，可押后

| # | 项 | 为什么降级 |
|---|---|---|
| T-8 | **C：STM32 START 纯开关** | 原目标是"去掉启动伪影 + 首帧即实时值"。**B 已把启动伪峰挡掉**（48 条去掉的 22 峰多在 0~4.5 s），C 只剩"语义更顺"的价值 |
| T-9 | **A-resume**（suspend/resume + gap 策略） | 原目标是"跨段不污染心搏历史"。**START 失败真因是供电**，跨段问题未实际暴露 |
| T-10 | **0x12 自动复位落点**（`485-send regs` 透传） | 手动 `rpeak reset` 已够；等真要调增益时再做 |

### P3 · backlog / 按需

| # | 项 | 触发条件 |
|---|---|---|
| T-11 | P4-c 真实贴片 / 多导联 | 需要人体 |
| T-12 | P4-d 双接收复核 | 只为给 11-09 那 2 秒定性 |
| T-13 | **P4-g 原始基线（COM27）** | **按需**：做 P5-b 需要噪声 RMS、或做 C 需要首样本偏移基线时才插线 |

## 4 关键判据速查（今天新增/踩过的）

**MIT 评测**
```
py mitbih_compare.py --skip-run            # 剔边口径（与文献/W1 可比）→ summary.csv
py mitbih_compare.py --no-trim --skip-run  # 完整数据（主口径）→ summary_full.csv
py mitbih_eval.py <ann500.txt> <peaks.txt> # 单份，同时打印 [全段] 与 [排除首尾5s]
py mitbih_pad_eval.py --pad-s 5 --pad-tail # 加基线五组对照（orig/pad0/padconst/padc0/padreal）
```
- 🛑 **两套口径不可混比**：完整数据 Se 99.087/+P 99.025/DER 1.889；剔边 99.106/99.094/1.801。
- 🛑 **三道闸门**：`samples==902800` / `bad==0` / （mit-run/play）`dropped==0`，缺一不可。
- 🛑 **判"一致"只认逐峰序列相同**，不认 σ / 峰数 / BPM 相近。

**板端 MIT**
```
mit-set 100,108,208 → mit-run-all → restart   # batch：黑屏全速，对账权威（9.3 s/条）
result-dump batch_mit_result.txt               # 或 py esp_run.py --dump-only --stem ... --out ...
mit-run 108 --speed 8                          # 有波形，但必须看 FILE 行 dropped=0
```
- 🛑 SD 上**只有 .bin、没有 _ann500.txt** ⇒ 板上只出峰序列，评分必须在 PC。
- 🛑 `rpeak rec` 在 FILE 源下**被固件拒绝**（`只在 485 实时源下记录`）。
- 🛑 **SD 挂死只能断电**：批处理异常后 `SD 卡未挂载`，软复位救不回来（11-10 实发一次）。

**fid 相关**
```
rpeak diag     # 板端看 来源/trigger/fid/fid−trigger/lag/窗口覆盖；形态②唯一可观测判据＝窗口覆盖<72
```

---

## 5 记忆分工

| 位置 | 装什么 |
|---|---|
| **本目录** | RS485/ESP32/STM32 通道、上位机协议、QRS 检测与 HRV 测量（本项目本职） |
| `ads1293_demo/.workbuddy/memory/` | ADS1293 滤波链（notch/带通/dc_block/µV 定标） |

⚠ **记忆已入库**（今天改的 `.gitignore`：`/.workbuddy/*` + `!/.workbuddy/memory/`，取反不能写成整目录排除）。

---

## 6 开场白（新会话第一句可直接用）

> 工作目录＝`C:\work2026\VsPro\ecg_rs485_device`。先读 `.workbuddy/memory/NOW.md` 与 `MEMORY.md`，
> 再读 **`docs/T239`（CH2 上传的最终执行底本：评审处置 + 4 项拍板 + 步1/2/3 落点）**、
> `docs/T234`（LOD 机制权威口径）、`docs/T227`（MIT 主口径）。
> 今天从 **T239 步 1** 开始：把 `ecg_restore_process()` 的 5 个 static + `g_dc` + `f64` 打包成
> `ecg_restore_ctx`，**CH1 输出必须逐位不变**，用同固件 A/B 双跑 `memcmp` double 位模式验证（`mismatch==0`）。
> 🛑 步 1 不许顺手改 CH2 —— 一步只做一件事。
