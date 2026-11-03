#ifndef ADS1293_ECG_FILTER_COEFFS_H
#define ADS1293_ECG_FILTER_COEFFS_H

/*
 * ADS1293 ECG 滤波链系数表（PC 端 scipy 双精度设计，常量写入）。
 *
 * ============================ 档位选择（编译期） ============================
 * 本文件支持九套系数表，改下面的宏即可切换（或编译时加 -DECG_FILTER_PROFILE=...）：
 *
 *   ★ T58(2026-09-20)：五档 BP 上角 150→250 Hz、LP 4 阶→2 阶（ST40 的 40 Hz 属设计意图，不动）。
 *     新 BP = 1×LP2@250 + 2×HP4@0.05。150 Hz 频响 **54.76% → 75.64%**（判限 ≥70%）。
 *
 *   ECG_PROFILE_ST40       (0) : v3 监护/ST 档   dc_block(0.05) -> notch 50/100/150 -> BP 0.05~40Hz   [7 节]
 *   ECG_PROFILE_DIAG150    (1) : D1 诊断档       dc_block(0.05) -> notch 50         -> BP 0.05~250Hz  [4 节]
 *   ECG_PROFILE_DIAG150_D2 (2) : D2 诊断档       dc_block(0.05) -> notch 50/100     -> BP 0.05~250Hz  [5 节]
 *   ECG_PROFILE_DIAG150_D3 (3) : D3/S3 诊断档
 *                                dc_block(0.05) -> notch 49.90/50.00/50.10(Q20) + notch 100(Q15)
 *                                               -> BP 0.05~250Hz                     [7 节]
 *   ECG_PROFILE_IMP620     (4) : IMP620 输入阻抗测试档（2026-09-17）
 *                                dc_block(0.05) -> notch 49.80/50.00/50.20(Q12) + notch 100(Q15)
 *                                               + notch 150(Q30) -> BP 0.05~250Hz    [8 节]
 *                                → 已上板验收 104.21% / 102.83%（口径 ≤110%）
 *   ECG_PROFILE_IMP620_N2  (5) : ★默认（2026-09-18 10:45）输入阻抗测试档 v2
 *                                dc_block(0.05) -> notch 49.80/50.00/50.20(Q12)
 *                                               -> notch 99.8/100.0/100.2(Q20)
 *                                               -> notch 149.8/150.0/150.2(Q20)
 *                                               -> BP 0.05~250Hz                    [12 节]
 *                                = IMP620 的 100Hz 与 150Hz 由"单节"升为"三节簇"
 *
 *   ★ T84(2026-09-21)：**新增第 9 档 A+B（＝T83 候选 B）并转为投产**，A+ 退为基线对照档。
 *     A+B = A+ 删 HP4 两节（BP 3→1，18→16 节）＋ dc_block 0.05→0.02。
 *     落码方式与理由见下方「落码方式备案」段；依据见 `tools/T83_候选AB裁决_2026-09-21.md` §9。
 *   ECG_PROFILE_DUAL_A_PLUS_B (8): ★投产 A+B 档（T84）
 *                                dc_block(0.02) -> notch 50x3(Q12)+100x3+150x3+200x3+250x3(Q20)
 *                                               -> BP 0.05~250Hz（**仅 LP2@250，HP 由 dc_block 承担**）
 *                                                                                    [16 节]
 *   ECG_PROFILE_DUAL_A_PLUS (7) : A+ 档（T77）＝**基线对照档，不再投产**（判据 a 实测 319 µV ✗）
 *                                dc_block(0.05) -> 同上一串 notch -> BP 0.05~250Hz（LP2 + 2×HP4@0.05）
 *                                                                                    [18 节]
 *
 *   ★ 当前默认 = DUAL_A_PLUS_B（A+B，16 节），为判据 a（低频脉冲后偏移）而做。
 *
 *   **切档规则：由"信号源阻抗"决定，不是"测完一律切回"。**
 *       - 真电极 / 低源阻抗 / 常规监护   -> ECG_PROFILE_DIAG150_D3
 *       - 620kΩ 高源阻抗（本项目当前场景）-> ECG_PROFILE_IMP620 或 IMP620_N2（保持不动）
 *
 *   [!] 620kΩ 接入时**不要**切回 D3：D3 没有 150Hz 陷波，而 620k 下 150Hz 被放大 165x，
 *       且 150Hz 恰落在 BP 上角（|H| 约 -3.02dB，几乎不衰减）
 *       => 实测判决 129.7% 超标（判据 <=110%），屏幕出来就是脏数据。
 *       D3 的"正常监护"前提是低源阻抗（真电极 150Hz 只有约 1.3uV）。
 *
 *   九档 API/结构体完全一致，只有 ECG_NUM_SECTIONS 与系数表不同。
 *
 * [IMP620_N2 = 输入阻抗测试档 v2，为什么（2026-09-18 T31/T32 判决，猛哥拍板 Q20/3节/50簇不动）]
 *   病根：IMP620 里 50Hz 用了**三节簇**，而 100Hz/150Hz 各只有**单节陷波**——同类问题两种强度。
 *   实测（09-18 §1~§7）：屏幕第一号残留就是 150Hz，raw 1306.7µV 只被压 −30.0dB（41.15µV），
 *   因为漂移使它偏离陷波中心 −0.16Hz，而单节 notch(150,Q30) 谷宽只有 5.01Hz。
 *   ① T31 钉死"漂移带到底多宽"：`coeffs.h:40` 旧写的「实测工频摊在 49.4~50.6Hz（宽 1.2Hz）」
 *      **是 FFT 窗的表观主瓣宽，不是工频在漂**——1.2Hz 恰是 1.67s Hann 窗的 −6dB 宽（1.245）
 *      / 3.33s 窗的 −20dB 宽（1.200）；短窗连峰位读数都能散 0.3Hz。
 *      真实漂移 = **0.0981Hz**（8 文件 × 09-16~18 三天 × USB/锂电5V × IN1IN2/RA-GND/620k ×
 *      4/10Hz/无输入；谐波严格成比例 raw 域 2.0000/3.0000/4.0000）
 *      ⇒ 3× 外推 150Hz 带 = **0.2944Hz**（149.8173~150.1116）。
 *      ⚠ 结论不变：±0.2/Q12 的真正收益是**可重复性**（实测同机 12s 两段差 4.45× vs 三陷波 1.71×），
 *        不是"覆盖 1.2Hz"——那句依据作废，本档沿用其结论。
 *   ② T32 定节数与 Q：把残值压到 3µV（=3mVpp 的 0.1%）即足够。
 *      | 配置（Q20）              | 100Hz 带内/残值/域宽     | 150Hz 带内/残值/域宽      | 节 |
 *      | 单节                     | −26.3dB/42.55µV/5.01     | −26.3dB/63.59µV/7.52      | 1  |
 *      | 2 节（间距 0.30）        | −48.9/3.13/7.80          | −55.9/2.09/11.68          | 2  |
 *      | **3 节（间距 0.20）**    | **−74.2/0.17/9.84**      | **−84.7/0.076/14.74**     | 3  |
 *      2 节刚达标但"深覆盖带 0.30Hz = 漂移带 0.294Hz"**零余量**；3 节深覆盖 0.60Hz ⇒ 余量 2 倍。
 *   ③ **多陷波压倒性优于降 Q**（同 ~15Hz 合并带宽）：单节 Q10 = −32.3dB，三节 Q20 = −84.7dB，**差 52dB**
 *      ⇒ 这不是"更宽 vs 更深"的取舍，而是"宽且深"对"宽而浅"。与 50Hz 簇的历史结论同构。
 *      Q20 vs Q30 在判决上等价（残值都 <<1µV），Q20 谷更宽（14.74 vs 9.84Hz）⇒ 抗漂移余量更大。
 *   ④ 代价（T32 §3）：**44-46Hz（QRS 能量区）零触碰**（额外 ≤0.02dB）；
 *      损失集中在 95-105Hz（98/102Hz 再损 0.65dB）与 140-150Hz（147 −9.96 / 148 −15.62 / 149 −27.10dB）。
 *      150Hz 之上本无诊断信息（IEC 诊断级上限 150Hz）。
 *   ⑤ 判决预期：三条工频线在屏幕上的最差残值 **74.48µV → 5.86µV（12.7×）**，
 *      比值 104.50% → **103.16%**。但**真正买到的是鲁棒性**：现状 104.21% 是"漂移恰落在
 *      陷波中心附近"的运气值（带内最差 105µV）；若 150Hz raw 涨 3 倍（09-18 已见 raw 从
 *      300µV 涨到 1306.7µV 的 4.4× 先例），现状会到 **107.1%** 逼近 110% 门限，本档不变。
 *   ⑥ 开销：9 → 13 节。CPU 按 0.8115 pp/节（D3 实测锚点）外推 **7.32% → 10.56%**（+3.25pp，
 *      单样本余量 89.4%）；Flash 常量区 **+240 B**（f32 +80 / f64 +160）；运行态 RAM f32 链 +112 B。
 *      ⚠ 9 节以上全是外推 ⇒ **落码后必须上板用 DWT->CYCCNT 实测**。
 *   ⑦ ⚠ 与 IMP620 同样吃掉 147.5~152.5Hz 诊断带宽（本档更宽，142.7~157.5Hz），
 *      **不能替代 D3 用于正常监护**（真电极场景 150Hz 仅 1.3µV，白吃带宽）。
 *      这正是"新增独立档、不动 IMP620 基线"的原因（猛哥 09-18 定）。
 *
 * [IMP620 = 输入阻抗测试档，为什么（2026-09-17 T24/T26/T27/T28 判决）]
 *   口径由猛哥 2026-09-17 明确：**屏幕总波形 p-p(接 620kΩ) / p-p(直连 3mVpp) ≤ 1.10**，
 *   数据源 = txt 第④列（= 协议上报 = 上位机屏幕，500Hz, 0.1526µV/LSB）。
 *   | 工况（620kΩ∥4.7nF） | 现状 D2 | D3(8节) | **IMP620(9节)** |
 *   | 620k 段 A⑧          | 141.6%  | 127.7%  | **104.6% 达标** |
 *   | 620k 段 B①          | 129.7%  | 128.0%  | **103.4% 达标** |
 *   直连基线 p-p 实测 3040.2µV（= AECG100 3mVpp/4Hz，理论 3000）；门限 3344.3µV。
 *
 *   ① 主因不是 50Hz，是 **150Hz（工频 3 次谐波）**：D1/D2/D3 三档都没有 150Hz 陷波，
 *      而 150Hz 恰好是 BP 的上角（|H(150Hz)| = −3.02dB ≈ 不衰减）。在 620kΩ 接入时它被
 *      放大 **165×**（直连 2.2µV → 620k 356µV）。
 *   ② 620kΩ 各次谐波的放大倍数与 620k∥4.7nF 阻抗逐一对上（同一机理 = 位移电流×|Z(f)|）：
 *      | f | Xc(4.7nF) | Z=620k∥Xc | 归一 | 实测放大 |
 *      | 50  | 677kΩ | 323kΩ | 1.00 | 359× |
 *      | 150 | 226kΩ | 163kΩ | 0.505| 165× |
 *      | 203 | 167kΩ | 131kΩ | 0.406| 113× |
 *   ③ 50Hz 簇不是"不够深"，是"太窄"：单节 notch(50,Q30) 在 ±0.3Hz 只剩 −9.4dB，漏出 ~360µV。
 *      把三个中心从 ±0.1/Q20(D3) 改成 **±0.2/Q12**，谷拼宽后同样两点的衰减从 −22/−32dB 提到 −34/−46dB。
 *      ⚠ **2026-09-18 T31 更正**：本档原写作"覆盖实测漂移带 49.4~50.6Hz（宽 1.2Hz）"——
 *        那个 1.2Hz 经查**是 FFT 窗的表观主瓣宽，不是工频在漂**（详见上方 IMP620_N2 段 ①）。
 *        真实漂移仅 **0.0981Hz**。**但本档结论不变**：它真正的收益是**可重复性**——
 *        同为 620kΩ/同机/只差 12s 的两段，单陷波残余差 4.45×，三陷波只差 1.71×（见 D3 段 ①）。
 *   ④ 残余分解（A⑧，µV rms，列④）证明 IMP620 已到顶：
 *      | 频带 | 45-55 | 55-100 | 100-140 | 148-152 | 152-250 |
 *      | 现状 | 249.9 |  41.6  |  21.1   |  294.1  |  18.5   |
 *      | IMP620|  29.4 |  39.7  |  21.0   |   10.0  |  18.4   |
 *      50/150 被砍 88%/97%，剩下的 55–250Hz 宽带（~79µV）是硬件地板，陷波无权处理。
 *      实测再叠加 Q8 / 4 节 / 5 节 50Hz 簇收益均为 **0.0pp**，故不再加节。
 *   ⑤ 代价：8 节 → 9 节，按 D3 的实测斜率（+1.62pp/2节）估 CPU ≈ 7.3%（余量仍 >92%）。
 *   ⑥ ⚠ 本档会吃掉 147.5–152.5Hz 的诊断带宽（Q30 → −3dB BW 5Hz），
 *      **故不能拿它替代 D3 用于正常监护**（真电极场景 150Hz 只有 1.3µV，白吃带宽）。
 *      这正是"新增独立档"而"不改 D3"的原因。
 *   ⑦ 另一个 620kΩ 独有现象：接入后 **0.05–0.1Hz 有一次 ~600µV 的抛物线基线隆起**
 *      （A⑧ 1s 均值 103→602→−85µV，约 10s 后自己回 0），量级 = 620kΩ × ~1nA 漏电流，
 *      属**接入瞬态/硬件直流特性**，陷波无低频能力，IMP620 对它零作用（实测 689→689µV）。
 *      对策是在硬件侧（等 10~15s 读数，或查明漏电流路径），不在滤波链。
 *
 * [D3 = D2 的工频主峰三陷波版，为什么（2026-09-17 判决）]
 *   数据：LiPc-2026-09-17 系列（换笔记本后抓的 620kΩ∥4.7nF 数据，主峰纯度高、裙边极低）
 *         + 旧 113751 / Li5V 两组作对照。固件同款 DF2T 因果递推，Hann 窗测 45–55Hz rms：
 *   | 工况（620kΩ 接入） | raw 45–55 | D2(现6节) | D3(8节) | D3 的 49.5–50.5 | D3 的 99–101 |
 *   | 新笔电 段① 11.03s  |   8622.0  |   72.6    |   24.8  |      2.1       |     1.4     |
 *   | 新笔电 段⑦ 12.12s  |   8562.3  |  294.4    |   45.4  |      3.6       |     6.2     |
 *   | 旧笔电 (PE) 9.25s  |  19716.7  | 1352.5    |  783.5  |      9.2       |    15.4     |
 *   | 旧笔电 (Li5V)10.0s |  19652.1  | 1500.6    |  993.0  |     11.3       |    11.2     |
 *
 *   ① 三陷波不是为了"更深"，是为了"**可重复**"：同为 620kΩ、同一台机、只差 12 秒的两段，
 *      raw 几乎相同（8621.8 / 8561.4），**D2 的单陷波 Q30 残余却差 4.45 倍**（62.6 → 280.6），
 *      而 D3 只差 1.71 倍（2.1 → 3.6）。单陷波深度对工频落点漂移极敏感（±0.02Hz 即可掉 30dB），
 *      三陷波把 2.5Hz 宽的谷拼成一条平面，把灵敏度换成鲁棒性。
 *   ② 100Hz 陷波 Q 由 30 放宽到 15（BW 3.32 → 6.54Hz）：实测 99–101 残余反而从
 *      D2 的 12.4µV 降到 D3 的 6.2µV（旧数据 30.3 → 15.4），宽谷同样抗漂移。
 *   ③ QRS 保真（60bpm 1mV NSR 重放，见 tools/_t20_qrs_probe.py / T20 报告）：
 *      R-S 只降 16.4µV（−1.68%），模板 max|Δ| 19.0µV，QRS 宽度逐拍不变，振铃 τ≈7ms。
 *   ④ 代价：6 节 → 8 节，M4F 实测 CPU 占用 4.884% → 6.507%（+1.62pp，见 §精度选型）。
 *   ⑤ 4Hz 通带信号零损失：1301.3 → 1301.3µV（D2/D3/f32/f64 四者逐位相同）。
 *   ⑥ f32 与 f64 实测逐位同值 ⇒ 本档 f32 也可用（D2 时代 0.05Hz 极点对 f32 敏感的问题，
 *      被前 4 节陷波"顶"在前面后仍不显著；但低端极点仍在，ST/基线定量分析仍推荐 f64）。
 *
 *   注：真实数据里 S0→S3 的净收益会受**接收主机地环路引入的宽带裙边**压制（旧笔电工况下
 *       只赚 4.7dB）；换电池供电的接收主机后裙边塌 87.7×，D3 收益才完全释放（9.2~16.2dB）。
 *       ⇒ 硬件侧（切断接收主机市电地环）与固件侧必须**同时**做，缺一不可。
 *
 * [D2 = D1 + 100Hz 陷波，为什么（2026-09-10 D1 装板实测 134928）]
 *   D1 装板后 P/T/基线出现"毛虫"。控制变量分析（同一 raw 134928 分别重放三条链）：
 *     TP 段 σ:      v3 11.8 µV -> D1 15.4 µV (+31%)
 *     100Hz 谐波:   v3 ~0 µV   -> D1  9.0 µV  ← 毛虫主源（D1 去掉 100Hz notch 后
 *                                              从 -0.10dB 的通带完整漏入）
 *     40-95Hz 宽带: v3  1.6 µV -> D1  5.2 µV  ← 次要
 *     数学闭合: √(11.8² + 9.0²) = 14.8 ≈ 15.4 ✓
 *   **D2 = 加回 100Hz notch（+1 节）** 后同一 raw 重放：
 *     TP 段 σ:      15.4 -> 12.4 µV（回到 v3 的 11.8 水平，改善 +19%）
 *     100Hz 谐波:   9.0  ->  0.9 µV（-90%）
 *     R-S 保真:     D1 963µV -> D2 952µV（-1.2%，notch 只挖 100Hz 窄谷，不碰 QRS）
 *   结论：D2 = "既要又要"最优解——QRS 保真（R-S ≈ raw 的 96%）+ 毛虫压回 v3 水平。
 *   150Hz 陷波收益仅 1.3µV，不划算，暂不加（如需即 D3 = +1 节）。
 *
 * [D1 诊断档 相对 v3 的收益，2026-09-10 离线验证]
 *   数据：AECG100 60bpm 1mV，190245 与 123316 两份（27/28 拍模板，PR 段基线，µV=0.1122）
 *   | 指标 | raw | v3(0.05-40) | D1(0.05-150) | D1 vs v3 |
 *   | R 峰 | 862 | 656         | 812          |  +23.9%  |
 *   | R-S  | 975 | 772         | 951          |  +23.3%  |  → 恢复到 raw 的 97.5%
 *   | P 峰 | 201 | 199         | 204          |   +2.5%  |
 *   | T 峰 | 202 | 166         | 171          |   +2.8%  |
 *   | ST   |  -2 | -45         | -31          |  +14 µV  |
 *   结论：R-S 压缩的主因是 **40Hz 低通 4 阶磨浅 S 谷**，高端放到 150Hz 后 QRS 幅度
 *         基本还原（-2.5%），P 波不受影响；T/ST 主要取决于低端，D1 改善有限。
 *   D1 代价（已由 D2 解决）：去掉 100/150Hz 陷波 → 强工频环境毛虫明显。
 *   硬件前提：ADS1293 (R1,R2,R3)=(4,5,6) 时 BW=175Hz 已覆盖诊断 150Hz，无需改抽取率。
 *
 * [v3 相对 v2 的升级，2026-09-09]
 *   带通低端 0.5 -> 0.05 Hz（dc_block 默认 fc 同步 0.05Hz）。
 *   原因: AECG100 60bpm 1mV 实测(092214)模板分析发现, 0.5Hz 高通的阶跃尾巴
 *         压在 ST 段与 TP 等电位段: ST +1->-215µV(下压), TP +2->+107µV(抬高不回0),
 *         T 波 -61%。低端改 0.05Hz 后 ST 回 -31µV、TP 拉平、T 恢复 80%
 *         （实验图 exp2_0.5_vs_0.05.png）。通带内 float32 实现误差 ≤0.34dB。
 *
 * [v2 相对 v1 的升级，2026-09-05]
 *   v1(9/3): fs = 1e6/1173;            陷波仅 50Hz;     带通 butter(2,[0.5,40]) = 2 biquad
 *   v2(本版): fs = 1e6/1172 (DRDY 实测); 陷波 50/100/150Hz(Q=30); 带通 butter(4,[0.5,40]) = 4 biquad
 *   原因: 接导联线悬空数据(113257)工频谐波 100/150Hz 极强(59~71dB),
 *         2-biquad 带通低通边在 100Hz 只衰减约 -20dB, 谐波泄漏明显;
 *         v2 用 3 个陷波直接压基波+2/3 次谐波, 带通升到 4 biquad 提边沿滚降。
 *
 * [重要: 因果(MCU) vs 零相位(PC filtfilt) 的深度差异]
 *   filtfilt 等效幅频 = |H(e^jw)|^2, 陷波深度翻倍, 对陷波中心与真实工频峰之间
 *   的微小偏差(本批数据实测峰位 49.97~49.99Hz, 电网或采样时钟漂移)容忍度高;
 *   MCU 只能用因果滤波, 深度只有一半: Q=30 陷波中心偏差 0.02~0.03Hz 时,
 *   50Hz 衰减从设计值 -90dB+ 掉到约 -35~-45dB。
 *   实测(113257 接导联线悬空): 因果链 σ≈57 codes vs 离线 filtfilt σ≈36.6。
 *   工程对策: 真实佩戴时 RLD 共模回路把 50Hz 压到很低, -45dB 残余可容忍;
 *             悬空/脱落场景由 LOD 门控(ecg_gate)输出 0 基线, 不依赖滤波深度。
 *   ★ 2026-09-17 补: D3 三陷波正是针对这条的**根治手段** —— 2.5Hz 宽的谷拼成平面后，
 *     工频落点在 49.95~50.10 内漂移不再改变残余（实测两段仅差 1.71×，而 D2 差 4.45×）。
 *     "中心偏差 0.02Hz 就掉 30dB" 这个问题从"深度问题"变成"无问题"。
 *
 * 采样率按 ADS1293 DRDY 周期 1172 us 固定:
 *     fs = 1e6 / 1172 = 853.2423208191125 Hz
 *
 * 生成命令(PC 端复现, 见 tools/gen_diag_coeffs.py；D3 见 tools/gen_d3_coeffs.py):
 *   from scipy import signal
 *   fs = 1e6 / 1172.0
 *   # --- v3 (ST40) ---
 *   secs = []
 *   for hz in (50.0, 100.0, 150.0):
 *       b, a = signal.iirnotch(hz, 30.0, fs=fs)
 *       secs.append(signal.tf2sos(b, a)[0])
 *   sos_bp = signal.butter(4, [0.05, 40.0], btype="bandpass", fs=fs, output="sos")
 *   # --- D1 (DIAG150) ---
 *   sos = np.vstack([signal.tf2sos(*signal.iirnotch(50.0, 30.0, fs=fs)),
 *                    signal.butter(4, [0.05, 150.0], btype="bandpass", fs=fs, output="sos")])
 *   # --- D2 (DIAG150_D2) = D1 + 100Hz 陷波 ---
 *   sos = np.vstack([signal.tf2sos(*signal.iirnotch(50.0, 30.0, fs=fs)),
 *                    signal.tf2sos(*signal.iirnotch(100.0, 30.0, fs=fs)),
 *                    signal.butter(4, [0.05, 150.0], btype="bandpass", fs=fs, output="sos")])
 *   # --- D3 (DIAG150_D3) = 三陷波主峰 + 宽 100Hz 陷波 + 同 BP（8 节）---
 *   from scipy import signal as sps
 *   def notch(f0, Q, fs=fs):
 *       b, a = sps.iirnotch(f0, Q, fs=fs)
 *       return sps.tf2sos(b, a)[0]
 *   secs = [notch(49.90, 20.0), notch(50.00, 20.0), notch(50.10, 20.0), notch(100.0, 15.0)]
 *   sos  = np.vstack([secs, sps.butter(4, [0.05, 150.0], btype="bandpass", fs=fs, output="sos")])
 *   # --- IMP620 (输入阻抗测试档) = D3 的 50Hz 簇放宽到 ±0.2/Q12 + 补 150Hz 陷波（9 节）---
 *   secs = [notch(49.80, 12.0), notch(50.00, 12.0), notch(50.20, 12.0),
 *           notch(100.0, 15.0), notch(150.0, 30.0)]
 *   sos  = np.vstack([secs, sps.butter(4, [0.05, 150.0], btype="bandpass", fs=fs, output="sos")])
 *   # --- IMP620_N2 (输入阻抗测试档 v2) = IMP620 的 100/150Hz 各由单节升为三节簇（13 节）---
 *   secs = [notch(49.80, 12.0), notch(50.00, 12.0), notch(50.20, 12.0),
 *           notch(99.80, 20.0), notch(100.00, 20.0), notch(100.20, 20.0),
 *           notch(149.80, 20.0), notch(150.00, 20.0), notch(150.20, 20.0)]
 *   sos  = np.vstack([secs, sps.butter(4, [0.05, 150.0], btype="bandpass", fs=fs, output="sos")])
 *   # ⚠ 陷波必须走 tf2sos()（会产生 ±1ULP 的 b0/b2 不对称，与既有表同源）；
 *   #   裸 iirnotch() 的 b0≡b2，与本表在末位不同。tools/gen_f3_coeffs.py 第 1 步即为
 *   #   "用 tf2sos 逐位复现 D3 表" 的自证（8/8 节 f64+f32 全 0 ULP），通过后才生成 IMP620；
 *   #   第 4 步输出 IMP620_N2（13 节）表，第 5 步给 P0 vs N2a 的级联频响对比。
 *
 * ★ T58(2026-09-20)：五档 BP 由 4 节降为 3 节 = [LP2@250] + [HP4@0.05] + [HP4@0.05]。
 *   节号随各档陷波节数右移：原"第 N~N+3 节"现为"第 N 节 = LP2@250；第 N+1~N+2 节 = HP4@0.05"。
 *
 * 级联顺序(ST40):      第 0~2 节 = 50/100/150 Hz 陷波(Q=30); 第 3~6 节 = 0.05~40 Hz BP（4 节，未改）。
 * 级联顺序(DIAG150):   第 0 节 = 50 Hz 陷波(Q=30); 第 1~3 节 = 0.05~250 Hz BP（LP2@250 + HP4×2）。
 * 级联顺序(DIAG150_D2):第 0~1 节 = 50/100 Hz 陷波(Q=30); 第 2~4 节 = 0.05~250 Hz BP。
 * 级联顺序(DIAG150_D3):第 0~2 节 = 49.90/50.00/50.10 Hz 陷波(Q=20);
 *                      第 3 节  = 100 Hz 陷波(Q=15); 第 4~6 节 = 0.05~250 Hz BP。
 * 级联顺序(IMP620):    第 0~2 节 = 49.80/50.00/50.20 Hz 陷波(Q=12);
 *                      第 3 节  = 100 Hz 陷波(Q=15); 第 4 节 = 150 Hz 陷波(Q=30);
 *                      第 5~7 节 = 0.05~250 Hz BP。
 * 级联顺序(IMP620_N2): 第 0~2 节 = 49.80/50.00/50.20 Hz 陷波(Q=12);
 *                      第 3~5 节 = 99.80/100.00/100.20 Hz 陷波(Q=20);
 *                      第 6~8 节 = 149.80/150.00/150.20 Hz 陷波(Q=20);
 *                      第 9~11 节 = 0.05~250 Hz BP。
 */

/* ---- 档位定义 ---- */
#define ECG_PROFILE_ST40       0
#define ECG_PROFILE_DIAG150    1
#define ECG_PROFILE_DIAG150_D2 2
#define ECG_PROFILE_DIAG150_D3 3
#define ECG_PROFILE_IMP620     4
#define ECG_PROFILE_IMP620_N2  5
#define ECG_PROFILE_DUAL_A     6   /* ★ T76(2026-09-21) 双态档：一套参数、两个 notch 状态各过一项 */
#define ECG_PROFILE_DUAL_A_PLUS 7  /* ★ T77(2026-09-21) A+ 档：DUAL_A + 链首 200/250 簇（18 节） */
#define ECG_PROFILE_DUAL_A_PLUS_B 8 /* ★ T84(2026-09-21) A+B 档：A+ 删 HP4 两节、低端 HP 全交给 dc_block@0.02（16 节） */

#ifndef ECG_FILTER_PROFILE
#define ECG_FILTER_PROFILE ECG_PROFILE_DUAL_A_PLUS_B /* ★ 当前档：A+B（16 节）｜切换＝改本行标识符，由 tools/_set_profile.py 负责；切档后必敲 `test notch` 复核 */
#endif

/* ============================================================================
 * ★ T84(2026-09-21) 落码方式备案（「候选 B」怎么落的，以及为什么这么落）
 * ============================================================================
 * B ＝ T83 §1 的候选 B：**A+ 档删掉 HP4 两节（BP 3 节 → 1 节）＋ dc_block 0.05 → 0.02**，
 *      18 节 → **16 节**。依据见 `tools/T83_候选AB裁决_2026-09-21.md` §9（极点预算）。
 *
 * 【① 为什么走「档位宏 + 系数表分支」，不走 -D 编译开关】
 *   coeffs.h 顶部虽写了「或编译时加 -DECG_FILTER_PROFILE=...」，但**本工程不用这条路**：
 *   -D 必须落到 EIDE 的 `eide.yml` → 派生 `builder.params` 的 defineList；改了 yml 而 EIDE
 *   没重新生成 params，构建会**静默沿用旧宏**（见 tools/_set_profile.py 顶部）。源码里的
 *   `#define ECG_FILTER_PROFILE` 这一行才是唯一切换点，没有这个风险。
 *
 * 【② 为什么「新增第 9 档」而不是「原地改 A+ 档」】
 *   A+ 保留为**基线对照档**，一行宏即可切回（`python tools/_set_profile.py --set aplus`）：
 *   - 判据 a 复测若与离线预测（a1 = 37.1 µV）对不上，可同版固件回采 18 节数据做 A/B 对照；
 *   - 若 620 kΩ 建场等待被认定为硬约束，可无痛退到 A（见 T83 §9.2 的 ±6% 刺激源容差注记）；
 *   - 与本工程既有惯例一致（IMP620 → IMP620_N2、DUAL_A → DUAL_A_PLUS 都是**新增**而非原地改）。
 *   ⚠ A+ 档自此**不再投产**：它过不了判据 a（实测 a1 = 319 µV，见 T83 §9 表 A）。
 *
 * 【③ 为什么把 dc_block 的截止频率从 ads1293.c 的硬编码提升为本文件里的按档宏】
 *   这是 B 落码时**顺带修掉的架构隐患**。判据 a 的极点预算法则（T83b 导出）：
 *
 *       a1 ≈ A · T · 2π · Σf ，  Σf ＝ 低端**全部** HP 极点频率之和
 *       ⇒ 预算 Σf ≤ X / (A·T·2π) = 0.1 mV / (3 mV × 0.1 s × 2π) = 0.05306 Hz
 *
 *   其中 Σf 的两半**原来劈在两个文件里**：BP 的 HP 极点写在**本文件**（档位决定），
 *   dc_block 的极点却硬编码在 `Ads1293/ads1293.c`（与档位无关）⇒ **档与 Σf 脱钩**：
 *   只把 ads1293.c 的 0.05f 改成 0.02f，A+ 档（18 节）会**静默**跟着变成 Σf = 0.15069，
 *   变成一个从未分析过的组合。提升为按档宏 `ECG_DC_BLOCK_FC_HZ_F` 后，**一档一行**，
 *   Σf 在同一个文件里可读可查；且所有既有档仍取 0.05f ⇒ **对既有档零行为变化**。
 *
 *   | 档 | Σf (Hz) | 预算占用 | 说明 |
 *   | A+  | 0.18069 | 341% ✗  | 15 陷波 + LP2@250 + 2×HP4@0.05(两极点) + dc@0.05 |
 *   | A+B | 0.02000 |  37.7% ✓| 15 陷波 + LP2@250 + **1 阶 dc@0.02**（HP4 已删） |
 *
 * ⚠ Σf 只决定**脉冲落点 a1**；**620 kΩ 建场/恢复时间**由**最低单极点**决定（τ = 1/(2π·f_min)，
 *   见 T83 §9.3：实测 现状 65.3 s / A 25.4 s / B 63.7 s）。两者是不同的指标，别混用。
 * ============================================================================ */

/* ============================================================================
 * 链结构（2026-09-20 显式化；T46 §2 ①「工频滤波器组旁路」的前置）
 *
 * 各档的系数表布局**统一**为：
 *
 *      [ 陷波节 × ECG_NUM_NOTCH_SECTIONS ]  ->  [ BP 节 × ECG_NUM_BP_SECTIONS ]
 *
 *   即：**陷波节恒在链首，带通节恒为链尾 ECG_NUM_BP_SECTIONS 节**。
 *      BP 的“标准”构成为 1×LP2@250（上角，T58）+ 2×HP4@0.05（下角）＝ 3 节；
 *      IMP620/N2 与 ST40 的 BP 是 4 节（T66 回退 / 设计如此）；**A+B 的 BP 只有 1 节**
 *      （LP2@250；下角 HP 整段交给 dc_block，见「T84 落码方式备案 ②」）。
 *
 * ⇒ 运行期「工频滤波器组旁路」（标准强制，见 T44）＝
 *      **跳过链首 ECG_NUM_NOTCH_SECTIONS 节** ＋ **清零这些节的 d1/d2**。
 *   ⚠ 两者必须同时做：**振铃住在 d1/d2 里，不在系数里**——T43 实测 3mV/100ms 脉冲
 *     平台内仍叠 225µV 的 50Hz，而基线上没有 ⇒ 改 Q / 挪 f0 / 换 [1,0,0] 一律无效。
 *
 * 各档核对（逐条对 ecg_filter.c 的系数表）：
 *   ST40        [ 7] = 3 陷波 (50/100/150 Q30)             + 4 BP(0.05~40)   ← 本轮不动
 *   DIAG150     [ 4] = 1 陷波 (50 Q30)                     + 3 BP(0.05~250)
 *   DIAG150_D2  [ 5] = 2 陷波 (50/100 Q30)                 + 3 BP(0.05~250)
 *   DIAG150_D3  [ 7] = 4 陷波 (49.9/50/50.1 Q20 + 100 Q15) + 3 BP(0.05~250)
 *   IMP620      [ 9] = 5 陷波 (50簇×3 Q12 + 100 Q15 + 150 Q30) + 4 BP(0.05~150) ← T66 回退
 *   IMP620_N2   [13] = 9 陷波 (50簇×3 + 100簇×3 + 150簇×3)  + 4 BP(0.05~150) ← T66 回退
 *   DUAL_A      [12] = 9 陷波 (同上三簇)                    + 3 BP(0.05~250) ← T76
 *   DUAL_A_PLUS [18] = 15 陷波 (再 +200簇×3 +250簇×3)       + 3 BP(0.05~250) ← T77（★基线对照档）
 *   DUAL_A_PLUS_B[16] = 15 陷波 (与 A+ 逐位相同)            + **1 BP**(LP2@250) ← ★T84 投产档
 *
 * ★ T58(2026-09-20) 改动：五档 BP 由 4 节 → 3 节
 *   （`butter(4,[0.05,150],'bandpass')` 的 2 节 LP4@150 → 1 节 LP2@250；
 *     HP4@0.05 的 2 节字面量原样保留）。ST40 上角 40 Hz 属设计意图，不动。
 *   依据：150 Hz 频响 54.76% ✗（表 201.107 第 3 行下限 70%）＋过冲由 LP 阶数定。
 *   改后：150 Hz → 75.64% ✓（余量 +5.64 pp）。见 `tools/T58_*.md`。
 *
 * ★ T66(2026-09-20) **按档分离**：上面的 fc250 只保留在 D1/D2/D3（诊断档，
 *   `ECG_NUM_BP_SECTIONS=3`）；**IMP620 / IMP620_N2 回 LP4@150（BP=4 节，9/13 节）**。
 *   依据：fc250 在 ≤150 Hz 增益差＝0（对输入阻抗测试零收益），却放行 155~426 Hz
 *   3.5~1241× ⇒ 620 kΩ 接入时 1 s 窗 p-p 中位比值 99.4%→105.5%（p95 102.5%→111.2% ✗）。
 *   副作用为正：ST40 的 `NOTCH 3 + BP 4 = 7` 自洽 ⇒ 原 `#error` 自动消失。
 * ============================================================================ */
/* ECG_NUM_BP_SECTIONS 改为**按档定义**（见下方各档分支）；T66(2026-09-20) 按档分离所需。 */

/* 「工频滤波器组」的运行期默认态（0=关 / 1=开）：开。
 *   依据：噪声电平试验要求**开启**；临床默认亦为开（T44 §1.4）。「关」必须是显式动作。
 * ⚠ **复位后 .bss 段清零 ⇒ 若不加干预，运行期开关的初值会是 0＝关，不满足 fail-safe**。
 *   故必须由 ecg_filter_f32_init() / ecg_filter_f64_init() 显式写回本宏（见 ecg_filter.c）。 */
#define ECG_NOTCH_DEFAULT_ON 1

/* ---- 各档：节数 ＋ 档名 ＋ **dc_block 截止频率**（T84 新增，见上方「落码方式备案 ③」）----
 * ECG_DC_BLOCK_FC_HZ_F 是本档**低端 HP 极点预算 Σf** 的 dc_block 那一半（单位 Hz）。
 * ⚠ 改它 ⇒ Σf 变 ⇒ 判据 a 的 a1 变。改前先算预算：Σf ≤ 0.05306 Hz（3 mV/100 ms 激励）。 */
#if (ECG_FILTER_PROFILE == ECG_PROFILE_DIAG150)
#define ECG_NUM_SECTIONS 4
#define ECG_NUM_NOTCH_SECTIONS 1
#define ECG_NUM_BP_SECTIONS 3
#define ECG_DC_BLOCK_FC_HZ_F 0.05f      /* Σf = dc0.05 + HP4@0.05×2p = 0.15069 (284% 超预算，诊断档非脉冲判据用) */
#define ECG_PROFILE_NAME "D1 diag: 0.05-250Hz + notch50 (4 sections)"
#elif (ECG_FILTER_PROFILE == ECG_PROFILE_DIAG150_D2)
#define ECG_NUM_SECTIONS 5
#define ECG_NUM_NOTCH_SECTIONS 2
#define ECG_NUM_BP_SECTIONS 3
#define ECG_DC_BLOCK_FC_HZ_F 0.05f
#define ECG_PROFILE_NAME "D2 diag: 0.05-250Hz + notch50/100 (5 sections)"
#elif (ECG_FILTER_PROFILE == ECG_PROFILE_DIAG150_D3)
#define ECG_NUM_SECTIONS 7
#define ECG_NUM_NOTCH_SECTIONS 4
#define ECG_NUM_BP_SECTIONS 3
#define ECG_DC_BLOCK_FC_HZ_F 0.05f
#define ECG_PROFILE_NAME "D3 diag: 0.05-250Hz + notch49.9/50.0/50.1(Q20)+100(Q15) (7 sections)"
#elif (ECG_FILTER_PROFILE == ECG_PROFILE_IMP620)
#define ECG_NUM_SECTIONS 9
#define ECG_NUM_NOTCH_SECTIONS 5
#define ECG_NUM_BP_SECTIONS 4
#define ECG_DC_BLOCK_FC_HZ_F 0.05f      /* BP 走 LP4@150 + HP4@0.05×2p（T66 回退），Σf 同上 */
#define ECG_PROFILE_NAME "IMP620: 0.05-150Hz + notch49.8/50.0/50.2(Q12)+100(Q15)+150(Q30) (9 sections)"
#elif (ECG_FILTER_PROFILE == ECG_PROFILE_IMP620_N2)
#define ECG_NUM_SECTIONS 13
#define ECG_NUM_NOTCH_SECTIONS 9
#define ECG_NUM_BP_SECTIONS 4
#define ECG_DC_BLOCK_FC_HZ_F 0.05f
#define ECG_PROFILE_NAME "IMP620_N2: 0.05-150Hz + notch50x3(Q12)+100x3(Q20)+150x3(Q20) (13 sections)"
#elif (ECG_FILTER_PROFILE == ECG_PROFILE_DUAL_A)
#define ECG_NUM_SECTIONS 12
#define ECG_NUM_NOTCH_SECTIONS 9
#define ECG_NUM_BP_SECTIONS 3
#define ECG_DC_BLOCK_FC_HZ_F 0.05f
#define ECG_PROFILE_NAME "DUAL_A: 0.05-250Hz + notch50x3(Q12)+100x3(Q20)+150x3(Q20) (12 sections)"
#elif (ECG_FILTER_PROFILE == ECG_PROFILE_DUAL_A_PLUS)
#define ECG_NUM_SECTIONS 18
#define ECG_NUM_NOTCH_SECTIONS 15
#define ECG_NUM_BP_SECTIONS 3
#define ECG_DC_BLOCK_FC_HZ_F 0.05f      /* Σf = 0.18069 = 341% 预算 ⇒ 判据 a 实测 319 µV ✗；本档已转基线对照，见 T84 */
#define ECG_PROFILE_NAME "A+: 0.05-250Hz + notch50x3(Q12)+100x3+150x3+200x3+250x3(Q20) (18 sections)"
#elif (ECG_FILTER_PROFILE == ECG_PROFILE_DUAL_A_PLUS_B)
/* ★ T84(2026-09-21) 投产档（＝T83 候选 B）：A+ 删 HP4 两节 ⇒ **低端高通只剩 dc_block 一阶**。
 * Σf = 0.02 Hz = **37.7% 预算**（余量 62%）⇒ a1 离线预测 37.1 µV，容忍审评方刺激源 ±165%。
 * 与 A+ 的关系：第 0~14 节陷波、第 15 节 LP2@250 **逐位相同**，只少了 HP4 两节 ＋ dc 0.05→0.02。
 * 回退：`python tools/_set_profile.py --set aplus`（A+，18 节，基线段）｜`--set aplusb`（本档）。 */
#define ECG_NUM_SECTIONS 16
#define ECG_NUM_NOTCH_SECTIONS 15
#define ECG_NUM_BP_SECTIONS 1
#define ECG_DC_BLOCK_FC_HZ_F 0.02f      /* ★ B 的落点靠这一行：Σf = 0.02 = 37.7% 预算 */
#define ECG_PROFILE_NAME "A+B16: 0.05-250Hz + notch50x3(Q12)+100x3+150x3+200x3+250x3(Q20) + HP1=dc0.02 (16 sections)"
#elif (ECG_FILTER_PROFILE == ECG_PROFILE_ST40)
#define ECG_NUM_SECTIONS 7
#define ECG_NUM_NOTCH_SECTIONS 3
#define ECG_NUM_BP_SECTIONS 4
#define ECG_DC_BLOCK_FC_HZ_F 0.05f
#define ECG_PROFILE_NAME "v3 ST/monitor: 0.05-40Hz + notch50/100/150 (7 sections)"
#else
#error "ECG_FILTER_PROFILE 取值非法：合法值 0=ST40 / 1=DIAG150 / 2=DIAG150_D2 / 3=DIAG150_D3 / 4=IMP620 / 5=IMP620_N2 / 6=DUAL_A / 7=DUAL_A_PLUS / 8=DUAL_A_PLUS_B"
#endif

/* ---- 编译期自检（2026-09-20 新增）----
 * 链结构约定若不成立，旁路会跳错节且**静默出错**（多跳过 BP 节 ⇒ 输出形态错但无报错）。
 * 这是把"靠约定"升级为"靠机制"的一步；加新档位时若忘记定义 ECG_NUM_NOTCH_SECTIONS，
 * 此处会立刻报错而不是悄悄编译过去。 */
#if !defined(ECG_NUM_NOTCH_SECTIONS)
#error "新档位忘记定义 ECG_NUM_NOTCH_SECTIONS（链首陷波节数），见本文件「链结构」段"
#endif
#if !defined(ECG_DC_BLOCK_FC_HZ_F)
#error "新档位忘记定义 ECG_DC_BLOCK_FC_HZ_F（dc_block 截止频率＝Σf 的 dc 那一半），见本文件「各档：节数 ＋ 档名 ＋ dc_block 截止频率」段"
#endif
#if (ECG_NUM_BP_SECTIONS) < 1
#error "ECG_NUM_BP_SECTIONS 必须 >= 1（链尾至少要留 1 节 LP2@250，否则上角不设限）"
#endif
#if (ECG_NUM_NOTCH_SECTIONS + ECG_NUM_BP_SECTIONS) != ECG_NUM_SECTIONS
#error "链结构约定被破坏：ECG_NUM_NOTCH_SECTIONS + ECG_NUM_BP_SECTIONS != ECG_NUM_SECTIONS"
#endif
#if (ECG_NOTCH_DEFAULT_ON != 0) && (ECG_NOTCH_DEFAULT_ON != 1)
#error "ECG_NOTCH_DEFAULT_ON 只能取 0（关）或 1（开）"
#endif

#define ECG_FS_HZ_D   853.2423208191125
#define ECG_FS_HZ_F   853.2423f

/* LOD 门控平滑(一阶 IIR): alpha = 1 - exp(-1/(tau*fs)), tau = 0.1 s */
#define ECG_GATE_ALPHA_F 0.0116516f

typedef struct {
    float b0, b1, b2;
    float a1, a2;
} ecg_biquad_coeff_f32;

typedef struct {
    double b0, b1, b2;
    double a1, a2;
} ecg_biquad_coeff_f64;

/* 定义在 ecg_filter.c（内容随 ECG_FILTER_PROFILE 切换） */
extern const ecg_biquad_coeff_f32 ECG_FILTER_COEFFS_F32[ECG_NUM_SECTIONS];
extern const ecg_biquad_coeff_f64 ECG_FILTER_COEFFS_F64[ECG_NUM_SECTIONS];

/* ==========================================================================================
 * 诊断/监护 运行期可选（★T92 落码 2026-09-22；依据 tools/T91_监护诊断可选_最小改动评估_2026-09-21.md）
 * ------------------------------------------------------------------------------------------
 * 需求：诊断与监护**运行期可选**，约束 =「在现有参数基础上以最小改动实现」。
 * 实现（核心＝**只换 1 节**）：A+B 档 16 节里 **第 15 节 LP2@250 是唯一的「上角节」**；
 *   监护态只需把它换成 **LP2@45**，其余 15 节陷波 ＋ dc_block@0.02 **逐位不动**
 *   ⇒ 表长 16 不变、热路径循环边界不变、结构体尺寸不变（912 B）、节数宏全不变。
 * 判据（GB 9706.227 监护；相对 **5 Hz** 归一，限 71~110%）：40 Hz **77.20%**（余 6.20 pp ✓）
 *   ⚠ 必须 **notch OFF**；40 Hz 距 50 Hz 陷波簇仅 10 Hz ⇒ 开关使该点差 **5.03 pp**
 *     ⇒ **任何 40 Hz 报数必须标注 notch 状态**（T91 §2.4）。
 * ⚠ 本宏只在「上角节 = 链尾节」的档位定义为非负；其余档为 −1 ⇒ `test mode mon` 不动系数。
 * ========================================================================================== */
#if (ECG_FILTER_PROFILE == ECG_PROFILE_DUAL_A_PLUS_B)
#define ECG_SECTION_TOPMOST 15        /* 第 15 节 = LP2@250（诊断上角）；mon 态换成 LP2@45 */
#else
#define ECG_SECTION_TOPMOST (-1)      /* 本档不支持运行期换档（零成本、零风险） */
#endif

#define ECG_MODE_DIAG 0               /* 默认：诊断（第 15 节 LP2@250） */
#define ECG_MODE_MON  1               /* 监护（第 15 节 LP2@45）       */
#define ECG_MODE_DEFAULT ECG_MODE_DIAG

#endif /* ADS1293_ECG_FILTER_COEFFS_H */
