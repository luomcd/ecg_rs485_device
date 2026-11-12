#ifndef ADS1293_ECG_FILTER_H
#define ADS1293_ECG_FILTER_H

#include <stdint.h>
#include "biquad.h"
#include "ecg_filter_coeffs.h"
#include "ecg_dc_block.h"   /* ★T239：ecg_restore_ctx 内嵌 ecg_dc_block（按值持状态） */

#ifdef __cplusplus
extern "C" {
#endif

/*
 * 实时 ECG 滤波链（因果，每来一个 ADS1293 样本调用一次）：
 *
 *   x ──> [50Hz notch]* ──> [100/150Hz notch]* ──> [BP] ──> y
 *        (* 50Hz：IMP620/IMP620_N2/DUAL_A/A+ 档为 49.80/50.00/50.20 三节 Q12；
 *                  D3 档为 49.90/50.00/50.10 Q20；D1/D2/ST40 为 1 节
 *         * 100Hz：IMP620_N2/DUAL_A/A+ 档为 99.80/100.00/100.20 三节 Q20；
 *                  D2/D3/IMP620/ST40 为 1 节
 *         * 150Hz：IMP620_N2/DUAL_A/A+ 档为 149.80/150.00/150.20 三节 Q20；ST40/IMP620 为 1 节
 *         * 200/250Hz：**仅 A+ 档（T77）** —— 199.80/200.00/200.20 ＋ 249.80/250.00/250.20，各三节 Q20)
 *
 * 档位（编译期切换，见 ecg_filter_coeffs.h 顶部；切换入口 tools/_set_profile.py）：
 *   ★ T58(2026-09-20)：五档 BP 上角 150→250 Hz、LP 4 阶→2 阶（ST40 的 40 Hz 不动）。
 *     新 BP = 1×LP2@250 + 2×HP4@0.05 ⇒ 各档节数 -1，150 Hz 频响 54.76% → **75.64%**（判限 ≥70%）。
 *   ★ T66(2026-09-20)：**按档分离** —— fc250 只留给 D1/D2/D3（诊断档）；
 *     IMP620 / IMP620_N2 **回退 LP4@150**（BP 4 节，9 / 13 节）。
 *     依据：fc250 在 ≤150 Hz 增益差＝0，却放行 155~426 Hz 3.5~1241×（620 kΩ 下 p-p 尾部失真）。
 *   ★ T76(2026-09-21)：新增 **DUAL_A 档**（T75 候选 A）—— 一套参数、两个 notch 状态各过一项。
 *   ★ T77(2026-09-21)：新增 **A+ 档**（T76 §4 的 A+ 路径）= DUAL_A ＋ 链首 200/250 各一簇，并设为默认。
 *   ★ T84(2026-09-21)：新增 **A+B 档**（T83 候选 B）＝ A+ 删 HP4 两节（BP 3→1，18→16 节）
 *     ＋ dc_block 0.05→0.02 Hz，**并转为投产**；A+ 退为基线对照档（判据 a 实测 319 µV ✗）。
 *     ★ 落码方式备案：**档位宏 + 系数表分支**（不用 -D）＋ **dc_block 截止频率提升为按档宏
 *       `ECG_DC_BLOCK_FC_HZ_F`**（原来硬编码在 Ads1293/ads1293.c）。详见
 *       `Filter/ecg_filter_coeffs.h` 的「T84 落码方式备案」段与 `tools/T84_B落码_2026-09-21.md`。
 *
 *   ECG_PROFILE_DUAL_A_PLUS_B ★当前默认(T84) = A+B 档（T83 候选 B）            [16 节]
 *                                = A+ 的第 0~14 节陷波 ＋ 第 15 节 LP2@250（**逐位相同**）
 *                                  ＋ **删掉 HP4 两节** ⇒ 低端高通只剩 dc_block **一阶**
 *                                = dc_block(0.02) -> 同一串 15 陷波 -> BP 0.05~250Hz（仅 LP2@250）
 *                                ★ 为什么 dc 是 0.02：判据 a 的**极点预算法则**
 *                                  a1 ≈ A·T·2π·Σf，预算 Σf ≤ 0.05306 Hz（3 mV/100 ms 激励）
 *                                  ⇒ Σf = 0.02 ＝ **37.7% 预算**，a1 预测 37.1 µV（限 100）
 *                                → notch ON/OFF 的**全部既有判据不受影响**：≥0.67 Hz 的 |ΔH| ≤ 0.005 dB
 *                                  （陷波 15 节 + LP2 一动未动），频响/输入阻抗/判据 b 逐位不变
 *                                ⚠ 唯一代价：**0.05 Hz 以下的漂移抑制**由 5 阶降为 1 阶
 *                                  （0.05 Hz +2.4 dB / 0.1 Hz +0.8 dB / 0.2 Hz +0.22 dB，均无对应判据）
 *                                ⚠ 620 kΩ 建场实测 **63.7 s ≈ 现状 65.3 s**（B 不是退化项；
 *                                  A 才是把等待压到 25.4 s 的那个）
 *                                ★ 省 2 节：CPU 14.6% → **12.39%**；`gdtover` 顺带回到阈值内
 *   ECG_PROFILE_DUAL_A_PLUS = A+ 档（2026-09-21 T77）＝**基线对照档，不再投产**  [18 节]
 *                                = DUAL_A 全盘（9 陷波 + 3 BP）
 *                                  ＋ 链首再补 200 簇(199.80/200.00/200.20,Q20)
 *                                     ＋ 250 簇(249.80/250.00/250.20,Q20)
 *                                ★ 依据 = 「陷波节恒在链首 ＋ `notch OFF` 整段跳过链首」
 *                                  ⇒ **往链首加陷波对 OFF 态频响零代价**（T76 发现 / T77 复核 max|ΔH| = 0）
 *                                → **notch ON**  = 输入阻抗/噪声：中位 **105.76% ✓** / p95 **109.93% ✓**
 *                                  （200/250 线 −86.1 / −92.0 dB；中位余量 4.24 pp）
 *                                  **notch OFF** = 频响/脉冲/CMRR/过载：与 DUAL_A、D3 **逐位等价**
 *                                  （150 Hz 归一 **75.64% ✓**）
 *                                ★ 九档里**唯一「两个口径 × 两种状态」全过**的结构
 *                                ⚠ 代价 +6 节：CPU 按 0.8115 pp/节外推 ≈ **14.6%**（12 节 9.75%）
 *                                  ⇒ 🛑 **必须上板 DWT 实测**；回退 `--set duala`（12 节）
 *                                🛑 T83 判决：**判据 a（脉冲后偏移）过不了**（实测 a1 = 319 µV，
 *                                  Σf = 0.18069 ＝ 341% 预算）⇒ T84 起只作基线对照，**勿投产**
 *   ECG_PROFILE_DUAL_A = 双态档 v1（2026-09-21 T76）                        [12 节]
 *                                = notch 49.80/50.00/50.20(Q12)
 *                                  + notch 99.8/100.0/100.2(Q20) + notch 149.8/150.0/150.2(Q20)
 *                                  + BP 0.05~250Hz（LP2@250 + 2×HP4@0.05）
 *                                = IMP620_N2 的陷波组(9 节) + D3 的 BP(3 节)，**逐位拼接、零新参数**
 *                                → **notch ON**  = 输入阻抗/噪声（150 簇压 149.87 Hz ⇒ 解 T73 的 116.55%）
 *                                  **notch OFF** = 频响/脉冲/CMRR/过载（只剩 3 节 BP，与 D3 的 OFF 态逐位等价）
 *                                ⚠ 实验档：ON 态中位 ~105.5% ✓ 但 p95 ~111.2% ✗；**若判据取 p95 ⇒ 切回 N2**
 *   ECG_PROFILE_IMP620_N2 = 输入阻抗测试档 v2（2026-09-18，620kΩ 鲁棒性）    [13 节]
 *                                = 同上三条陷波簇 + BP 0.05~150Hz（T66 回退 LP4@150）
 *                                → 三条工频线屏幕最差残值 74.48µV → 5.86µV（12.7×）；
 *                                  实测输入阻抗 99.4%（中位）/ 102.5%（p95）✓✓
 *                                ⚠ 吃掉 ~142.7–157.5Hz 诊断带宽，勿用于正常监护
 *   ECG_PROFILE_IMP620  = 输入阻抗测试档 v1（2026-09-17，供 620kΩ 实测）     [9 节]
 *                                = notch 49.80/50.00/50.20(Q12) + notch 100(Q15)
 *                                  + notch 150(Q30) + BP 0.05~150Hz
 *   ECG_PROFILE_DIAG150_D3 = D3/S3 诊断档（2026-09-17 定稿；★频响/脉冲测试主用）[7 节]
 *                                = notch 49.90/50.00/50.10(Q20) + notch 100(Q15) + BP 0.05~250Hz
 *                                → 工频主峰压 −50dB 且**结果可重复**（抗工频落点漂移）
 *   ECG_PROFILE_DIAG150_D2 = D2 诊断档：notch 50/100 + BP 0.05~250Hz      [5 节]
 *                                    → QRS 保真，但单陷波残余随工频漂移可差 4.45×
 *   ECG_PROFILE_DIAG150    = D1 诊断档：notch 50 + BP 0.05~250Hz          [4 节]
 *                                    → R-S 保真最好，强工频下基线毛虫明显
 *   ECG_PROFILE_ST40       = v3 监护/ST 档：notch 50/100/150 + BP 0.05~40Hz [7 节]
 *                                    → 带宽最窄，抗工频谐波最稳
 *   九档共用同一套 API 与结构体，切换无需改调用代码（ECG_NUM_SECTIONS 自动适配）。
 *   注意：结构体大小随档位变化，**不同档位的对象不可混用，改档后必须重新 init**。
 *     按 biquad_df2t_f64 = 56 B/节、biquad_df2t_f32 = 28 B/节，另加固定头（f64 16 B / f32 8 B）：
 *       节数  n = 4 / 5 / 7 / 9 / 12 / 13 / 16 / 18
 *       f64  = 56n + 16 → 240 / 296 / 408 / 520 / 688 / 744 / 912 / 1024 B
 *       f32  = 28n +  8 → 120 / 148 / 204 / 260 / 344 / 372 / 456 /  512 B
 *     ★ 2026-09-21（T76）更正：上表原写「5/6/7/8/9/13 节」——那是 **T58 之前的节数**
 *       （T58 各档 -1、T66 把 IMP620/N2 又 +1），且漏了 D1 的 4 节。现按当前九档重列
 *       （★T77 加 A+ 的 18 节；★T84 加 A+B 的 16 节）。
 *
 * ⚠ 输入前置（必读，2026-09-05 实测）：ADS1293 24-bit 码中心在 ~6.075M，直接喂
 *   (float)raw 会造成 15~18s 超长瞬态 + float32 底噪劣化。必须先经 ecg_dc_block
 *   去直流（见 ecg_dc_block.h / README），raw 必须 int32_t 存储。
 *
 * 精度选型（v3 起，2026-09-09；全档通用）：
 *   低端 0.05Hz 后高通节极点 r≈0.99986 极贴单位圆，**推荐 f64 版**（ecg_filter_f64_process /
 *   ecg_chain_f64）：与设计零偏差。
 *   开销（2026-09-17 用 arm-none-eabi-gcc 10.2.1 -O2 反汇编**实测**，非估算；
 *   -mcpu=cortex-m4 -mfpu=fpv4-sp-d16，fs=853.2423，单样本预算 93760 周期 @80MHz）：
 *     6 节(D2) = 4.884% → 8 节(D3) = 6.507% → 7 节(ST40) ≈ 5.70%
 *     每样本滤波耗时 57.2µs(6节) → 76.3µs(8节)
 *   ⚠ 本节曾写 "~1% 可忽略"，实测低估约 5 倍，2026-09-17 已更正。
 *   根因：M4F 的 FPU **只做单精度**，整链 double 全走 `__aeabi_dmul/dadd/dsub` 软浮点，
 *   单节 DF2T(f64) = 47 条指令 + 9 次库调用（f32 版 = 21 条、零调用）。
 *   结论：8 节占用 6.5%，单样本余量仍 >93%，**不构成瓶颈**；但"可忽略"的说法不成立。
 *   ★ 2026-09-18 新增 IMP620_N2（13 节）**尚未上板实测**：按 0.8115pp/节外推 ≈ **10.56%**
 *     （9 节 IMP620 → 13 节，+3.25pp），单样本余量 89.4%。**必须用 DWT->CYCCNT 实测确认。**
 *   f32 版在 D3 档上实测与 f64 **逐位同值**（4Hz 与带内残余均同），故 f32 亦可用；
 *   不过 0.05Hz 极点对 f32 仍敏感，ST/基线**定量**分析建议仍用 f64。
 *
 * LOD 门控（“悬空/脱落 -> 0 基线”需求，2026-09-04 起）：
 *   设备端读 ADS1293 **0x18 ERROR_LOD**（bit0~5=IN1~6，实测悬空时可靠置位 0x07）
 *   判定导联是否接好；ok=1 输出滤波信号，ok=0 输出平滑过渡到 0。
 *   注意：0x19 ERROR_STATUS 实测全程恒 0x00（bit3 从不置位），在核对清楚前勿作唯一判据。
 *   门控系数为一阶 IIR（tau=0.1s），防置 0 / 恢复瞬间波形突跳。
 */

/*
 * 工频滤波器组旁路（2026-09-20 新增，T46 §2 ① / T47）：
 *   标准（GB 9706.225 201.12.4.105.3 ＋ 227 附录AA）**强制要求工频陷波可关**：
 *   频响 / 低频脉冲 / CMRR / 过载容限试验必须「关」，噪声电平试验要求「开」。
 *   ⇒ 运行期开关 notch_on：1=开（默认，ECG_NOTCH_DEFAULT_ON）｜0=旁路。
 *
 *   实现要点（四条，均不得简化）：
 *     ① 旁路＝**跳过链首 ECG_NUM_NOTCH_SECTIONS 节**（改循环起点，每样本零判断开销），
 *        **不是**把系数换成 [1,0,0] —— 振铃住在 d1/d2 状态里，不在系数里。
 *     ② 切回「开」时**不是清零、是 DC 预置**（把陷波节状态置为「当前输入的直流稳态」）：
 *        d1 = v·(1−b0)、d2 = v·(b2−a2)，v = 最近一次 process 的输入（结构体字段 last_x）。
 *        见下方「为什么不能清零」。切到「关」**不清状态**（跳过 ⇒ 状态不参与运算，
 *        且切回时会被 DC 预置整段覆盖，清与不清等价）。
 *     ③ **BP 节状态任何方向都原样保留**，否则低端 4 阶高通要重建 15~18s（屏幕基线长漂）。
 *     ④ 默认态由 init() 写回 ECG_NOTCH_DEFAULT_ON（复位后 .bss 清零 ⇒ 不干预就是「关」，
 *        不满足 fail-safe）。
 *
 *   为什么不能清零（T48b 实测＋离线，2026-09-20）：
 *     `on→off` 清零无影响（节被跳过）；但 `off→on` 清零 = 9 个 Q20 陷波节**冷启动**。
 *     陷波器 y = b0·v + d1，零状态下首样本只给 b0·v，而正确值是 v ⇒ 输出**从 0 爬到平台**，
 *     后级 BP 节（T58 后为 3 节；原 4 节）看到一个 ≈ 平台幅度的**阶跃**，其高通部分产生 τ≈200ms 瞬态。
 *     实测 ~130µV／离线 165µV（现行）⇒ DC 预置后 **8~17µV**，全场景稳定（改善 18~38×）。
 *     ⚠ 反面方案「不清零、保留旁路期冻结状态」在信号平稳时最好（5.6µV）但**依赖未声明假设**：
 *       旁路期间若信号变了（+1mV 直流场景）反而 316µV，**比现行还差 2 倍**。已否决。
 */

typedef struct {
    biquad_df2t_f32 s[ECG_NUM_SECTIONS];
    float   last_x;     /* ★T48b：最近一次 process 的输入，供 set_notch 做 DC 预置 */
    uint8_t notch_on;   /* 工频滤波器组：1=开（默认）｜0=旁路（标准试验态） */
} ecg_filter_f32;

typedef struct {
    biquad_df2t_f64 s[ECG_NUM_SECTIONS];
    double  last_x;     /* 同上（当前固件实际使用的是本 f64 版） */
    uint8_t notch_on;   /* 同上 */
    uint8_t mode;       /* ★T92：0=diag（默认，第 15 节 LP2@250）｜1=mon（LP2@45）。
                         *   与 notch_on 共用原对齐 padding ⇒ **sizeof 不变（912 B）** */
} ecg_filter_f64;

/* 门控平滑器: g 平滑到 ok(0/1), 输出 0~1 缓变系数 */
typedef struct {
    float g;       /* 当前门控系数 0~1 */
    float alpha;   /* 一阶 IIR 系数 1-exp(-1/(tau*fs)) */
} ecg_gate_f32;

void  ecg_filter_f32_init(ecg_filter_f32 *f);
void  ecg_filter_f32_reset(ecg_filter_f32 *f);
float ecg_filter_f32_process(ecg_filter_f32 *f, float x);
void  ecg_filter_f32_process_block(ecg_filter_f32 *f,
                                   const float *in, float *out, uint32_t n);

void   ecg_filter_f64_init(ecg_filter_f64 *f);
void   ecg_filter_f64_reset(ecg_filter_f64 *f);
double ecg_filter_f64_process(ecg_filter_f64 *f, double x);
void   ecg_filter_f64_process_block(ecg_filter_f64 *f,
                                    const double *in, double *out, uint32_t n);

/* 工频滤波器组开关。on: 0=旁路 / 非0=开启。
 * 关→开：把链首 ECG_NUM_NOTCH_SECTIONS 个陷波节的 d1/d2 **DC 预置**为当前输入的直流稳态
 *         （d1 = last_x·(1−b0)、d2 = last_x·(b2−a2)，DC 增益=1 ⇒ 输出≈输入，不给下游递阶跃）。
 * 开→关：只置标志位，**不清状态**（被跳过、不参与运算）。
 * BP 节状态在任何方向都不动。状态未变化时不做任何事（幂等）。
 * ⚠ 语义变更（2026-09-20 T48b 拍板）：原实现"双向清零"是缺陷，见本文件顶部说明。 */
void ecg_filter_f32_set_notch(ecg_filter_f32 *f, int on);
void ecg_filter_f64_set_notch(ecg_filter_f64 *f, int on);
/* 查询（返回 1=开 / 0=关；f 为 NULL 时返回默认态） */
int  ecg_filter_f32_notch_is_on(const ecg_filter_f32 *f);
int  ecg_filter_f64_notch_is_on(const ecg_filter_f64 *f);

/* 诊断/监护运行期可选（★T92，2026-09-22）。mon: 0=diag（默认）｜非0=mon。
 * 只重写第 ECG_SECTION_TOPMOST 节系数；切换前用**该节的旧 b0**反解稳态输入做 DC 预置
 * （实测 Δy 34.2 µV ＜ 常态基线 52.6 µV，**6 拍＝7.0 ms 回真值**；
 *   对照「整链重建」Δy 196 µV 且 3 s 仍偏 33 µV ⇒ 真机 15~18 s 长漂）。
 * ⚠ 与 set_notch **正交**；init() 会把 mode 写回 ECG_MODE_DEFAULT（fail-safe，同 notch 语义）。 */
void ecg_filter_f64_set_mode(ecg_filter_f64 *f, int mon);
int  ecg_filter_f64_mode_is_mon(const ecg_filter_f64 *f);
/* ★T92b(2026-09-22) 回显自证：返回第 ECG_SECTION_TOPMOST 节**运行期实际**是哪个上角，
 *   读实例并与两个常量**逐位比对**（不用阈值 —— 阈值在未来改系数时会静默失效）。
 *   返回 "LP2@250(diag)" / "LP2@45(mon)" / "??unknown"（都不匹配 ⇒ 状态机错乱或内存被踩）；
 *   档位不支持运行期换档时返回 "n/a(static)"。
 *   起因：`test mode` 原有回显的 `profile:` 字段打的是**编译期宏** ECG_PROFILE_NAME
 *        （静态档位描述串），diag/mon 两态输出逐字相同 ⇒ 现场无法自证切换是否落到系数上。 */
const char *ecg_filter_f64_topmost_name(const ecg_filter_f64 *f);

/* LOD 门控: tau_s<=0 用默认 0.1s */
void  ecg_gate_f32_init(ecg_gate_f32 *gt, float tau_s);
float ecg_gate_f32_process(ecg_gate_f32 *gt, int ok);

/* 一步组合: 滤波 -> 门控输出; 每样本调用一次, 因果可实现
 * ⚠ x 必须是已过 ecg_dc_block_process() 去直流后的波动值(几百~几千 codes),
 *   不是 ADS1293 原始 ~6.075M code —— 本函数不含 dc_block(见本文件顶部"输入前置")。
 *   完整链: raw(int32_t) -> ecg_dc_block_process() -> ecg_chain_*(去直流后的值) */
float  ecg_chain_f32(ecg_filter_f32 *f, ecg_gate_f32 *gt, float x, int ok);
double ecg_chain_f64(ecg_filter_f64 *f, ecg_gate_f32 *gt, double x, int ok);

/* =========================================================================
 * ★T239 步1（2026-11-12）：**通道上下文** —— 把滤波入口改成可实例化
 *
 * 背景：`ecg_restore_process()` 原先直接吃 `Ads1293/ads1293.c` 里的两个全局
 *   `g_dc` / `f64`，再配 5 个文件级 static ⇒ **全链只有一个实例**。
 *   CH2 要走同一条链（T237/T239），就必须把这些状态打包成结构体。
 *
 * 现状（步1）：只建 `s_ctx1` 一个实例，`ads1293.c` 调用点签名不变；
 *   **行为必须逐位不变**，验收＝同固件 A/B 双跑（T239 §4.1，宏 `ECG_RESTORE_AB_VERIFY`）。
 *
 * 两层初始化的分工（🛑 不要合并，合并后"链复位"会连会话态一起清）：
 *   `ecg_restore_ctx_chain_init()`   ：dc_block + f64 的**系数与节点状态**
 *                                      ≡ 旧的 `ecg_dc_block_init(&g_dc,..)` + `ecg_filter_f64_init(&f64)`
 *   `ecg_restore_ctx_session_start()`：base/seen/blank/hold + START 静默窗
 *                                      ≡ 旧的 `ecg_restore_start()`
 *   ⚠ 调用顺序必须与原 `ads1293_filter_chain_init()` 一致：先 chain_init，末尾 session_start。
 *
 * typeof 尺寸（按 A+B 档实算）：dc 40 + flt 912 + base 8 + startup 4 + restore_cnt 4
 *   + hold 2 + seen 1 + blank 1 = 972 → 对齐 8 ⇒ **976 B**。
 *   🛑 实测以 `sizeof` 为准，别按字段相加（见 T239 §5 的离线断言脚本做法）。
 * ========================================================================= */
typedef struct {
    ecg_dc_block   dc;           /*   40 B：去直流（含预热快相） */
    ecg_filter_f64 flt;          /*  912 B：A+B(D3) 档 16 节（含 notch_on / mode） */
    double         base;         /*    8 B：慢基线（伪迹判据用，非链内状态） */
    uint32_t       startup;      /*    4 B：START 静默窗剩余样本 */
    uint32_t       restore_cnt;  /*    4 B：进入伪迹态次数（统计量，**不复位**） */
    uint16_t       hold;         /*    2 B：空白态保持计数 */
    uint8_t        seen;         /*    1 B：首样本已定基线 */
    uint8_t        blank;        /*    1 B：伪迹空白态 */
} ecg_restore_ctx;

void   ecg_restore_ctx_chain_init(ecg_restore_ctx *c);      /* 只碰 dc/flt，不复位会话态 */
void   ecg_restore_ctx_session_start(ecg_restore_ctx *c);   /* 只复位会话态，不碰 dc/flt */
double ecg_restore_ctx_process(ecg_restore_ctx *c, int32_t x_raw);

/* 单实例设置/查询（步1 只有 CH1；步2 由 Ads1293 层的 setter 同时作用于两路）
 * ⚠ `set_notch`/`set_mode` 的**语义与瞬态完全沿用** ecg_filter_f64_*（DC 预置，不是清零），
 *   它们只是把同一个调用落到某个 ctx 的 flt 上。 */
void   ecg_restore_ctx_set_notch(ecg_restore_ctx *c, int on);
void   ecg_restore_ctx_set_mode(ecg_restore_ctx *c, int mon);
int    ecg_restore_ctx_get_notch(const ecg_restore_ctx *c);
int    ecg_restore_ctx_get_mode(const ecg_restore_ctx *c);
const char *ecg_restore_ctx_topmost_name(const ecg_restore_ctx *c);

/* CH1 实例句柄（定义在 fast_restore_snippet.c；整个工程只有它一处定义） */
ecg_restore_ctx *ecg_restore_ctx_ch1(void);

/* ---- CH1 兼容包装（签名/语义**逐字不变**，`ads1293.c` 调用点无需改） ---- */
double ecg_restore_process(int32_t x_raw); /* 去直流后的值 -> 原始 ADS1293 24-bit 码 */
void   ecg_restore_start(void);            /* ★T146：START 钩子（复位 5 个 static + 装载静默窗）*/
void   ecg_restore_chain_init(void);        /* ★T239：链初始化（dc_block + f64） */

/* =========================================================================
 * ★T239 步1 的**临时**验证开关（默认 0，验收完即删）
 *
 * 打开后 `fast_restore_snippet.c` 会再编进一份**旧实现原样拷贝**
 * （`ecg_restore_process_ref()` + 自己的 r_dc/r_flt + 5 个 static），
 * 由 `ads1293_raw_popoutput()` 每样本两路并跑、`memcmp` 比 double 的位模式。
 *
 * 判据：`test abstat` 的 `mismatch == 0` 且 `total >= 100000`，覆盖
 *   notch on/off、mode diag/mon、raw853 on/off、STOP→START、一次伪迹空白。
 * 🛑 打开后每样本滤波耗时翻倍（≈145 µs → ≈290 µs），**只用于验证，不可出厂**。
 * 用法：把下面这个 0 改成 1 → 重新构建 → 测 → 改回 0 → 再构建。
 * ========================================================================= */
#ifndef ECG_RESTORE_AB_VERIFY
#define ECG_RESTORE_AB_VERIFY   0
#endif

#if ECG_RESTORE_AB_VERIFY
void   ecg_restore_ref_chain_init(void);
void   ecg_restore_ref_start(void);
void   ecg_restore_ref_set_notch(int on);
void   ecg_restore_ref_set_mode(int mon);
double ecg_restore_process_ref(int32_t x_raw);   /* = 旧实现的逐字拷贝 */
void   ecg_restore_ab_reset(void);
extern uint32_t g_ab_total;      /* 参与比较的样本数 */
extern uint32_t g_ab_mismatch;   /* 位模式不一致的样本数（必须为 0） */
#endif

#ifdef __cplusplus
}
#endif

#endif /* ADS1293_ECG_FILTER_H */
