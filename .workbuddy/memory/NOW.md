# NOW.md — ecg_rs485_device 交接锚点

> **更新：2026-11-10 19:00。HEAD＝`8c39e79`（本次提交后前移），工作区干净。**
> 新会话读完本文件即可接上；铁律见同目录 `MEMORY.md`；结论见 `docs/T224~T228`。

---

## 1 一句话现状

**「QRS fid 落点修复」这条技术线今天收尾了**（T224 定位 → T225 评审 → T226 落码+上板 → T227 MIT 全量 → T228 操作手册）。
**P4-b 完成**：48 条 MIT-BIH，主口径＝完整数据，**Se 99.087 / +P 99.025 / DER 1.889**，`fid` 零副作用；
**可疑现象（原缺陷 B）判定不做**（0.0055 次/min，加门代价 133 倍）。
**明天第一件事：P4-f LOD 门控方案对齐** —— 设备无输入时仍 4.6 拍/秒乱打点，而 LOD 位只用于显示、零处参与门控。

---

## 2 今日净增量（11-10）

| 项 | 结论 | 落点 |
|---|---|---|
| **T224** | 真因＝回溯路径 `locate_rpeak` 覆盖不足；两种失效形态（`found=false` 误差 9 / 部分覆盖静默选错误差 27） | `docs/T224_*` |
| **T225** | 第三方评审：条件批准 P3A，只落 fid、不并入缺陷 B | `docs/T225_*` |
| **T226** | P3A 落码 + 上板：90 bpm **2.50→0.35**、120 bpm **0.22**；`rpeak diag` 上线 | `docs/T226_*` |
| **T227 v0.7** | MIT 48 条：主口径完整数据、首秒根因三层、加基线五组实测、P4-e/P4-f 待办 | `docs/T227_*` |
| **T228** | MIT 上板测试操作手册（batch vs mit-run、三道闸门、8 条故障排查、§9 MIT 覆盖不到的东西） | `docs/T228_*` |
| 固件 | `ecg_rpeak.c/h` fid + 诊断；`ecg_pipeline` + `cli_console` 加 `rpeak diag` | commit `5002ef9` |
| 工具 | `mitbih_compare.py`（48 条，`--no-trim`）、`mitrun.py`（板端 8x 抽样）、`esp_run.py`（`--stem` 通用回传）、`replay_rpeak.py`、`rp_compare.py`、`pc485_cmd.py`、`pc_sweep_replay.py` | `esp32/485tools/` |

**板端三层验证全部通过**：batch 3 条 + `mit-run` 8× 5 条，**逐峰与 PC 完全一致，`dropped` 全 0**
⇒ **xtensa 单精度与 x86 逐位一致，编译器/FPU 这层关闭**。

---

## 3 待办（按优先级，明天从这里开始）

1. **🔴 P4-g AECG100 下电/上电基线对比（明天第一件，先采数据再讨论）**：
   四种状态各记 10 s 原始基线 —— ①AECG 下电 ②上电未输出 ③上电输出 0 mV ④导联悬空。
   **必须记 mean / std / 峰峰值 / 首样本值 / 板上打点数**。
   起因（11-10 晚）：现场反馈"AECG 关闭时屏上零打点"，实测证实乱打点需要
   「**首个台阶小 + 有噪声**」——同一段噪声，DC 偏置 0/100/500/2000/8000 ⇒ 峰数 **46/36/1/1/1**。
2. **🔴 P4-f LOD 门控**（已提前到最前）：无输入 / 导联脱落时**抑制打点、不发布 R 峰、不喂 HRV**。
   - 依据：ADS1293 LOD 位已上报，但 `ecg_pipeline.c` / `ecg_rpeak.c` **零引用**（只 `ecg_frame.c:270,279` 打印）；
   - 三条候选：①LOD 直接抑制（首选，硬信息）②阈值加绝对下限 ③有效信号门（需标定）；
   - ❌ 已排除：`K × noise_peak` 闸门（10-29 实测，`noise_peak` 会被伪峰抬高）。
3. **🔴 P4-e 空载与切换专项**：①AECG100 无输出 ②导联悬空 ③空载→接上信号。
   判据已定死在 T227 §6.2 / T228 §9.3（零打点、BPM 显示 `--`、数拍内起跳）。
4. **🟡 决定主口径**：完整数据（DER 1.889）vs 零台阶预热 `padc0`（DER 1.85，不丢数据）。
   `padc0` 是唯一"不丢数据且三项同时改善"的填法，**待拍板是否升为主口径**。
5. **🟡 决定 T227 §2 主表**已切到完整数据；剔边降为 §2.2 对比口径 —— 已做，如需回退告诉我。
6. **🟡 MEMORY.md 已 12 KB（超 3000 建议上限）**：明天开局先压缩，细节留在本文件与 T227。
7. **⏸ P4-c 真实贴片/多导联、P4-d 双接收复核、P3B 双坐标**：均 backlog，理由见 T227 §6.1/§6.2。

---

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
> 再读 `docs/T227`（§2 主口径 / §2.3 首秒根因 / §6.2 待办）与 `docs/T228`（上板操作）。
> 今天从 **P4-f LOD 门控方案对齐** 开始——先讨论方案，别直接改码。
