/*
 * 起源：报告《300mV直流偏置问题分析_2026-09-15.md》§4.1 的 fast restore 片段，
 *       当初仅作"能否直接粘贴编译"的验证。
 *
 * ⚠ 2026-09-20 更正（原注释写"不参与固件构建"，**已作废且会误导改动落点**）：
 *   本文件**已进固件构建**（EIDE/IAR；证据 EWARM/demo_ads1293/build/demo_ads1293/
 *   .obj/__/__/Filter/fast_restore_snippet.o），且是**当前唯一的滤波入口**——
 *
 *     Ads1293/ads1293.c:1300   ads1293_raw_popoutput() 内：
 *         ecgfilter.filterraw = ecg_restore_process(ecgfilter.ecgraw);
 *
 *   （该处 `#if 1` 生效；同函数 L1304~1305 直调 ecg_filter_f64_process 的那两句在 `#else` 里，
 *     当前**不编译**。）
 *
 *   ⇒ 屏幕波形 = 本文件的 ecg_restore_process()，其内部即完整链：
 *         ecg_filter_f64_process(&f64, (double)ecg_dc_block_process(&g_dc, (float)x_raw))
 *     所以**滤波器行为的一切改动，最终都要经本文件才到屏幕**（除非改 ads1293.c 的分支）。
 *
 * 编译（单文件语法自检，用工程 Filter/ 下的真实源码）：
 *   arm-none-eabi-gcc -mcpu=cortex-m4 -mfloat-abi=hard -mfpu=fpv4-sp-d16 \
 *       -std=c99 -Wall -Wextra -O2 -I"<Filter 目录>" \
 *       -c fast_restore_snippet.c -o /tmp/t.o
 */

#include <math.h>
#include <stdint.h>

#include "ecg_dc_block.h"
#include "ecg_filter.h"
#include "ecg_out.h"          /* ECG_UV_PER_CODE */

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* =========================================================================
 * A) 推荐版：空白态状态机（§4.1.3）
 *    伪迹期间输出 0，平静 200ms 后整链重置一次再干净恢复。
 *    真实数据 0 误触发；阶跃/慢斜坡都能在 1~1.5s 内恢复（见报告 §4.1.4）。
 * ========================================================================= */

#define ECG_RESTORE_TH_MV       50.0f
#define ECG_RESTORE_TH_CODES    ((double)(ECG_RESTORE_TH_MV * 1000.0f / ECG_UV_PER_CODE)) /* 445,633 */
#define ECG_ALPHA_SLOW          0.00117131f   /* 慢基线 tau = 1 s   (正常跟踪) */
#define ECG_ALPHA_FAST          0.02316742f   /* 快跟踪   tau = 50 ms (空白态内) */
#define ECG_BLANK_HOLD          170u          /* 平静保持 200 ms @ 853.24 Hz */

/* ---- T146 (2026-09-28)：START 静默窗 —— 把"预热"从 SINC 期扩到 AFE 建立期 --------
 * 🛑 本条治的是「START 后行为**不一致**」，不是「偏移大」。两者必须分开看：
 *     偏移的**幅度** ← 低端极点 fc（T144 §4.3 已定死：A+B 档 fc=0.02Hz, tau=7.96s）；
 *     偏移的**方向/有无** ← "链从哪一时刻开始跟踪输入"。**后者才是随机源。**
 *
 * 机制（为什么方向会随机）：
 *   下游 ecg_dc_block 是差分式高通，y = sum r^(n-k)*dx
 *   ⇒ 只对"跟踪起点**之后**"的输入变化响应。若起点取在 AFE 自身建立的半途
 *   （START 后输入要 ~1.5 s 才稳定，T144 §4.3c 实测）：
 *       起点在平台值**之上** ⇒ 输入之后只降 ⇒ 屏幕基线**单调下移**
 *       起点在平台值**之下** ⇒ 输入之后只升 ⇒ 屏幕基线**单调上移**
 *   AFE 停在哪个相位取决于 STOP 时长/输入电荷状态 ⇒ **方向随机**
 *   （即用户 2026-09-28 报的两种情形：在 0mV 上 ⇒ 下移；偏离 0.1mV ⇒ 上移。）
 *
 * 做法：START 后**强制静默 N 个样本**（期间输出 0 = 屏幕中央线，且**不喂链**），
 *       静默结束后的第一个样本自然成为链的跟踪起点（此时输入已稳定）。
 *       ⇒ 每次 START 的可见行为**逐样本一致**：先平线 N 样本，再从干净基线出波。
 *
 * 取值：0 = 关闭（旧行为，用于 A/B 对比）／ 2560 ≈ 3.0 s @853.24 Hz（默认）
 *       依据：AFE 慢建立 T1≈1.5 s（T144 §4.3c）。
 *       🛑 取值不是估的，是**离线扫描**出来的（`tools/_t146_consistency.py sweep`，
 *          输入 337 uV/tau1=1.5 s，峰值即"链开始跟踪后产生的可见偏移上限"）：
 *             窗 0 s → 6.92 px｜1 s → 3.55｜2 s → 1.82｜**3 s → 0.94**｜5 s → 0.25
 *          目视可靠下限 = 0.5 小格 = 0.10 mV = 3.05 px（1 px = 32.8 uV，T144 §3）
 *          ⇒ 3 s 已把观感压到读数噪声内；嫌平线久可降到 2 s（仍 1.82 px < 3.05）。
 * ⚠ 与 dc_block 的 ECG_DC_BLOCK_WARM_SKIP(=8 帧 / 9.4 ms) **不是同一件事、不要合并**：
 *   那个治 SINC 建立（5.86 ms 量级），这个治 AFE 建立（1.5 s 量级），差 2.5 个数量级。
 *   （T142b 的 SKIP=8 之所以无效，原因正是作用面差数量级 —— 见 T145 §2。） */
#ifndef ECG_RESTORE_START_SETTLE_SAMPLES
/* 🛑T147 (2026-09-28) 默认由 2560 改回 **0（关窗）**。三条理由：
 *   ① 上板实测：3 s 窗**没有**解决偏移 —— 偏移出现在窗口**之后**的运行期；
 *   ② 窗口方案有**硬边界**：窗后残余 ≈ A·e^(-W/(FS·tau_in))，压到 0.1 mV 需 W ≳ 2.3·tau_in
 *      （`tools/_t146_consistency.py window` 二维表：tau_in=3 s 需 6.9 s 平线 ⇒ 体验不可接受）；
 *   ③ 3 s 平线本身伤体验（用户反馈）。
 *   保留本宏只为 A/B：改回 2560u 即恢复。
 *   ⚠ F1（s_base 首样本初始化）与 F2（ecg_restore_start 复位状态）**不随本宏**，一直生效。 */
#define ECG_RESTORE_START_SETTLE_SAMPLES   0u
#endif
#if (ECG_RESTORE_START_SETTLE_SAMPLES > 100000u)
#error "ECG_RESTORE_START_SETTLE_SAMPLES 取值非法（允许 0 ~ 100000）"
#endif

/* ---- ★T239 步1（2026-11-12）：链状态打包成**通道上下文** -------------------
 * 旧写法（作废，留档说明改了什么）：
 *     extern ecg_dc_block   g_dc;   // 定义在 Ads1293/ads1293.c
 *     extern ecg_filter_f64 f64;
 *     static double   s_base;  static int s_seen, s_blank;
 *     static uint16_t s_hold;  static uint32_t s_restore_cnt, s_startup;
 * 打包进 ecg_restore_ctx 后 ⇒ 步2 的 CH2 用**同一段代码**、各自持状态（T239 §4.2）。
 * 🛑 本步只建 s_ctx1 一个实例：运算顺序、每个系数**逐位不变**，
 *   验收方式＝同固件 A/B 双跑比 double 位模式（T239 §4.1 / 宏 ECG_RESTORE_AB_VERIFY）。 */
static ecg_restore_ctx s_ctx1;

ecg_restore_ctx *ecg_restore_ctx_ch1(void)
{
    return &s_ctx1;
}

/* ---- 链初始化：只碰 dc/flt 的系数与节点状态 --------------------------------
 * ≡ 旧 ads1293_filter_chain_init() 里那两句
 *     ecg_dc_block_init(&g_dc, ECG_DC_BLOCK_FC_HZ_F);
 *     ecg_filter_f64_init(&f64);
 * ⚠ 搬进本文件是因为 dc/flt 现在住在 ctx 里；**宏 `ECG_DC_BLOCK_FC_HZ_F` 仍是单一真源**
 *   （按档定义在 Filter/ecg_filter_coeffs.h），没有在别处重新硬编码。 */
void ecg_restore_ctx_chain_init(ecg_restore_ctx *c)
{
    ecg_dc_block_init(&c->dc, ECG_DC_BLOCK_FC_HZ_F);
    ecg_filter_f64_init(&c->flt);
}

void ecg_restore_chain_init(void)
{
    ecg_restore_ctx_chain_init(&s_ctx1);
}

/* ---- 单实例设置/查询（转落到 ctx->flt，语义与瞬态不变） ------------------- */
void ecg_restore_ctx_set_notch(ecg_restore_ctx *c, int on)
{
    ecg_filter_f64_set_notch(&c->flt, on);
}

void ecg_restore_ctx_set_mode(ecg_restore_ctx *c, int mon)
{
    ecg_filter_f64_set_mode(&c->flt, mon);
}

int ecg_restore_ctx_get_notch(const ecg_restore_ctx *c)
{
    return ecg_filter_f64_notch_is_on(&c->flt);
}

int ecg_restore_ctx_get_mode(const ecg_restore_ctx *c)
{
    return ecg_filter_f64_mode_is_mon(&c->flt);
}

const char *ecg_restore_ctx_topmost_name(const ecg_restore_ctx *c)
{
    return ecg_filter_f64_topmost_name(&c->flt);
}

/* ---- 会话态复位（**逐字**等价旧 ecg_restore_start 的语义） -----------------
 * 复位 base/seen/blank/hold + 装载 START 静默窗。
 * 🛑 `restore_cnt` 是统计量，**不清**（沿用旧行为，别"顺手修"）。 */
void ecg_restore_ctx_session_start(ecg_restore_ctx *c)
{
    c->base    = 0.0;
    c->seen    = 0u;
    c->blank   = 0u;
    c->hold    = 0u;
    c->startup = (uint32_t)ECG_RESTORE_START_SETTLE_SAMPLES;
}

/* ---- T146 (2026-09-28)：START 钩子 ----------------------------------------------
 * 由 Ads1293/ads1293.c 的 ads1293_filter_chain_init() 在**每次 START** 调用
 * （0x01 命令 -> ads1293_start -> conversion_run(true)；ASCII `test afe start`；
 *   ads1293_reset_afe 三条入口都经它）。
 *
 * 🛑 为什么必须在这里复位：本文件 5 个 static 在 T146 之前**没有任何复位入口**。
 *   （T142b 审计"ecg_dc_block_reset 谁在调"时只搜了 Ads1293/ + Core/，漏了 Filter/，
 *    当时误记成"无调用点" —— 教训见技能 §4.99 姊妹坑：审计路径必须覆盖所有源目录。）
 *   不复位 ⇒ 每次 START 的起始条件 = 上一次会话的残值（s_base 停在旧基线、
 *   s_seen/s_blank/s_hold 停在旧状态）⇒ 冷启动走"先 200 ms 空白态"、
 *   热启动直接出波 ⇒ **START 后行为不一致**。
 *
 * 内容：① 清 base/seen/blank/hold（restore_cnt 是统计量，不清）
 *       ② 装载静默窗 startup = ECG_RESTORE_START_SETTLE_SAMPLES（宏=0 即退化为"纯复位"）
 * ⚠ 链内 dc_block/flt/out 的复位由 ads1293_filter_chain_init 自己负责，此处不重复
 *   （T239 起：dc/flt 在 `ecg_restore_ctx_chain_init()`，resampler 在 ads1293.c）。
 * ⚠ T239 步1：上面这 5 个 static 已并入 `ecg_restore_ctx`，本函数即
 *   `ecg_restore_ctx_session_start(&s_ctx1)` 的薄包装，语义逐字不变。 */
void ecg_restore_start(void)
{
    ecg_restore_ctx_session_start(&s_ctx1);
}

/* =========================================================================
 * 🛑🛑 T196p（2026-10-14）**源码备注 —— 已知隐患 ＋ 已拍板的护栏方案（本轮只登记，未落活代码）**
 *
 * 隐患：**单样本坏码会被当成 50 mV 伪迹**，代价是「空白 ~0.3 s ＋ 整链复位」。
 *   本函数的判据只用**一个样本**的 dev 与 `ECG_RESTORE_TH_CODES` 比大小 ⇒
 *   只要读回一个坏码，整条链就被复位一次，且此后 0.3 s 内**不喂链**（输出 0）。
 *
 * 实测证据（`ecg_20261014_110536_采集SOP.txt`，124 s 长，猛哥 10-14 确认**未碰过导联线**）：
 *   · **10 段**空白（段长 146~171 行 ≈0.29~0.34 s，≈每 12 s 一次）＋ **11 行**坏码；
 *   · 坏码行：列②(ecgraw) ≈ **3,758,251**（偏基线 **−2.32M code ≈ −260 mV**）、
 *     同行列⑦(ecgraw2) ≈ **2.00×** 正常值、**二者同出自一次 6 字节 SPI 突发**
 *     （`ads1293_ecg_ch()` 的 `read_reginc(CH1_ECG_H, 6)`）⇒ 判为**读码坏**、非真实电极瞬变；
 *   · 9 个非零坏码**离散仅 ±0.24%** ⇒ 越是"可重复"越不像真实电极事件。
 *
 * 现象指纹（自查/排查先看这三条，能一眼认出是"空白"而非"丢包"）：
 *   ① 列④/列⑤ **恒 0 连续 ~150 行**（列② 同期正常）；
 *   ② 列⑥ `dtcycles` 由 ~11,650 **掉到 ~794**（⇒ `return` 在"喂链"之前）；
 *   ③ "台阶＋指数衰减"出现在**零段末尾**（不在开头）。
 *
 * ⚠ 危害两条：
 *   ① 每次事件把链**整段复位** ⇒ 产生 **τ≈8 s** 的"台阶＋指数衰减"瞬态，
 *      而 `τ_th(A+B)=7.958 s` 与之几乎相同 ⇒ **极易被误当成"链对阶跃输入的响应"**去做 τ 拟合（假绿）；
 *   ② 长时采集**持续累积**（实测 ≈每 12 s 一次）⇒ 污染基线与任何"阶跃响应"分析。
 *   ⇒ 分析侧对策：`tools/_t169/_s2f_blank_scan.py`（扫空白段 ＋ 链前阴性对照 ＋ 坏码台账）。
 *
 * 🛑 护栏方案（2026-10-14 猛哥拍板"要做"；**本轮只登记、未落活代码**，实施须走
 *    「改宏 → 对齐 → 再改固件」并**另立基线**，因为"伪迹响应延迟 N 个样本"是新行为）：
 *   (a) ★推荐：**单样本超门限不立即触发** —— 先丢该样本并计数，需**连续 N 个样本同向超门限**
 *       （N=2~3）才真正进空白态。对**真伪迹**的响应延迟 = N/853.24 Hz ≈ **2.3~3.5 ms**（可忽略）；
 *       对**单点坏码**则完全免疫。落点就在本文件的第 171 行判据处，不动采集路径。
 *   (b) 备选：在 `Ads1293/ads1293.c` 的 `ads1293_raw_enqueue()` 侧对读回的 24-bit 码做
 *       量程/连续性校验，坏码**直接不入队**。⚠ 分散了"唯一真源"、且掩盖了读码问题。
 *   ★ 建议落 (a)：护栏留在**唯一真源**（本文件），且能与 S1b/S2 的判据口径一并验收。
 *
 * 关联：技能 `firmware-change-verify §4.145`；报告 `tools/_t169/T196_测试方法讨论_伪迹空白机制归因_2026-10-14.md`。
 * ========================================================================= */

/* 每样本调用一次（主循环 drain 队列时）。返回滤波输出(code 域)，伪迹空白期间返回 0。
 * ★T239 步1：本函数由 `ecg_restore_process(x)` **改名为** `ecg_restore_ctx_process(c,x)`，
 *   逐字搬移 —— 唯一的改动是把 `s_*` / `g_dc` / `f64` 换成 `c->*`。
 *   🛑 运算顺序、常数、分支结构**一律不动**：这一层是"逐位不变"的根基，别在此处顺手优化。 */
double ecg_restore_ctx_process(ecg_restore_ctx *c, int32_t x_raw)
{
    const double x = (double)x_raw;

    /* ★T146-F1：首样本即基线。
     *   原实现只在"伪迹判据"里用 base，而 base 初值 0，原始码域直流 ~6.07e6
     *   ⇒ 第 2 个样本 dev ≈ 6.07e6 ≫ 50 mV 阈值(445,633 codes)
     *   ⇒ **冷启动（上电后第一次 START）必然误进 200 ms 空白态**，而热启动不会
     *   ⇒ 这是"START 后行为不一致"的第一条确定性来源。 */
    if (!c->seen) {
        c->base = x;
        c->seen = 1u;
    }

    /* ★T146-F3：START 静默窗。期间输出 0（屏幕中央线）且**不喂链** ——
     *   链此刻是 ads1293_filter_chain_init 刚复位过的干净态（dc_block warmed=0），
     *   窗末第一个样本自然成为跟踪起点（AFE 已稳定 ⇒ 起点落在平台值上 ⇒ 不再单向漂移）。
     *   base 在窗内用慢基线持续跟踪，保证窗末 dev 判据不会误触发。 */
    if (c->startup > 0u) {
        c->base += (double)ECG_ALPHA_SLOW * (x - c->base);
        c->startup--;
        return 0.0;
    }

    if (c->seen) {
        const double dev = (x > c->base) ? (x - c->base) : (c->base - x);

        if (c->blank) {
            if (dev > ECG_RESTORE_TH_CODES) {
                c->hold = ECG_BLANK_HOLD;          /* 伪迹仍在移动 -> 续期 */
            }
            c->base += (double)ECG_ALPHA_FAST * (x - c->base);

            if (c->hold > 0u) {
                c->hold--;
                return 0.0;                       /* 空白：输出 0，不给链喂数据 */
            }
            /* 平静下来 -> 整链只重置这一次，从干净基线恢复 */
            ecg_dc_block_reset(&c->dc);
            ecg_filter_f64_reset(&c->flt);
            c->base  = x;
            c->blank = 0u;
            c->restore_cnt++;
        /* 🛑 T196p：本行是**唯一触发点**，只用**一个样本**比门限 ⇒ **单样本坏码即可复位整链**。
         *   护栏方案（连续 N 样本同向超限才触发）见本函数上方的「T196p 源码备注」，尚未落码。 */
        } else if (dev > ECG_RESTORE_TH_CODES) {
            c->blank = 1u;
            c->hold  = ECG_BLANK_HOLD;
            ecg_dc_block_reset(&c->dc);           /* warmed=0 -> 下个样本自动当新基线 */
            ecg_filter_f64_reset(&c->flt);         /* ★ 必须整链清(ECG_NUM_SECTIONS 节)，只清 dc_block 无效 */
            c->base  = x;
            c->restore_cnt++;
            return 0.0;
        } else {
            c->base += (double)ECG_ALPHA_SLOW * (x - c->base);
        }
    }
    c->seen = 1u;

    return ecg_filter_f64_process(&c->flt,
                                  (double)ecg_dc_block_process(&c->dc, (float)x_raw));
}

/* ---- CH1 兼容包装（签名与语义逐字不变；`ads1293.c` 调用点零改动） ----
 * ⚠ 全工程只有这一个实例。步2 的 CH2 走 `ecg_restore_ctx_process(&s_ctx2, ...)`。 */
double ecg_restore_process(int32_t x_raw)
{
    return ecg_restore_ctx_process(&s_ctx1, x_raw);
}

/* =========================================================================
 * B) 对照版：慢基线偏离 -> 立即重置（简单，但对慢斜坡不够稳，见报告 §4.1.6）
 * ========================================================================= */

static double   t_base = 0.0;
static int      t_base_init = 0;
static uint32_t t_restore_cnt = 0u;

//static 
int ecg_fast_restore_check(int32_t x_raw, ecg_dc_block *dcb, ecg_filter_f64 *flt)
{
    double dev;

    if (!t_base_init) { t_base = (double)x_raw; t_base_init = 1; return 0; }

    dev = (double)x_raw - t_base;
    if (dev < 0.0) dev = -dev;

    if (dev > ECG_RESTORE_TH_CODES) {
        ecg_dc_block_reset(dcb);
        ecg_filter_f64_reset(flt);
        t_base = (double)x_raw;
        t_restore_cnt++;
        return 1;
    }

    t_base += (double)ECG_ALPHA_SLOW * ((double)x_raw - t_base);
    return 0;
}

#if 0
/* ==================== 模拟主循环，验证两种调用方式都能编译 ==================== */

static ecg_dc_block   g_dcb2;
static ecg_filter_f64 g_flt2;

static int32_t fake_raw(uint32_t i, double off_mv)
{
    const double t = (double)i / ECG_FS_HZ_D;
    const double uv = 1000.0 * sin(2.0 * M_PI * 10.0 * t) + off_mv * 1000.0;
    return (int32_t)(6075000.0 + uv / 0.1122);
}

int main(void)
{
    uint32_t i;
    double y = 0.0;

    ecg_dc_block_init(&g_dcb, 0.05f);
    ecg_filter_f64_init(&g_flt);
    ecg_dc_block_init(&g_dcb2, 0.05f);
    ecg_filter_f64_init(&g_flt2);

    for (i = 0u; i < (uint32_t)(20.0 * ECG_FS_HZ_D); i++) {
        const int32_t raw = fake_raw(i, (i < 4267u) ? 0.0 : 300.0);

        /* A) 推荐版：一次调用搞定 */
        y = ecg_restore_process(raw);

        /* B) 对照版：三次调用 */
        (void)ecg_fast_restore_check(raw, &g_dcb2, &g_flt2);
        {
            const float  xd = ecg_dc_block_process(&g_dcb2, (float)raw);
            const double y2 = ecg_filter_f64_process(&g_flt2, (double)xd);
            y += y2;
        }
    }

    return (s_seen && (y == y) && (s_restore_cnt < 0xFFFFFFFFu) && (t_restore_cnt < 0xFFFFFFFFu)) ? 0 : 1;
}
#endif

/* =========================================================================
 * ★T239 步1 的**临时**验证实现（`ECG_RESTORE_AB_VERIFY == 1` 才编进来）
 *
 * 目的：证明"打包成 ctx"没有改变任何数值。
 * 做法：这里保留一份**旧实现的逐字拷贝**（`r_dc`/`r_flt` + 5 个 static + 同名逻辑），
 *   由 `ads1293_raw_popoutput()` 每样本两路并跑，`memcmp` 比较两个 `double` 的**位模式**。
 *
 * 🛑 这个文件里出现两份几乎相同的代码是**故意的**，不要"去重" ——
 *   去重后就不再有独立参照，A/B 也就失去意义。验收完**整段删除**。
 * 🛑 默认不编（宏=0）⇒ 出厂固件零冗余、零额外 CPU。
 * ========================================================================= */
#if ECG_RESTORE_AB_VERIFY

static ecg_dc_block   r_dc;
static ecg_filter_f64 r_flt;
static double   r_base        = 0.0;
static int      r_seen        = 0;
static int      r_blank       = 0;
static uint16_t r_hold        = 0u;
static uint32_t r_restore_cnt = 0u;
static uint32_t r_startup     = 0u;

uint32_t g_ab_total    = 0u;
uint32_t g_ab_mismatch = 0u;

void ecg_restore_ab_reset(void)
{
    g_ab_total    = 0u;
    g_ab_mismatch = 0u;
}

void ecg_restore_ref_chain_init(void)
{
    ecg_dc_block_init(&r_dc, ECG_DC_BLOCK_FC_HZ_F);
    ecg_filter_f64_init(&r_flt);
}

void ecg_restore_ref_start(void)
{
    r_base    = 0.0;
    r_seen    = 0;
    r_blank   = 0;
    r_hold    = 0u;
    r_startup = (uint32_t)ECG_RESTORE_START_SETTLE_SAMPLES;
}

void ecg_restore_ref_set_notch(int on)
{
    ecg_filter_f64_set_notch(&r_flt, on);
}

void ecg_restore_ref_set_mode(int mon)
{
    ecg_filter_f64_set_mode(&r_flt, mon);
}

/* ---- 以下为 T239 之前 `ecg_restore_process()` 的**逐字拷贝**（只换了变量名） ---- */
double ecg_restore_process_ref(int32_t x_raw)
{
    const double x = (double)x_raw;

    if (!r_seen) {
        r_base = x;
        r_seen = 1;
    }

    if (r_startup > 0u) {
        r_base += (double)ECG_ALPHA_SLOW * (x - r_base);
        r_startup--;
        return 0.0;
    }

    if (r_seen) {
        const double dev = (x > r_base) ? (x - r_base) : (r_base - x);

        if (r_blank) {
            if (dev > ECG_RESTORE_TH_CODES) {
                r_hold = ECG_BLANK_HOLD;
            }
            r_base += (double)ECG_ALPHA_FAST * (x - r_base);

            if (r_hold > 0u) {
                r_hold--;
                return 0.0;
            }
            ecg_dc_block_reset(&r_dc);
            ecg_filter_f64_reset(&r_flt);
            r_base  = x;
            r_blank = 0;
            r_restore_cnt++;
        } else if (dev > ECG_RESTORE_TH_CODES) {
            r_blank = 1;
            r_hold  = ECG_BLANK_HOLD;
            ecg_dc_block_reset(&r_dc);
            ecg_filter_f64_reset(&r_flt);
            r_base  = x;
            r_restore_cnt++;
            return 0.0;
        } else {
            r_base += (double)ECG_ALPHA_SLOW * (x - r_base);
        }
    }
    r_seen = 1;

    return ecg_filter_f64_process(&r_flt,
                                  (double)ecg_dc_block_process(&r_dc, (float)x_raw));
}

#endif /* ECG_RESTORE_AB_VERIFY */
