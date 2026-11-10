# NOW.md — ecg_rs485_device 交接锚点

> **更新：2026-11-09 17:40。HEAD＝`8a0022b`（待本次提交后更新），工作区干净。**
> 新会话读完本文件即可接上；铁律见同目录 `MEMORY.md`；立项/拍板过程见 `docs/T1xx`。

---

## 1 一句话现状

**UT-2211 接 +5V 后链路全清**（上行坏帧 2.3%→0.000%，下行 50%→100%）。
**HRV 6 心率 × 3 次测完：60/150/180/210 PASS（σ=0.21~0.32，贴近量化极限 0.289），
90/120 FAIL（σ=2.72/5.93）**。FAIL 机制已定位为「单拍下降支误检」（见 `docs/T221`），
**下一步：加原始域局部极大验证 → 重测 V1~V4**。

---

## 2 本周增量（11-06，T212~T219）

| 任务 | 结论 | 落点 |
|---|---|---|
| **T212** | 清屏改为**纯视觉**（方案 B）：不 `restart`、不归零、不改 `play`。§8.4 已订正 | `docs/T212_*` |
| T212b | 加 `ui-clear`，清屏可无人值守回归（只覆盖动作层） | `cli_console.c` |
| **T213** | `485-stat` 的 STATUS 行加**帧龄** + 陈旧标注（`sta_ms`） | `app_state.c` |
| **T214** | 加 `485-perf <secs>`：窗口帧率/丢帧/帧长/内存，分母用实测时长 | `cli_console.c` |
| **T215** | 评估定版：R峰记录成本、CH2 真实通道、LOD 影响（含 DRDY 周期订正） | `docs/T215_*` |
| **T216** | 加 `rpeak on\|off`（本地，只停绘制）＋ `mem`（RAM/PSRAM 实测） | `cli_console.c` |
| **T217** | 加 `rpeak rec`：**固定 300 s** 自动落盘 QRS 峰序列 | `cli_console.c` |
| **T218** | HRV 测试规范 v0.2 + `hrv_analyze.py`（定位精度分析） | `docs/T218_*` |
| T219 | `rec` 完成提示 + PROGRESS 抑制 + 时基判据改鲁棒 | 同上 |

> T198~T211（RS485 通道改造）已全部结案，文档在 `docs/T198~T206`。
> **T213/T214/T216/T217/T219 不单独立文档**：改动记录在 commit message 与当日日报里，
> 判据与用法已进本文件与 `MEMORY.md`。

---

## 3 待办（按优先级）

> **明天第一件事：讨论怎么优化**（猛哥 18:13 定）。核心已明确——三类失效、三条判据，
> 细节全在 `docs/T221` §3.4 与 §6.0，**先读文档再动手**。

1. **🔴 修法方案对齐**（先讨论，别直接改码）：T221 §6.0 的三条判据
   C1 极性（自适应，不能写死）/ C2 幅度 ≥30% 平均 R（前 5 拍冷启动）/ C3 落点纠正。
   ⚠ C3 单独不够——60 bpm 的负向假峰那里没有正向极大，搜索会失败。
2. **🔴 V4（改码前）**：`rpeak wide on` + 120 bpm 记一次，确认 ±27 的真实漂移形态。
3. **🔴 V1**：改完后测 90 bpm 3 次，σ ≤ 0.5 **且峰数仍为 270**（峰数变少 = 纠正逻辑丢了拍）。
4. **🔴 V5**：60 bpm 连测 6 次（偶发率约 1/3，需多次才抓得到），峰数须恒为 180。
5. **🟡 V3 回归**：四个 PASS 心率各 1 次，不劣化（≤0.35）。
6. **🟡 查 `is_twave` 的 spacing<360 窗**：60 bpm 的 P 波在 R 后约 900 ms，完全在窗之外。
7. **🟡 180 bpm 补 1 次**（第 15 轮 dump 失败，已有 2 次一致）。
8. **🟡 真机态其余性能项**：显示流畅性、端到端延迟、断链恢复、30 min 长稳。
9. **🟡 CH2 / LOD（T215 P3~P6）**：顺延。
10. **⏸ 记忆入库**：`.gitignore:32` 是 `**/.workbuddy/` ⇒ 无异地副本。
    修法：改 `/.workbuddy/*` + `!/.workbuddy/memory/`（取反必须这样写）。

### 3.1 测过但**不要**再做的事

- 🛑 调 `ECG_RP_REFRACTORY`：90/120 的漂移点间距 342 样本（684 ms），远大于 160 ms
  不应期，调它无效，只会误杀 210 bpm 的真峰（RR=143 样本）。
- 🛑 "不是局部极大就丢弃"：会造成真漏检（270 → 约 246）。
- 🛑 用"整数 RR / 高心率退化 / 积分窗占比"解释 90/120：三条都已被 18 轮数据推翻。
- 🛑 把 60 bpm 的问题说成"漏检"或"只多检一个"：实测是**多检 2 + 漏检 1 = 净 +1**。

## 4 工具配方

**构建 / 烧录**
```bash
# ESP32（⚠ 先关 SerialDebug 等占用 COM35 的工具，否则 PermissionError 13）
cd /c/work2026/VsPro/ecg_rs485_device/esp32
./485tools/idf.sh -C .../esp32/cardiolite-ecg build
./485tools/idf.sh -C .../esp32/cardiolite-ecg -p COM35 app-flash
# 🛑 flash.sh 硬编码旧路径 C:/work2026/CardioLite，别用

# STM32（EIDE，产物在 <根>/build）；IAR=C:\iar\ewarm-9.70.2
python stm32/tools/_fw_build.py
```

**进入真机实时态（每次重启/烧录后都要走一遍）**
```
485-start            ← 只开 ESP32 侧 UART1，**不会**让 STM32 出数据
485-send start       ← 发 0x01 才进 RUN（下行 ~50% 丢包，失败重发，1~2 次即通）
485-stat / 485-perf  ← 看 frames 在涨 = 真在出数据
```

**ESP32 CLI 要点**
```
log info                     ★靠日志判据的验收必须先开（上电压到 ERROR）
485-perf [secs]              窗口性能：帧率/丢帧/帧长/内存
rpeak on|off                 打点显示（只停绘制，BPM 照算）
rpeak rec / rec off          记录 QRS 峰序列，**secs 可选 30~600，默认 300**
                              → /sdcard/rpeak_hrv.txt（⚠ 每次覆盖，务必立刻回传）
ui-clear                     清屏（与屏上键同一段）
mem                          内部 RAM / PSRAM
result-dump rpeak_hrv.txt    回传峰序列
```
🛑 `play` 在 RS485 源下是**静默空操作**（只置 `s_change`，485 生产者不消费）——先 `485-stop`。
🛑 `485-send` 未 `485-start` 时原地返回（`Δtx=0`，不是失败）。

**协议（速查）**
帧：`AA 55 | TYPE | LEN(2,LE) | CMD | ARG | CRC8`（下行 TYPE=0x80，总长 `6+LEN`）
```
START AA 55 80 01 00 01 5D   STOP AA 55 80 01 00 02 54   SNAP AA 55 80 01 00 10 2A
MODE  ...02 00 04 (00 EF/01 E8)   NOTCH ...05 (00 FA/01 FD)   CH2 ...06 (00 C5/01 C2)
```
门控：`0x10/0x06/0x11` **仅 STOP 态**；`0x04/0x05/0x06` 要求 `argLen==2`，否则**静默丢弃**。
上行显示帧：`AA 55 01 | LEN | SEQ | STA | 25×int16 | CRC8`；**59 B 单通道 / 109 B 双通道**。
判双通道生效**只看帧长 109**，屏上画布切了不算。STA：bit0 mode/bit1 notch/bit2 ch/bit3 raw853/bit4 LOD。

**探针（ST-Link）**：必须 `-c mode=hotplug`；每次 attach 都会 halt 内核制造丢字节 ⇒ **测下行时禁碰 SWD**。

**HRV 分析 / 采集**
```bash
py 485tools/hrv_analyze.py --port COM35 --dump rpeak_hrv.txt --true-bpm 180   # 或 210
py 485tools/hrv_analyze.py --selftest                                          # 自检
py 485tools/hrv_sweep.py --bpms 60,90,120,150,180,210 --repeat 3 --secs 180    # 扫频
```
🛑 `rpeak_hrv.txt` 是**固定文件名、每次覆盖**。11-06 已因此丢掉 180 bpm 原始数据。
`hrv_sweep.py` 每轮回传后立刻另存 `hrv_data/rpeak_hrv_{bpm}bpm_{n}.txt`，手工跑必须自己另存。

---

## 5 记忆分工

| 位置 | 装什么 |
|---|---|
| **本目录** | RS485 / ESP32 / STM32 通道 / 上位机协议 / HRV 测量（本项目本职） |
| `ads1293_demo/.workbuddy/memory/` | ADS1293 **滤波链**（notch / 带通 / dc_block / µV 定标） |

⚠ **历史遗留未迁移**：T198~T211 的细节仍在 `ads1293_demo` 的 `MEMORY.md` §10/§13
与 `2026-11-02`~`2026-11-05` 四份日报里。

---

## 6 开场白（新会话第一句可直接用）

> 工作目录＝`C:\work2026\VsPro\ecg_rs485_device`。先读 `.workbuddy/memory/NOW.md`
> 和 `MEMORY.md`，再读 `docs/T218_*` 的 §4/§7。然后从 NOW.md §3 第 1 项
> （峰序列加峰处邻域值）开始。
