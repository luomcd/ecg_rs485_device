#include "main.h"
#include "stdarg.h"
#include "stdlib.h"
#include "stdbool.h"
#include "stdio.h"
#include "usbd_cdc_if.h"
#include "ads1293.h"
#include "ecg_filter.h"   /* 2026-09-20：test notch 需要开关 API 与 ECG_PROFILE_NAME */
#include "ecg_crc8.h"     /* 2026-09-23 G4：下行二进制帧 CRC8 校验（增量版 ecg_crc8_update）*/

extern void printf_msg(const char *format,...);
extern void queue_n_test(void);
/* ★T239（2026-11-12）：原 `extern ecg_filter_f64 f64;` **已删除**。
 *   滤波实例不再是全局 —— 统一走 `ads1293.h` 导出的
 *   `ads1293_set_notch/set_mode/notch_is_on/mode_is_mon/topmost_name()`。
 *   🛑 别在本文件重新 extern 任何滤波实例：那会绕过"设置扇出集中一处"的约束，
 *     步2 加 CH2 时必然漏设一路（T239 §4.2）。 */


volatile uint8_t   VCOM_RxData = 0;
volatile uint32_t  VCOM_RxLength = 0;
volatile uint8_t   *VCOM_RxBuffer = NULL;
extern   bool      _bdispascii;
extern   uint8_t   _popoutcnt;

/* ★T170-A（2026-10-08）：`0x01 START` 写表失败时**回显原因**（默认开）。
 *   为什么此刻可以回显：`ads1293_start()` 的失败分支**停在 STOP**（无流）⇒
 *     `printf_msg` 内含的 `HAL_Delay(5)` 不再有“吞 DRDY 样本”的顾虑
 *     —— 那正是原本“失败不回显”的唯一理由（见下面 0x01 段注释）。
 *   置 0 即回到 T170 之前的静默行为。 */
#ifndef ECG_START_FAIL_ECHO
#define ECG_START_FAIL_ECHO 1
#endif
extern   volatile bool _bconvstart;   /* 定义在 Core/Src/demo.c:16（RUN/STOP 态，G3 回显用）*/
typedef enum
{
  OK,              /* Message read OK. */
  NO_DATA,         /* No data in message buffer. */
  UNEXP_END_ERR,   /* Unexpected end of message found. */
  WRONG_SEQ_ERR,   /* Wrong sequence found. */
  WRONG_CHECK_ERR, /* Wrong message checksum. */
  MSG_LEN_ERR,     /* Message length exceeded */
  UNKNOWN_ERROR    /* Unknown error. */
} vcom_msg_status_t;

typedef struct
{
  uint32_t len;
  uint8_t data[128];
} vcom_msg;

enum ParseState {
	PS_WHITESPACE,
	PS_TOKEN,
	PS_STRING,
	PS_ESCAPE
};
enum ParseState stackedState;
void vcom_parseargs(char *argstr, int *argc_p, char **argv, char** resid)
{
	int argc = 0;
	char c;
	enum ParseState lastState = PS_WHITESPACE;

	/* tokenize the argstr */
	while ((c = *argstr) != 0) {
		enum ParseState newState;

		if (c == ';' && lastState != PS_STRING && lastState != PS_ESCAPE)
			break;

		if (lastState == PS_ESCAPE) {
			newState = stackedState;
		} else if (lastState == PS_STRING) {
			if (c == '"') {
				newState = PS_WHITESPACE;
				*argstr = 0;
			} else {
				newState = PS_STRING;
			}
		} else if ((c == ' ') || (c == '\t')) {
			/* whitespace character */
			*argstr = 0;
			newState = PS_WHITESPACE;
		} else if (c == '"') {
			newState = PS_STRING;
			*argstr++ = 0;
			argv[argc++] = argstr;
		} else if (c == '\\') {
			stackedState = lastState;
			newState = PS_ESCAPE;
		} else {
			/* token */
			if (lastState == PS_WHITESPACE) {
				argv[argc++] = argstr;
			}
			newState = PS_TOKEN;
		}

		lastState = newState;
		argstr++;
	}

	argv[argc] = NULL;
	if (argc_p != NULL)
		*argc_p = argc;

	if (*argstr == ';') {
		*argstr++ = '\0';
	}
	*resid = argstr;
}

void vcom_recv(uint8_t* buf, uint32_t *len)
{
    VCOM_RxData = 1;
    VCOM_RxBuffer = buf;
    VCOM_RxLength = *len;  
}

void vcom_test_analysis(uint8_t* msg, uint8_t len)
{
    int argc;
    char *argv[64];
    char *resid;    

    vcom_parseargs((char*)msg, &argc, argv, &resid);

    if( argc== 2 && memcmp(argv[1],"config",6)==0)
    {
        printf_msg("config test\r\n");
        ads1293_config_test();
        return;
    }
    if( argc== 2 && memcmp(argv[1],"ascii-0",7)==0)
    {
        printf_msg("disp protocol\r\n");
        //ads1293_autoinc_test();
        _bdispascii = 0;
        _popoutcnt = 25;
        ads1293_set_dispver(1);   /* ★T163：复位版本位（否则再发 ascii-1 会走精简分支） */
        return;
    } 
    if( argc== 2 && memcmp(argv[1],"ascii-1",7)==0)
    {
        printf_msg("disp ascii\r\n");
        _bdispascii =1;
        _popoutcnt = 4;//5;
        ads1293_set_dispver(1);   /* ★T163：本档 = 7 列，**逐字不变**（41 个硬门脚本的基线） */
        //ads1293_autoinc_test();
        return;
    } 
    /* ---- ★T163（2026-09-29）：ASCII 精简档 `ascii-2` -------------------------------
     * 猛哥 2026-09-29 拍板（T162 §八）⇒ α 方案：**新增本档，`ascii-1` 一字不改**。
     * 默认列集 = ②（链前原始码）＋ ⑤（链后值，0.1 µV 定点）⇒ **18 B/行恒定**。
     * 列①③⑥ 由下面三条开关按需打开（猛哥："不需要可以不用，需要的时候加上"）。 */
    if( argc== 2 && memcmp(argv[1],"ascii-2",7)==0)
    {
        _bdispascii = 1;
        ads1293_set_dispver(2);
        /* ⚠ 本档 ASCII 路径**不用** `_popoutcnt`（改用 `_asciipopcnt`）⇒ 把它放回
         *   二进制帧长 25，使"从 ascii-2 直接回 ascii-0"不需要额外动作。 */
        _popoutcnt = 25;
        /* 一次 printf_msg = 一次 HAL_Delay(5) ⇒ 回显必须合成**一行**（见 §诚实边界）
         * ★T163c（2026-09-29）订正两处**回显失真**：
         *   ① `cols=` 原写 `1u + col(1) + 1u + col(2) + 1u + col(3)`，而 `col()` 返回 0/1，
         *      而 ②⑤ **恒开** ⇒ 真值 = **2 + raw + idx + cpu**；原式恒多算 1
         *      （三开关全关报 3 / 全开报 6）。现改为 `2u + col(1) + col(2) + col(3)`。
         *   ② `linesz=` 原写死 `18u`，而**本命令不重置列开关** ⇒ 先 `test ascii-raw on`
         *      再发 `test ascii-2` 时实际已是 25 B 却仍报 18 ⇒ 改报
         *      `ads1293_ascii2_linesz()` 的**运行期真值**。
         *   🛑 两条都属于"回显字段来源审计"该抓的一类（回显要报真值，不报设计值）。 */
        printf_msg("ascii-2 on: raw=%u idx=%u cpu=%u batch=%u/%u rowmax=%u linesz=%u cols=%u\r\n",
                   (unsigned)ads1293_ascii2_col(1),
                   (unsigned)ads1293_ascii2_col(2),
                   (unsigned)ads1293_ascii2_col(3),
                   (unsigned)ads1293_ascii2_popcnt(),
                   (unsigned)ads1293_ascii2_popcap(),
                   (unsigned)ads1293_ascii2_rowmax(),
                   (unsigned)ads1293_ascii2_linesz(),                      /* ② 真值（默认 18） */
                   (unsigned)(2u + ads1293_ascii2_col(1) + ads1293_ascii2_col(2)
                              + ads1293_ascii2_col(3)));                   /* ① 列数：②⑤ 恒开 */
        return;
    }
    /* 纯查询（无副作用）：串口助手手动用时靠它自证当前列组合与批次 */
    if( argc== 2 && memcmp(argv[1],"asciistat",9)==0)
    {
        printf_msg("ascii ver=%u ascii=%u raw=%u idx=%u cpu=%u batch=%u/%u rowmax=%u popoutcnt=%u\r\n",
                   (unsigned)ads1293_dispver(),
                   _bdispascii ? 1u : 0u,
                   (unsigned)ads1293_ascii2_col(1),
                   (unsigned)ads1293_ascii2_col(2),
                   (unsigned)ads1293_ascii2_col(3),
                   (unsigned)ads1293_ascii2_popcnt(),
                   (unsigned)ads1293_ascii2_popcap(),
                   (unsigned)ads1293_ascii2_rowmax(),
                   (unsigned)_popoutcnt);
        return;
    }
    /* 列①：853 域样本序号（853 模式下的**首选丢包判据**；默认关）---
     * ★ 6 位口径（猛哥 2026-09-29 补充拍板："ascii 仅测试用、短时抓取 ⇒ 序号 6 位够"）：
     *   容量 999,999 / 853.2423 Hz ≈ **19.5 min**，且 `_ecgrawidx` 随 START 复位。
     *   ⇒ 回显把"位宽 + 容量 + 随 START 复位"一起报出，便于上板自证（同 `raw853` 范式）。 */
    if( argc== 3 && memcmp(argv[1],"ascii-raw",9)==0)
    {
        if( memcmp(argv[2],"on",3)==0)
        {
            ads1293_set_ascii2_col(1, 1);
            printf_msg("ascii-raw on: col1 w6 cap 19.5min@853Hz STARTreset; batch->%u/%u\r\n",
                       (unsigned)ads1293_ascii2_popcnt(),
                       (unsigned)ads1293_ascii2_popcap());
            return;
        }
        if( memcmp(argv[2],"off",4)==0)
        {
            ads1293_set_ascii2_col(1, 0);
            printf_msg("ascii-raw off: batch->%u/%u\r\n",
                       (unsigned)ads1293_ascii2_popcnt(),
                       (unsigned)ads1293_ascii2_popcap());
            return;
        }
        printf_msg("usage: test ascii-raw on|off\r\n");
        return;
    }
    /* 列③：500 域样本序号（500 模式下的**唯一丢行判据**；默认关） */
    if( argc== 3 && memcmp(argv[1],"ascii-idx",9)==0)
    {
        if( memcmp(argv[2],"on",3)==0)
        {
            ads1293_set_ascii2_col(2, 1);
            printf_msg("ascii-idx on: col=idxfilter(500Hz); batch->%u/%u\r\n",
                       (unsigned)ads1293_ascii2_popcnt(),
                       (unsigned)ads1293_ascii2_popcap());
            return;
        }
        if( memcmp(argv[2],"off",4)==0)
        {
            ads1293_set_ascii2_col(2, 0);
            printf_msg("ascii-idx off: batch->%u/%u\r\n",
                       (unsigned)ads1293_ascii2_popcnt(),
                       (unsigned)ads1293_ascii2_popcap());
            return;
        }
        printf_msg("usage: test ascii-idx on|off\r\n");
        return;
    }
    /* 列⑥：dtcycles（**全项目唯一的 CPU 占用实测手段**，默认关）---
     * ⚠ 与 `test raw853` 的"回显命令"范式一致：argc==2 时只报状态、不改行为。 */
    if( argc== 2 && memcmp(argv[1],"cpu",3)==0)
    {
        printf_msg("cpu(dtcycles):%s batch:%u\r\n",
                   ads1293_ascii2_col(3) ? "on" : "off",
                   (unsigned)ads1293_ascii2_popcnt());
        return;
    }
    if( argc== 3 && memcmp(argv[1],"cpu",3)==0)
    {
        if( memcmp(argv[2],"on",3)==0)
        {
            ads1293_set_ascii2_col(3, 1);
            printf_msg("cpu on: col6=dtcycles(DWT); batch->%u/%u\r\n",
                       (unsigned)ads1293_ascii2_popcnt(),
                       (unsigned)ads1293_ascii2_popcap());
            return;
        }
        if( memcmp(argv[2],"off",4)==0)
        {
            ads1293_set_ascii2_col(3, 0);
            printf_msg("cpu off: batch->%u/%u\r\n",
                       (unsigned)ads1293_ascii2_popcnt(),
                       (unsigned)ads1293_ascii2_popcap());
            return;
        }
        printf_msg("usage: test cpu on|off\r\n");
        return;
    }
    /* ---- ★T163：ASCII 批次数（猛哥："如果有需要可以新增。建议还是用命令"）--------
     * 语义 = ascii-2 每次 CDC 传输出**多少行**。默认 16。
     * 🛑 实际上界由 `ads1293_ascii2_popcap()` 给出：同时受队列容量（≤24，因 enqueue
     *   满时会覆盖最旧项）与缓冲（(512-1)/行上界）夹逼 ⇒ **设定值可能被下调**，
     *   所以回显里报的是**函数返回的生效值**，不是入参。 */
    if( argc== 3 && memcmp(argv[1],"asciibatch",10)==0)
    {
        int32_t n = atoi(argv[2]);
        uint32_t got;
        if( n <= 0) { printf_msg("usage: test asciibatch <n>  (cap=%u)\r\n",
                                 (unsigned)ads1293_ascii2_popcap()); return; }
        got = ads1293_set_ascii2_popcnt((uint32_t)n);
        printf_msg("asciibatch req:%d -> %u (cap=%u rowmax=%u)\r\n",
                   (int)n, (unsigned)got,
                   (unsigned)ads1293_ascii2_popcap(),
                   (unsigned)ads1293_ascii2_rowmax());
        return;
    }
    // if( argc== 2 && memcmp(argv[1],"autoinc",7)==0)
    // {
    //     printf_msg("autoinc test\r\n");
    //     ads1293_autoinc_test();
    //     return;
    // } 
    // if( argc== 2 && memcmp(argv[1],"queue",5)==0)
    // {
    //     printf_msg("queue test\r\n");
    //     queue_n_test();
    //     return;
    // }  
 
    // if( argc== 2 && memcmp(argv[1],"rdregs",6)==0)
    // {
    //     printf_msg("readregs test\r\n");
    //     ads1293_readregs_test();
    //     return;
    // }  
    
    if( argc== 2 && memcmp(argv[1],"afe",3)==0)
    {
        printf_msg("afe test\r\n");
        ads1293_afe_test();
        return;
    } 
    if( argc== 3 && memcmp(argv[1],"afe",3)==0 && memcmp(argv[2],"start",5)==0)
    {
        printf_msg("afe test start\r\n");
        ads1293_conversion(true);  
        return;
    }
    if( argc== 3 && memcmp(argv[1],"afe",3)==0 && memcmp(argv[2],"stop",4)==0)
    {
        printf_msg("afe test stop\r\n");
        ads1293_conversion(false);
        return;
    }    

    /* ---- 工频滤波器组开关（2026-09-20 新增；T46 §2 ① ／ T47；T48b/T49 修正）------
     * 标准强制要求陷波可关（GB9706.225 201.12.4.105.3 ＋ 227 附录AA）：
     *   频响 / 低频脉冲 / CMRR / 过载容限试验 ⇒ 必须「关」；噪声电平试验 ⇒ 要求「开」。
     * `test notch`        : 只回显当前状态（不改）
     * `test notch on|off` : 切换。
     *   「关」= 跳过链首 N 节陷波（不清状态）；「开」= **DC 预置**陷波节状态
     *   （原实现双向清零，切回 on 时 ~130 µV 瞬态，T48b 已修正 ⇒ 预期 ~10 µV）。
     * ⚠ `test afe start` 会经 ecg_filter_f64_init() 把开关写回默认「开」（fail-safe），
     *   故试验顺序必须是「先 start，再 test notch off」。 */
    if( argc== 2 && memcmp(argv[1],"notch",5)==0)
    {
        printf_msg("notch:%s profile:%s\r\n",
                   ads1293_notch_is_on() ? "on" : "off",
                   ECG_PROFILE_NAME);
        return;
    }
    if( argc== 3 && memcmp(argv[1],"notch",5)==0)
    {
        if( memcmp(argv[2],"on",3)==0)
        {
            ads1293_set_notch(1);
            printf_msg("notch on\r\n");
            return;
        }
        if( memcmp(argv[2],"off",4)==0)
        {
            ads1293_set_notch(0);
            printf_msg("notch off\r\n");
            return;
        }
        printf_msg("usage: test notch [on|off]\r\n");
        return;
    }

    /* ---- 诊断/监护 运行期可选（★T92 落码 2026-09-22；方案 T91）---------------------
     * 需求：诊断与监护可选，以「改动最小」为约束 ⇒ **只换 1 节**
     *   A+B 档第 15 节 = LP2@250（诊断上角）→ LP2@45（监护上角），其余 15 节逐位不动。
     * `test mode`          : 只回显（mode:diag / mode:mon）
     * `test mode diag|mon` : 切换（只重写第 ECG_SECTION_TOPMOST 节 ＋ 对该节 DC 预置）
     * ⚠ `test afe start` 经 ecg_filter_f64_init() 把 mode 写回默认 **diag**（fail-safe）
     *   ⇒ 试验顺序必须是「先 start，再 test notch off / test mode mon」。
     * ⚠ 与 `test notch on|off` **正交**，四个组合态：
     *      diag+off = 诊断频响试验态（逐位不变，T88 已验收）｜diag+on = 诊断噪声试验态
     *      mon +off = 监护频响试验态（本次新增）        ｜mon +on = 监护噪声试验态
     * 🛑 监护判据（40 Hz ≥ 71%）**必须 notch OFF**，且报数须标注 notch 状态
     *    （40 Hz 距 50 Hz 陷波边缘 ⇒ 开关使该点差 5.03 pp，T91 §2.4）。 */
    if( argc== 2 && memcmp(argv[1],"mode",4)==0)
    {
        printf_msg("mode:%s notch:%s profile:%s\r\n",
                   ads1293_mode_is_mon() ? "mon" : "diag",
                   ads1293_notch_is_on() ? "on" : "off",
                   ECG_PROFILE_NAME);
        /* ★T92b(2026-09-22)：上面那串 `profile:` 是**编译期宏**（静态档位描述），
         *   diag/mon 两态**逐字相同**、且 mon 态它仍写 250 Hz ⇒ 不能用来判档。
         *   本行读**运行期**第 ECG_SECTION_TOPMOST 节系数、与两个常量逐位比对，才是硬证据：
         *     diag ⇒ LP2@250（b0 = 0.37672777）｜mon ⇒ LP2@45（b0 = 0.02211457），差 17.04×。
         *   出现 `??unknown` = 两者都不匹配 ⇒ 状态机错乱或内存被踩，别继续测。 */
        printf_msg("topmost[%d]: %s\r\n", ECG_SECTION_TOPMOST,
                   ads1293_topmost_name());
        return;
    }
    if( argc== 3 && memcmp(argv[1],"mode",4)==0)
    {
        if( memcmp(argv[2],"diag",4)==0)
        {
            ads1293_set_mode(0);
            printf_msg("mode diag\r\n");
            return;
        }
        if( memcmp(argv[2],"mon",3)==0)
        {
            ads1293_set_mode(1);
            printf_msg("mode mon\r\n");
            return;
        }
        printf_msg("usage: test mode [diag|mon]\r\n");
        return;
    }

    /* ★T244（2026-11-12）临时诊断 `test patt 0|ff|off` **已删除**（同日 18:20 清理）。
     * 它把两路样本强制成常量，配合 PC 侧 `--pattern` 逐字节比对来定位坏点；
     * 结论已固化到 docs/T244（坏点在帧首+3/帧尾−2、坏值恒"清位" ⇒ 物理层）。
     * 🛑 别为了"临时看看"再加回来：它绕过真实样本，忘关就是一条假平线。
     * 需要复现取证时用 git 的 a302bef 版本编一版临时固件。 */

    /* ---- ★T239 步2（2026-11-12）：CH2/CPU 运行期观测 ------------------------------
     * 用法：`test chstat`（**只读**，不清零；清零随 START 发生在
     *   `ads1293_filter_chain_init()` —— 与 gdtover 同口径）。
     * 回显**一行**：
     *   `ch: ch2=<0|1> desync=<n> dtmax=<cyc>(<uus>us) over=<n>`
     *   ← 双通道开关 ＋ 两路重采样器失步次数 ＋ 两路**合计**每样本耗时峰值/越限次数
     * 🛑 判据（T239 §6）：`dtmax < 32000`（400 µs，DRDY 1172 µs 的 34%）、`over == 0`、
     *   `desync == 0`。三个数任一不过 ⇒ **停**，别拿后面的波形下结论。
     * ⚠ cyc 是真值、µs 只是给人看的换算（整数÷80），精确核算用 cyc 那列。
     * 🛑 **只允许一次 `printf_msg`**：`comm_send()` 是"忙判→丢帧"（`comm_port.c:175`
     *   在拷贝**之前**判 `s_tx_busy`）⇒ 连续两次 `printf_msg` 的第二条会被**静默丢弃**
     *   （2026-11-12 首版写成两行，实测只回 `dt:` 那行，`rs:` 那行从未出现）。
     *   需要更多字段就往同一行里加，**别开第二条**。
     * 🛑 **命令名为什么是 `chstat` 而不是 `cpustat`**：本函数的分派**全是前缀比较**
     *   （`memcmp(argv[1],"cpu",3)`），而 `cpu` 是既有的"列⑥开关"子命令
     *   ⇒ 叫 `cpustat` 会被它先吃掉（实测回的是 `cpu(dtcycles):off batch:16`），
     *   本命令永远不可达。改名 `chstat`（首 3 字符 `chs` 与既有任一子命令都不撞）。
     *   ⇒ 以后新增子命令**必须**先查前缀：`grep 'memcmp(argv\[1\],' vcomcmd.c`。
     * 🛑 必须带 `\r\n` 结尾（T163 踩过：少 CRLF 会把下一行接到本行上）。 */
    if( argc== 2 && memcmp(argv[1],"chstat",7)==0)
    {
        const unsigned dtmax = (unsigned)ads1293_dt_max_cycles();
        printf_msg("ch: ch2=%u desync=%u dtmax=%u(%uus) over=%u\r\n",
                   (unsigned)ads1293_ch2_is_on(),
                   (unsigned)ads1293_out_desync_count(),
                   dtmax, dtmax / 80u,
                   (unsigned)ads1293_dt_over_count());
        return;
    }

#if ECG_RESTORE_AB_VERIFY
    /* ---- ★T239 步1 临时验证：A/B 逐位比较统计（宏=1 才有）------------------------
     * 用法：`test abstat` 看累计；`test abreset` 清零后重新计数。
     * 判据：跑满 1~2 min 且中途覆盖 notch/mode/raw853 切换 + 一次 STOP→START 后，
     *       `mismatch` 必须为 **0**、`total` **≥ 100000**。
     * 🛑 这两条命令与下面的 `#if ECG_RESTORE_AB_VERIFY` 代码块**同生共死**：
     *   验收通过后把宏改回 0（命令自动消失），并在最终提交里删掉整段。
     * 🛑 必须带 `\r\n` 结尾，否则串口工具会把下一行接在同一行上（T163 踩过）。 */
    if( argc== 2 && memcmp(argv[1],"abstat",6)==0)
    {
        printf_msg("ab: total=%u mismatch=%u\r\n",
                   (unsigned)g_ab_total, (unsigned)g_ab_mismatch);
        return;
    }
    if( argc== 2 && memcmp(argv[1],"abreset",7)==0)
    {
        ecg_restore_ab_reset();
        printf_msg("ab reset\r\n");
        return;
    }
#endif

    /* ---- 853 原始流输出开关（2026-09-20 猛哥拍板；T54）--------------------------
     * 目的：让**每个 853 样本**都进输出流（行率 500 → 853.2423 Hz），供离线
     *   (a) 复核 250~426 Hz 段的真实输入能量；
     *   (b) 取低频三判据（位移 / 斜率 / 过冲）的「无插值伪影」版本。
     * 用法：
     *   `test raw853`        : 只回显状态（上电默认 **off**），并提示当前 hz / popoutcnt
     *   `test raw853 on|off` : 切换（off 会复位重采样器相位，见 ads1293_set_raw853）
     *
     * ⚠⚠ 打开后的四条纪律，用之前先读完：
     *   1) **时间轴基准变了**：txt 里没有时间列，时间靠「行数 × 行周期」推。
     *      500 Hz 是 2.000 ms/点；853 是 1.172 ms/点（1/853.2423）。比值 1.7065。
     *      下游脚本必须同步改 fs，否则周期 / 幅值结论按 1.7065 倍失真
     *      ——T49 已踩过：0.498 s 的周期被算成 0.292 s。**开 853 前先确认脚本 fs。**
     *   2) **上位机按 500 Hz 处理，画不对**：853 流进上位机会因行率翻倍而时基错位
     *      （需去重才对得上），⇒ raw853 on 期间**只看 txt，不看上位机波形**。
     *   3) **列③ / 列④ 固定输出 0**：idxfilter 与 dsrateraw 都是 500 Hz 重采样的
     *      产物，853 模式不产生它们（置 0 而非留陈值，避免脏列误读）。
     *   4) **丢包判据改用列①（idxraw = 853 样本序号）**，不再用列③：
     *        相邻两行 idxraw 差 **恒 1** ⇒ 无丢包；
     *        差 **> 1** ⇒ 主循环没跟上 DRDY（`_bconvint` 是单标志，被下一次中断
     *        覆盖后就丢了 raw 样本）⇒ 这才是丢包的根因位置。
     *      🛑 **减压时不要动 `_popoutcnt`**（猛哥 2026-09-20 明确）：
     *        · 它的赋值只有两处，都在既有命令里 —— `test ascii-1` ⇒ **4**、
     *          `test ascii-0` ⇒ 25（见本文件 ascii-0 / ascii-1 两分支），不是可调参数；
     *          ⚠ **2026-09-29 订正**：本条原写「`test ascii-1` ⇒ **5**」并引用了
     *          `vcomcmd.c:125/132` —— 代码实为 **`_popoutcnt = 4;`**（源码里 `4;//5;`），
     *          且那两处行号早已漂移 ⇒ **结论不变（别动它），数字与行号在此订正**，
     *          并**不再引用行号**（避免再次漂移）。
     *        · ASCII 档 **4 行** × 单行最坏 ~49 B（`%.02f` 遇 mV 级读数）≈ **196 B**，
     *          远小于 `ads1293_raw_popoutput` 的 `buf`（**512 B**）⇒ **天然安全**；
     *          手动调大只会把这个安全余量自己吃掉，属自找风险；
     *        · 切二进制档本就会把它置 25（`buff` 只需 7+25*2+1 = 58 B），
     *          也无需人工介入 ⇒ **没有需要动它的场景**。
     *        · ★T163（2026-09-29）起：**ASCII 批次另有专用入口** `test asciibatch <n>`
     *          （只对 `ascii-2` 档生效，走 `_asciipopcnt`，**仍不碰 `_popoutcnt`**）；
     *          它的上界由「队列容量 ≤24」与「缓冲 (512-1)/行上界」双重夹逼。
     *        · 若见跳号，唯一正确的减压手段是 ② 切 `ascii-0` 走二进制
     *          （2 B/样本 ≈ 1.7 kB/s，几乎无压力；代价是**无列① 可读**，
     *          其时丢包只能靠帧号 seQidx 与整机行为判断）。
     *      速率参考：ASCII 每行 40~49 B ⇒ 853 下达 **34.1~41.8 kB/s、约 213 包/s**
     *      （每包 **4** 行；原写 170 包/s 是按"5 行"算的，同上一并订正）。
     *      绝对带宽不是瓶颈（CDC 全速远高于此），风险在
     *      **单缓冲无流控 ＋ 主循环节拍** ⇒ 仍以列① 自证为准。
     *      ★ 开 `ascii-2` 后默认 **18 B/行**：853 域仅 **15.4 kB/s**；若同时把
     *        `test asciibatch 16`，CDC 调用次数 **213 → 53 次/s（−75%）**
     *        ——**调用次数才是真瓶颈**（单缓冲下每次调用都可能整批丢），见 T162 §二。
     * ⚠ **txt 输出依赖 `test ascii-1`**：上电默认 `_bdispascii = 0`（**二进制**）、
     *   `_popoutcnt = 25` ⇒ 直接 `raw853 on` 得到的是**二进制 853 流，没有 txt 文本行**。
     *   抓 txt 的完整顺序：`test ascii-1` → `test afe start` → … → `test raw853 on`。
     *   用 `test raw853` 回显自证：应看到 `popoutcnt:5 ascii:1`。
     * ⚠ 与 `test afe start` 的关系：start 会经 ecg_filter_f64_init() 复位滤波链，
     *   但不碰本开关 ⇒ 顺序建议「先 start，再 test raw853 on」（与 notch 同一注意点）。 */
    if( argc== 2 && memcmp(argv[1],"raw853",6)==0)
    {
        printf_msg("raw853:%s hz:%u popoutcnt:%u ascii:%u\r\n",
                   ads1293_raw853_is_on() ? "on" : "off",
                   ads1293_raw853_is_on() ? 853u : 500u,
                   (unsigned)_popoutcnt,
                   _bdispascii ? 1u : 0u);
        return;
    }
    if( argc== 3 && memcmp(argv[1],"raw853",6)==0)
    {
        if( memcmp(argv[2],"on",3)==0)
        {
            ads1293_set_raw853(1);
            printf_msg("raw853 on: 853.2423Hz; col(3)(4)=0; loss by col(1)idxraw\r\n");
            return;
        }
        if( memcmp(argv[2],"off",4)==0)
        {
            ads1293_set_raw853(0);
            printf_msg("raw853 off: 500Hz resampled\r\n");
            return;
        }
        printf_msg("usage: test raw853 [on|off]\r\n");
        return;
    }

    /* ---- 双通道(CH1+CH2)输出开关（★G8 2026-09-23；T125）-------------------------
     * 用法：
     *   `test ch2`        : 只回显（上电默认 **off**）＋ 当前帧长/LEN/ASCII 档/popoutcnt
     *   `test ch2 on|off` : 二进制显示帧 59 B ↔ **109 B**
     * 语义（G8 拍板）：on 时帧 = `AA 55 01 | LEN=3+4N | SEQ | STA | [CH1×N][CH2×N] | CRC`，
     *   且 **CH2 的 N 个点 = CH1 的 N 个点**（直接复制，不为 CH2 做滤波/重采样）
     *   ⇒ 上位机两个画布**逐位完全相同**（这是验收本步的硬判据）。
     * 🛑 三条纪律（用之前读完）：
     *   1) **先断开上位机** —— 它按 59 B 定长解析，109 B 帧进来必然错乱（波形全乱/
     *      卡死/CRC 报错）。这不是固件 bug ⇒ 用**串口助手**抓包。测完立刻 `ch2 off`
     *      再重连上位机。
     *   2) **本步 CH2 不是真实信号**，它是 CH1 的副本。要看 CH2 真值（芯片 0x3A-0x3C
     *      原始码）请用 ASCII 档：`test ascii-1` 的**第⑦列**就是 ecgraw2。
     *   3) **不许改 `_popoutcnt`**（猛哥 2026-09-20 明确）：帧长由 LEN 公式给，N 仍 25。
     * ⚠ 与 STA bit2 联动：on ⇒ bit2=1（I&II）⇒ 上位机状态栏「工况」随之变化。
     * ⚠ 与 `test raw853` 正交、可同时开（853 双通道 = 109 B × 853 行/s ≈ 93 kB/s，
     *   CDC 带宽够，但**单缓冲无流控** ⇒ 压测请一次只开一个）。 */
    if( argc== 2 && memcmp(argv[1],"ch2",3)==0)
    {
        printf_msg("ch2:%s frame:%uB len:%u ascii:%u popoutcnt:%u\r\n",
                   ads1293_ch2_is_on() ? "on" : "off",
                   ads1293_ch2_is_on() ? (unsigned)(9u+4u*(unsigned)_popoutcnt)
                                       : (unsigned)(9u+2u*(unsigned)_popoutcnt),
                   ads1293_ch2_is_on() ? (unsigned)(3u+4u*(unsigned)_popoutcnt)
                                       : (unsigned)(3u+2u*(unsigned)_popoutcnt),
                   _bdispascii ? 1u : 0u,
                   (unsigned)_popoutcnt);
        return;
    }
    if( argc== 3 && memcmp(argv[1],"ch2",3)==0)
    {
        if( memcmp(argv[2],"on",3)==0)
        {
            ads1293_set_ch2(1);
            printf_msg("ch2 on: dual 109B [CH1x25][CH2x25]; DISCONNECT PC TOOL FIRST!\r\n");
            return;
        }
        if( memcmp(argv[2],"off",4)==0)
        {
            ads1293_set_ch2(0);
            printf_msg("ch2 off: single 59B\r\n");
            return;
        }
        printf_msg("usage: test ch2 [on|off]\r\n");
        return;
    }

    /* ---- G3(2026-09-23)：寄存器快照（协议 v2.2 §3.2，Type 0x03）-------------------
     * `test snapshot` : 读表内 36 条寄存器 ⇒ 上行**一整帧 0x03**（REQID = 0x00「上电主动上报」）。
     *   为什么要这条 ASCII 命令（T106 §4-G3 的两个理由）：
     *     ① 让 **G3 不依赖 G4** —— 二进制下行分流（`vcom_recv_analysis` 的 0xAA55 分支）
     *        还没落码，没有它就只能等 G4 才能验收快照 ⇒ 加这条才叫"一步一测"；
     *     ② 长期有用：串口助手随时能要一份**机器可读**的全表快照，与 `test afe`
     *        的 36 行十六进制回显对拍（那 36 行是文本、粘不出结构化数据）。
     * ⚠ 本通道**不加 STOP 态硬门**（调试自由，RUN 态也能要），只在回显里报当前态 run=0/1：
     *   RUN 态读 36 条会与 DRDY 抢 SPI 时基（可能少 1~2 个样本）且与显示帧混流
     *   ⇒ 要干净快照请先 `test afe stop`。🛑 **二进制下行通道（G4 的 0x10）必须加硬门。**
     * ⚠ 别与 `test config` 混：那条走 `ads1293_config_test()` 会**写** CONFIG=0x00；
     *   本命令**只读、绝不写**。
     * ★ 验收（串口助手，先 `test afe` 写表并回读 OK，再 `test snapshot`）：
     *   ① 收 **80 B**，帧头 `AA 55 03 4A 00 00 24`（TYPE=03 / LEN=0x004A=74 / REQID=00 / COUNT=0x24=36）；
     *   ② 其后 72 B 为 (REG,VAL)×36，REG 升序；VAL 应与 `regs_leaderIII` 逐条全等
     *      —— ⚠ **唯一例外是首项 0x00 CONFIG**：其 START_CON 位随运行态变
     *      （`test afe` 后 = 0x00；RUN 中 = 0x01；`test afe stop` 后 = 0x02）
     *      ⇒ 上位机 `Config/expected_regs.json` 也正是把 0x00 排除在 35 项比对之外（同一理由）；
     *   ③ CRC8 全对（覆盖 `[2]` 起 77 B）；④ 回显行 `snapshot: items=36 len=74 bytes=80 run=? ret=0`。*/
    if( argc== 2 && memcmp(argv[1],"snapshot",8)==0)
    {
        uint8_t cnt  = 0;
        int     sret = ads1293_send_snapshot(0x00u, &cnt);   /* 0x00 = 上电主动上报 */
        printf_msg("snapshot: items=%u len=%u bytes=%u run=%u ret=%d\r\n",
                   (unsigned)cnt,
                   2u + 2u * (unsigned)cnt,
                   8u + 2u * (unsigned)cnt,
                   _bconvstart ? 1u : 0u,
                   sret);
        return;
    }

    printf_msg("argc=%d \r\n",argc);

}

/* ---- G4(2026-09-23)：下行二进制命令分流（协议 v2.2 §5，下行帧 TYPE=0x80）----------------
 * ★ 本步是**全局风险最高**的一处改动：它插在**既有 ASCII 调试通道的前面**。
 *   一旦漏掉 `return`、或没把接收标志复位，二进制字节就会流进 CRLF 扫描/拷贝循环
 *   （`:369-396`）⇒ 污染 `msg.data` ⇒ 撞毁 `:403` 的 `memcmp("test",4)`
 *   ⇒ `test afe` / `test notch` / `test raw853` / `test snapshot` 全部**静默失效**。
 *   所以本函数的契约只有一条：**整包吃掉，或整包不吃**（不吃就原样留给 ASCII 路径）。
 *
 * 本步（G4）只实现 `0x10 REQ_SNAPSHOT`（协议 §5.1）；其余 CMD、未知 TYPE **一律静默**。
 * 附带收益：解决 T105 §6 记的那条缺口 —— G4 之前，上位机"连接即自动下发 REQ_SNAPSHOT"
 * 会把 7 B 二进制扔进 CRLF 扫描路径；有了分流，这类帧在此被就地吃掉、不再污染 ASCII 通道。
 *
 * 三条纪律（依据 T106 §9c 预检卡）：
 *  ① 🛑 **一次消费**：本函数返回后，调用方**立刻复位** `VCOM_RxData/Buffer/Length`
 *     并 `return`，**绝不回落到 CRLF 扫描**（见 `vcom_recv_analysis` 调用点注释）。
 *     ⚠ 复位不能指望 `:398-400` —— 那在 `while(1)` 之后，本分支已 `return` 走不到；
 *     漏了就变成"主循环每圈重发一帧快照"。
 *  ② 🛑 **禁止 `printf_msg`**：`demo.c:41` 的 `HAL_Delay(5)` ⇒ 每命令丢 ≈4.27 个 DRDY 样本。
 *     于是"坏帧 / 未支持 / 状态门"三种拒绝**都没有回显** —— 这正是副判据①的观测方式：
 *     ***没有*回帧才算对**（不是"没报错就算对"）。
 *  ③ **校验口径与上行同源**：CRC8 覆盖 TYPE+LEN+PAYLOAD（`Filter/ecg_crc8.h` 口径，
 *     poly=0x07/init=0x00/不反射）。这里用**增量版** `ecg_crc8_update()` 逐字节算 ——
 *     `ecg_crc8.c` 里一次性版就是 `while (len--) crc = ecg_crc8_update(crc, *data++)`，
 *     两者**代码级同源**（不是两套实现）⇒ 结果必然一致；好处是**不必把 volatile 的
 *     CDC 缓冲拷进局部数组**（省 128 B 栈、省一次拷贝，也绕开 `-Wdiscarded-qualifiers`）。
 *
 * 帧格式（协议 v2.2 §5，与上行同构）：
 *   `AA 55 | TYPE=0x80 | LEN(2B LE) | PAYLOAD(CMD[,ARG...]) | CRC8`；
 *   总长 = **6 + LEN**；LEN ≥ 1（至少含 1 B CMD）；本步最长帧 = **8 B**（CMD + 1 B ARG）。
 * ----------------------------------------------------------------------------------- */

/* 下行帧 LEN 的显式上限：= CDC 单包上限 64 − 帧开销 6。
 * ⚠ 它同时兜住"CDC 给出异常长包"这种越界可能；将来 G10（批量 SET_REGS）需要
 *   一次写全 36 条（LEN=72 > 58）时，**必须先给 CDC 加分包累积**再放开这个上限。 */
#define VCOM_BIN_MAX_LEN   58u

static void vcom_bin_dispatch(const volatile uint8_t *src, uint32_t len)
{
    uint32_t i;
    uint8_t  type;
    uint16_t argLen;
    uint8_t  calc = 0x00u;

    if (len < 7u) {                       /* 最小合法帧：LEN=1 ⇒ 6+1 = 7 B */
        return;
    }
    if (src[0] != 0xAAu || src[1] != 0x55u) {   /* 双保险：调用方已判过一次 */
        return;
    }

    type   = src[2];
    argLen = (uint16_t)((uint32_t)src[3] | ((uint32_t)src[4] << 8));

    /* ★ 长度对核（T106 §9c C-2.4）：帧总长必须 = 6 + LEN。
     *   LEN 是**帧内自称**的值 ⇒ 与实收长度不符即丢弃；对核通过后，
     *   后面所有按下标取值（src[5] … src[len-1]）才都落在实收范围内。 */
    if (argLen > VCOM_BIN_MAX_LEN) {
        return;
    }
    if ((uint32_t)6u + (uint32_t)argLen != len) {
        return;
    }

    /* CRC 覆盖 [2] 起 3+LEN 字节（含 TYPE/LEN/PAYLOAD，不含帧头与 CRC 自身）*/
    for (i = 2u; i < (len - 1u); i++) {
        calc = ecg_crc8_update(calc, src[i]);
    }
    if (calc != src[len - 1u]) {
        return;                           /* 坏 CRC ⇒ 静默丢弃（副判据①）*/
    }

    if (type != 0x80u) {
        return;                           /* 本步只认下行命令帧 */
    }

    switch (src[5]) {
      case 0x10u:                         /* REQ_SNAPSHOT（协议 §5.1）*/
        /* 🛑 状态门（T106 §9c C-2.6）：协议规定快照**只在 STOP 态**回 ——
         *  RUN 态读 36 条会与 DRDY(1172 µs) 抢 SPI 时基 ⇒ 可能丢 1~2 个样本、
         *  并与显示帧混流。⚠ 与 ASCII 通道（`:342` 的 `test snapshot`）**口径不同**：
         *  那条是调试通道、**不加硬门**；本二进制通道**必须加**。
         *  RUN 态**静默**（不回任何字节）⇒ 上位机侧表现为"等满 1.0 s 超时"。 */
        if (_bconvstart) {
            return;
        }
        /* REQID 回填触发它的 CMD ⇒ `0x10`（ASCII 通道恒 `0x00`）。
         * 这也是上位机 `SnapshotOutcome.IgnoredStale` 分支**第一次真正可达**
         * （T112 §9d.6 记的"路线 A 下该分支不可达"到此解除）。
         * count 传 NULL：本层不需要 COUNT，函数内部已判空。 */
        (void)ads1293_send_snapshot(0x10u, NULL);
        break;

      case 0x03u:                         /* RESET（协议 §5.1；★G5 2026-09-23）*/
        /* ⚠ **不加状态门**（T106 §9e.6 待拍⑤）：主判据正是"**运行中**点 Reset ⇒ 显示帧停
         *   ＋ 收到快照"，必须允许 RUN 态进入。🛑 与上面 0x10（**必须加**门）口径**相反** ——
         *   别看着 0x10 那段抄门过来。
         * 序列与返回值语义全部封装在 ads1293_reset_afe() 内（停流/STANDBY → 清两队列 →
         *   滤波链复位 → 写表+回读 → 停在 STOP → 上行 0x03 回执，REQID 回填 0x03）。
         * 返回值**不许 printf_msg 回显**（printf_msg 内含 HAL_Delay(5)，demo.c:41）：
         *   写表失败 ⇒ **不发快照** ⇒ 上位机等满 1.0 s 超时（与 0x10 的 RUN 态门同一种
         *   可识别表现）。"失败原因回传"另立一步。 */
        (void)ads1293_reset_afe();
        break;

      case 0x01u:                         /* START（协议 §5.1；★G6 2026-09-23）*/
        /* 🛑 **不加状态门**（T106 §9g.6 待拍③）：ads1293_start() 首步就停流
         *   ⇒ 重复 START 是**安全的重启**（幂等）。⚠ 与上面 0x10（**必须加**门）
         *   口径**相反**、与下面 0x03（**不加**）同口径 —— 别看着 0x10 段抄门过来。
         * 序列全部封装在 ads1293_start() 内：停流/STANDBY → 清两队列 → **写全表**(P1)
         *   → 成功才起流（写表失败 ⇒ **不起流**、停在 STOP，见待拍②）。
         * 隐式 ACK = **显示帧流"起"**（协议 §5.1）⇒ 🛑 **不发快照**，别等错。
         * 返回值**不许 printf_msg 回显**（内含 HAL_Delay(5)，demo.c:41）：写表失败
         *   ⇒ 不起流 ⇒ 上位机 IsStreaming 1.5 s 超时、按钮回「▶ Start」⇒ 可识别。 */
        {
            /* ★T170-A（2026-10-08）：失败**回显原因**。此前完全静默 —— 上位机只能靠
             *   1.5 s 超时识别，连“第几条不一致”都看不到（T170 §0）。
             *   🛑 此刻可安全回显：失败分支**停在 STOP**（无流），printf_msg 的
             *     HAL_Delay(5) 不再有“吞样本”顾虑。置 `ECG_START_FAIL_ECHO 0` 可关。 */
            int sr = ads1293_start();
#if ECG_START_FAIL_ECHO
            if (sr != 0) {
                printf_msg("start FAIL: idx=%d reg=0x%02X want=0x%02X got=0x%02X (not streaming)\r\n",
                           sr, ads1293_wt_fail_reg(), ads1293_wt_fail_want(), ads1293_wt_fail_got());
            }
#else
            (void)sr;
#endif
        }
        break;

      case 0x02u:                         /* STOP（协议 §5.1）*/
        /* 不加状态门（待拍③）：ads1293_stop() 幂等，STOP 态再发一次无副作用。
         * 隐式 ACK = **显示帧流"停"**（协议 §5.1）⇒ 🛑 同样**不发快照**。 */
        ads1293_stop();
        break;

      case 0x04u:                         /* SET_MODE（协议 §5.1；★G7 2026-09-23）*/
        /* 🛑 **不加状态门**（T106 §9k.6 待拍①）：STOP 态改滤波设置无副作用
         *   （`ads1293_start()` 内的 filter_chain_init 会把 mode 写回 ECG_MODE_DEFAULT，
         *   见 P8）⇒ 加门只多一个分支。⚠ 与上面 0x10（**必须加**门）口径**相反**、
         *   与 0x03/0x01/0x02 同口径 —— 别看着 0x10 段抄门过来。
         * ③ **per-CMD 长度校验**（待拍③）：本协议**第一条带 ARG 的命令**，从严 ——
         *   LEN 必须 = 1(CMD) + 1(ARG) = 2。🛑 **只加新命令**（0x04/0x05），不回头给
         *   已上板验收过的 0x01/0x02/0x03/0x10 补同款（补了要重验基线）。
         * ④ ARG 非法值（≠0/1）**交给内核**（待拍④）：set_mode 内 `mon = mon ? 1 : 0`
         *   已是容错语义 ⇒ 零额外代码。
         * ACK = 下一帧**显示帧 STA.bit0** 跟随（不是快照；待拍②）—— STA 由
         *   ads1293_status_byte() **运行期读取** CH1 的滤波 ctx
         *   （`ads1293_mode_is_mon()` ⇒ `ecg_restore_ctx_get_mode()`）⇒ 天然跟随。
         *   ★T239 起不再有 `f64` 这个全局，读点已收敛到 ads1293.c 的访问器里。
         *   STOP 态无显示帧 ⇒ **无回执**（不是丢帧，协议 §5.1 本就只以 STA 体现）。
         * ⚠ 侧效应：换第 ECG_SECTION_TOPMOST 节系数 ＋ 对该节 DC 预置 ⇒ 波形一次
         *   瞬态台阶（~10 µV）**是设计不是缺陷**（T91 策略 B / T92b 已验收）。 */
        if (argLen != 2u) {
            return;
        }
        ads1293_set_mode(src[6]);
        break;

      case 0x05u:                         /* SET_NOTCH（协议 §5.1；★G7）*/
        /* 状态门口径同 0x04（**不加**，待拍①）；per-CMD 长度校验同 0x04
         *   （argLen 必须 == 2）；ARG 非法值交内核（待拍④）。
         * ⚠ 侧效应：notch off→on 会 DC 预置陷波节（T48b）⇒ 波形一次瞬态台阶
         *   （~10 µV）**是设计不是缺陷**；off 方向只跳过该节、**不清状态**（切换
         *   开销最小、数值最稳）。 */
        if (argLen != 2u) {
            return;
        }
        ads1293_set_notch(src[6]);
        break;

      case 0x06u:                         /* SET_CHANNEL（协议 §5.1；★G9 2026-09-23）*/
        /* 🛑 **状态门必须加**（协议 §5.1 命令表：允许态 = **仅 STOP**）：RUN 态
         *   `START_CON=1` 会锁住芯片配置寄存器（P2）⇒ 切通道不生效；且成功时会回一帧
         *   快照 ⇒ 上位机会拿到「看起来成功、其实没切」的假象。
         *   ⚠ 口径归属：与 `0x10`/`0x11` **同口径（都要门）**，与 `0x01`/`0x02`/`0x03`/
         *   `0x04`/`0x05`（**都不加门**）**相反** —— 别看上面那几条抄。
         *   RUN 态**静默**（不回任何字节）⇒ 上位机侧表现为等满 1.0 s 超时。
         * ② **per-CMD 长度校验**（同 0x04/0x05 从严）：ARG = 1 B ⇒ LEN 必须 == 2。
         *   🛑 漏这道门：LEN=1 的 `AA 55 80 01 00 06 xx` 能过长度对核（6+1=7=len）
         *   ⇒ 会拿上一帧的 `src[6]` 当 ARG ⇒ **静默用错值**。非法长度 ⇒ 静默丢弃。
         * ③ **复用 G8 的 setter**，不新增函数：`ads1293_set_ch2(src[6])`（`!=0 => I&II`）。
         *   `_bch2` 是 static（ads1293.c:55）⇒ 分派层只能走导出的 setter，不能直写。
         *   ⇔ 与 ASCII `test ch2 on|off` **共享同一真值源**（同 `test raw853`/`test notch` 规矩）
         *     ⇒ 状态字节 STA.bit2 与帧长同时跟随，界面可从两处独立察觉。
         * ④ ACK = 上行 `0x03` 快照（REQID = 0x06）＋ 后续帧长 53/103 ＋ STA.bit2 跟随。
         *   🛑 无 printf_msg（命令路径铁律）。 */
        if (_bconvstart) {
            return;
        }
        if (argLen != 2u) {
            return;
        }
        ads1293_set_ch2(src[6]);
        (void)ads1293_send_snapshot(0x06u, NULL);
        break;

      case 0x11u:                         /* SET_REGS（协议 §5.1；★G10 2026-09-23）*/
        /* 🛑 **状态门必须加**（协议「允许态 = **仅 STOP**」）：RUN 态 `START_CON=1`
         *   会把 `0x13` / `0x21`-`0x24` 这类寄存器**锁住** ⇒ 写入**静默失败**；而本通道
         *   成功时会回一帧快照 ⇒ 上位机会拿到"看起来成功、实际没写进去"的**假象**。
         *   ⚠ 口径归属：与 `0x10` **同口径（都要门）**，与 `0x03`/`0x01`/`0x02`/
         *   `0x04`/`0x05`（**都不加门**）**相反** —— 别看着下半段那几条抄。
         *   RUN 态**静默**（不回任何字节）⇒ 上位机侧表现为"等满 1.0 s 超时"。
         * ③ **per-CMD 长度校验**（同 0x04/0x05 从严）：ARG = `(REG,VAL)×N` ⇒
         *   LEN 必须 = **1 + 2N**（奇且 ≥ 3）。🛑 漏这道门的后果：LEN=2 的
         *   `AA 55 80 02 00 11 CR`（长度对核 `6+2=8=len` 能过！）会被
         *   `(argLen-1)/2 = 0` 判成"0 项"或把 VAL 误当 REG ⇒ 必须**显式拒**。
         *   非法长度 ⇒ **静默丢弃**（负向判据：不崩、不写、不回）。
         * ④ **帧长上限 = 策略 B（T127 拍板②）**：`argLen > VCOM_BIN_MAX_LEN(58)`
         *   那道门（在 `vcom_bin_dispatch()` 开头）已保证 `N ≤ 28` ⇒ 帧总长 `7+2N ≤ 63 B`
         *   ⇒ **天然单包落地**（CDC 单包上限 64 B）。
         *   ⇒ **36 项必须分 2 批**（28 + 8）下发。
         *   🛑 **t127 Q1 订正**：协议 §5 原写"CDC 接收缓冲 `msg.data[128]` ⇒ N ≤ 50、
         *   36 项一次下发安全"是**错的** —— `msg` 是 **ASCII 通道**的局部结构体；
         *   二进制通道的真闸就是 `VCOM_BIN_MAX_LEN`。文档已订正，**别照旧文实现**。
         *   🛑 **不改 CDC 层、不动 `VCOM_BIN_MAX_LEN`、不碰 `usbd_cdc_if.c`** ——
         *   策略 B 的最大优点正是"固件不需要为它做任何事"（留到上位机真做
         *   「一键下发差异」时再议策略 A + 下行累积缓冲）。
         * 白名单分工（固件只拒 7 条、其余 29 条放行；差的 4 条**必须放行**否则 G9 坏掉）
         *   见 `Ads1293/ads1293.c` 的 `_reg_writable()` 上方注释。
         * 返回值**不许 printf_msg 回显**（内含 HAL_Delay(5)，demo.c:41）：写失败/被拒 ⇒
         *   内核**不发快照** ⇒ 上位机等满 1.0 s 超时（与 0x10 的 RUN 态门同一种可识别表现）。 */
        if (_bconvstart) {
            return;
        }
        if (argLen < 3u || (argLen & 1u) == 0u) {   /* LEN 必须 = 1 + 2N（奇且 ≥ 3）*/
            return;
        }
        (void)ads1293_set_regs(&src[6], (uint32_t)((argLen - 1u) >> 1));
        break;

      case 0x12u:                         /* SET_REGS_FORCE 实验写（协议 v2.3 §5.1；★T141 2026-09-24）*/
        /* 🛑 本 case 与上面 `case 0x11u` 的**两道门逐字相同**（`_bconvstart` 状态门 + 长度门），
         *   差别**只有一处**：调 `_ex(..., allow_protected = 1, reqid = 0x12)` ⇒ 白名单放行全部 36 条。
         *   ⚠ 抄 0x11 时**别漏状态门**：RUN 态 `START_CON=1` 会锁住 `0x13`/`0x21`-`0x24` ⇒ 写入
         *     **静默失败**，而本通道成功时会回一帧快照 ⇒ 上位机拿到"看起来成功、实际没写进去"的假象。
         *   ⚠ 白名单纪律（协议 v2.3）：候选集合由上位机 json 的 `writable` 数组决定；
         *     固件这道门只放行 ② 那 7 条**表内**项，**表外地址任何模式都仍拒**。
         *   ⚠ 负向判据与 0x11 同款：非法长度 / RUN 态 / 表外地址 ⇒ **静默**（不崩、不写、不回）。
         *   ⚠ **旧固件兼容**：本码位在旧固件上落到下面的 `default:` ⇒ 静默丢弃 ⇒ 上位机等满
         *     1.0 s 超时（可识别、无副作用）⇒ 步②③可分批烧录。 */
        if (_bconvstart) {
            return;
        }
        if (argLen < 3u || (argLen & 1u) == 0u) {   /* LEN 必须 = 1 + 2N（奇且 ≥ 3）*/
            return;
        }
        (void)ads1293_set_regs_ex(&src[6], (uint32_t)((argLen - 1u) >> 1), 1u, 0x12u);
        break;

      default:                            /* 其余 CMD 本步未实现 ⇒ 静默 */
        break;
    }
}

void vcom_recv_analysis(void)
{

    uint32_t s0_index = 0;
    uint32_t s1_index = 1;
    uint8_t  source[2] = {0};
    vcom_msg_status_t status;
    vcom_msg  msg = {0};    
    if (VCOM_RxData == 0) return;

    /* ---- G4(2026-09-23)：下行二进制帧分流（🛑 全局风险最高的一段）---------------------
     * 位置纪律：必须在进入 CRLF 扫描（下面的 `while(1)`）**之前**，且**必须 `return`**。
     *
     * 判据"前两字节 = 0xAA 0x55"为什么不会误吞 ASCII 命令：可打印 ASCII 恒 ≥ 0x20，
     * 永远凑不出 0xAA/0x55（都 > 0x7F 或不可打印）⇒ 两条通道互不干扰（T113 已核）。
     *
     * 🛑 复位三件套**必须写在分支里**，不能交给 `:398-400` —— 那三行在 `while(1)` 之后，
     *   本分支已经 `return`，走不到。漏掉的后果不是"多一条日志"，而是：
     *   `VCOM_RxData` 仍为 1 ⇒ 主循环下一圈又进本函数 ⇒ 拿**同一个** `VCOM_RxBuffer/Length`
     *   反复命中 ⇒ 每圈发一帧快照（速率 = 主循环频率）⇒ 总线被淹。
     *
     * 分流落码后，"G4 之前上位机连上就往下行扔 7 B 二进制"（T105 §6 缺口）就地消失：
     * 那些帧在此被吃掉，不再污染后面的 ASCII 通道。*/
    if (VCOM_RxLength >= 7u && VCOM_RxBuffer[0] == 0xAAu && VCOM_RxBuffer[1] == 0x55u)
    {
        vcom_bin_dispatch(VCOM_RxBuffer, VCOM_RxLength);
        VCOM_RxData   = 0;
        VCOM_RxBuffer = NULL;
        VCOM_RxLength = 0;
        return;
    }

    while(1)
    {
        if (s0_index >= VCOM_RxLength)
        {
          status = UNEXP_END_ERR;
          break;
        }

        source[0] = VCOM_RxBuffer[s0_index];
        source[1] = VCOM_RxBuffer[s1_index];

        if (source[0] == 0x0D && source[1]==0x0A)
        {
          status = OK;
          break;
        }

        if (s1_index >= VCOM_RxLength)
        {
          status = UNEXP_END_ERR;
          break;
        }

        msg.data[s0_index] = source[0];
        msg.len++;
        s0_index += 1;
        s1_index = s0_index + 1;        
    }
    
    VCOM_RxData   = 0;
    VCOM_RxBuffer = NULL;
    VCOM_RxLength = 0;  

    if(OK == status){
        if(memcmp(&msg.data[0],"test",4)==0){
            vcom_test_analysis(&msg.data[0],msg.len);
            return;
        }
    }
    
   
}