# NOW.md — ecg_rs485_device 交接锚点

> **更新：2026-11-12 16:45。** 新会话读完本文件即可接上；铁律见同目录 `MEMORY.md`；结论见 `docs/T224~T239`。

---

## 1 一句话现状（2026-11-12）

**LOD 已收口；`T239` 步1（ctx 重构）A/B 双向通过；步2（CH2 真实数据上传）已落码＋上板，主判据全过，只剩「`test ch2 off` 59 B 回归」与「链路坏帧 1.09%」两件待收。下一步＝步3 看双画布波形。**

| 11-12 净增量 | 结论 |
|---|---|
| `T236` 回归 4 组 | A 5/6、**B 9/9**、C1/C3 过、**D1 30 min peaks 零增量 / crc 0** |
| 🛑 误判纠正 | "ch2 后流拉不回来"不存在 —— 真因＝没 `485-start` ＋ ESP32 会话间重启 |
| `T237`/`T238` | 方案 B 获批准；8 条 P0/P1 待处置 |
| **`T239` 处置（已拍板）** | 驳回 P0-3、升级 P1-4、补 N1~N3，4 项拍板见下 |
| **`T242` ESP32 显示** | 删画布2 `"no data"`；`s_dual` 初值 **true**；已烧录 |
| **`T239` 步2 已上板（11-12 15:55）** | `s_ctx2`+`out2` 独立链、`ch2_out@18`、`dtcycles→uint32`、`_bch2=1`；**109 B 帧 192/192 帧 CH1≠CH2**（真实两路）；`chstat` ⇒ `desync=0 dtmax=325us over=0`；帧率 **19.97 fps**；⚠ 链路 `crc_err` **1.09%**（拔掉 485→USB 后照旧 ⇒ 不是它，量级同「缺 +5V」签名） |
| **`T240` 步1（11-12）** | `g_dc/f64`+5 static → `ecg_restore_ctx s_ctx1`(976 B)；**CH1 逐位不变**（板端＋主机 40 万样本 `mismatch=0`）；SRAM −16 B / Flash +249 B |

**4 项拍板**
1. **默认双通道**（`_bch2` 初值 → `1u`）＋ `ch2 off` 作回退 ⇒ **驳回 T238 P0-3**
2. 步 1 逐位回归＝**同固件 A/B 双跑 + `memcmp`**（`mismatch==0`、`total≥100000`）
3. **853 模式一起做 CH2**（入队即算 LSB 存 `ch2_out`，打包不分支）
4. 看图用 **ESP32 双画布**（`ecg_pipeline.c:740` 已就绪，零改动）

**T239 三处硬约束**
- 🛑 `dtcycles` 是 **uint16**，819 µs 封顶，装不下 1172 µs 周期 ⇒ **升 uint32（零成本，吃 @6-7 pad）**；口径仍 CH1-only
- 🛑 CH2 **不做 24-bit 符号扩展**（照抄 `ads1293.c:604-607` 的故意注释口径）
- 🛑 `ch2_out` 落 `@18` 吃 pad ⇒ `ecg_filter_t` 仍 **32 B**

**三步**：~~步1 ctx 重构 + A/B 验证~~ ✅ → ~~步2 CH2 链 + 默认双通道~~ ✅（余 2 项，见下） → **步3 阶段①/②看波形**。
步2 判据 **6/7 过**（`T241`）：109 B／59.4 B 双向验过、CH1≠CH2 192/192、`desync=0`、`dtmax=325 µs`；
**唯一未过＝`crc_err`**：已用 **UART5→TTL 直连（COM9）** 定死归因 —— RS485 段 1.9~2.0%、
TTL 直连 **0.17~0.25%**（≈8 倍差，且不再丢字节）⇒ **RS485 段是主因**。
残余：ST-Link 地环已排除（拔掉 0.22% vs 0.25%）。
★crc_err 已**归零并定档**：ESP32 120Ω **保持 OFF** ＋ **两侧同步 57600**（`comm_port.c` / `app_config.h` 均已改并烧录）⇒ 3600 帧/39 万字节零错；整链 20.17 fps、0 丢。
**押后**：C-3 门控（已拍"暂不做"）、`0x08` 电流档扫描。

**🛑 环境硬事实（11-12 实测，别再找错端口）**
- 本板 **ASCII 控制台不在 PC 侧**：`comm_port.h:49 ECG_COMM_PORT=UART5` ⇒ `printf_msg` 全走
  UART5→RS485→**ESP32**。COM3=ST-Link VCP（无字节）／COM33/34=J-Link／**COM35=ESP32 `cardiolite>`**；
  STM32 原生 USB 口**没插**。⇒ 要读 STM32 状态只能经 ESP32（`485-stat` 的 `raw=0x..`／STA 保留位）。
- 🛑 **`stop`→改 notch/mode→`start` 无效**：`filter_chain_init` 的 `f64_init` 会把 notch 写回"开"、
  mode 写回 diag（fail-safe）⇒ **必须先 START 再改设置**。
- 🛑 **`485-send` 每发一条会打坏上行流 1~3 个 `crc_err`** ⇒ 命令偶尔静默丢，重发即中。
- ✅ **COM26 = CH340（485→USB）＝STM32 ASCII 控制台**：`python stm32/tools/_rs485_cli.py "test chstat"`。
  RTS 是方向控制；**开串口会复位 ESP32**（DTR/RTS），故 COM35 的 `485-start`/`485-send`/`485-stat` 必须一次会话做完。
- 🛑 **`cpustat` 不可用**：`cpu` 前缀先吃 ⇒ 新命令叫 **`chstat`**。新增子命令必先
  `grep 'memcmp(argv\[1\],' vcomcmd.c` 查前缀。
- 🛑 **连发两次 `printf_msg` 第二条必丢**（`comm_send` 在拷贝前判忙）⇒ 回显一律一行。
- 🛑 **`485-send start` 回显 `ESP_OK` ≠ 真起流**（下行会静默丢）⇒ 起流判据只看 `485-stat` 的 `rx` 在涨。
- 烧录：`STM32_Programmer_CLI.exe -c port=SWD mode=UR reset=HWrst -w <hex> -v -rst`（`ST-LINK_CLI` 会被 SIGTERM 打断）。

<details><summary>11-11 / 11-12 前半（LOD 收口，已完成）</summary>

**LOD 已从"只显示"做到"端到端可用"，`docs/T234` 是权威机制文档。**

| 项 | 结论 |
|---|---|
| LOD-ON 噪声代价 | RMS +25%、50 Hz 1.2 LSB ⇒ **B 门限余量 6.8×，无需重标** |
| 台架首轮 | 8 nA 够；置位 ≤1.6 s、恢复 ≤1.8 s；拔 IN3 只报警不停 QRS |
| C-2 屏上图标 | 亮灰=正常／红+叉=脱落／暗灰=未知 |
| 抖动定性 | **= 人碰导联线**；8 nA 不改、显示侧不加防抖 |
| **C-3 判据** | 已出 `STA.bit5`（宏 `ECG_LOD_GATE_POLICY`：0=IN1\|IN2／1=任意）；拔 IN3 出红叉但**不停测** |
| **协议 v2.4** | `T101` 标题 v2.4（文件名仍 v2.2）；bit4 源订正；新增 bit5 |
| **上位机** | 文案 bug 修 ⇒ 三态「未知/脱落/正常」；`Protocol.cs` 加 `Ch1Invalid=0x20`（未消费） |

**C-3 门控已拍"暂不做"**（备注在 `T234 §4.5`／`T237 §9`）。

</details>

<details><summary>11-10 及以前（QRS fid 落点修复，已收尾）</summary>

**P4-b 完成**：48 条 MIT-BIH 主口径 **Se 99.087 / +P 99.025 / DER 1.889**；缺陷 B 判定不做。

</details>

---

## 3 待办（2026-11-11 15:00 重评，按"目标是否还成立"排序）

> **重评原则**：B 落地后"空载乱打点/启动伪影"已消除 ⇒ 依赖它们的旧待办目的失效。

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
| T-8 | **C：STM32 START 纯开关** | B 已挡掉启动伪峰（22 峰多在 0~4.5 s）⇒ C 只剩"语义更顺" |
| T-9 | **A-resume**（suspend/resume + gap） | START 失败真因是供电，跨段问题未暴露 |
| T-10 | **0x12 自动复位落点** | 手动 `rpeak reset` 已够 |

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
