#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205i_chip_watch.py —— 用 SWD 直读芯片真实状态，验证「WinApp 点击到底有没有落地」（T205 定案）

为什么必须这一步
----------------
猛哥 2026-11-04 的反驳很硬，两条都指向「下行其实是通的」：

    ① 「START 后有数据上行，再点 STOP 应该不会成功才对」
    ② 「点监护/诊断按钮后，状态栏也会同步生效」

而我的脚本在**任何**收发模型下都只有 16.7%。两边必有一边错。

WinApp 侧已排除「乐观高亮」这条解释：`Form1.cs:1809-1812` 明确在**下一帧**就用 STA 顶回去，
`SendCommand` 也是单发无重试（`:746-772`、`:1120-1145`）。点完若不跳回，就说明命令真的落地了。

所以只剩一种判法：**不看串口、不看 UI，直接读芯片内存**。

读什么（IAR map：`demo_ads1293.map`）
-------------------------------------
    f64             0x20000538   (912 B, `ads1293.o`)
    f64.notch_on    0x200008C0   ← 陷波真实开关（0 关 / 1 开）
    f64.mode        0x200008C1   ← 工况真实档位（0 诊断 / 1 监护）
    comm_tx_frames  0x200010A8   ← 上行帧累计数，**用它判断流在不在跑**

地址已交叉验证：读到 `notch_on=0, mode=1` 时，串口 STA＝`0x01`，二者逐位吻合。

★ 为什么必须同时看 `comm_tx_frames`
------------------------------------
首次实测（11:39:46~11:40:52）猛哥点了 10 次，**10 次全部落地**。但那 10 次是否发生在
**RUN 态**无法事后判明 —— 而 **STOP 态下行实测是 100%**（T205 §1）。若当时是 STOP 态，
这 10/10 就不能用来否定脚本测到的 17%。

本版起每拍都读 `comm_tx_frames`，只有它**在涨（≈20/s）**时才承认当时是 RUN 态。

🛑 **本脚本只用 SWD，不打开 COM27** ⇒ 可以**与 WinApp 同时运行**，互不干扰。

用法
----
    # 然后在 WinApp 里点「陷波 开/关」各 5 次、「监护/诊断」各 5 次
    python tools/_t205i_chip_watch.py --seconds 60

怎么判
------
* 每次点击后 1~2 s 内值跟着翻 ⇒ **WinApp 的下行是通的**，我的 16.7% 是测量假象，要重做。
* 点了半天值不动 ⇒ 命令确实没到（UI 显示是乐观值），与我的测量一致。
"""
import argparse
import re
import subprocess
import sys
import time

CLI = r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"
ADDR_FLAGS = 0x200008C0      # notch_on(1 B) | mode(1 B)
ADDR_TXF = 0x200010A8        # comm_tx_frames（判断 RUN/STOP）


def read_flags() -> tuple | None:
    try:
        out = subprocess.run(
            [CLI, "-c", "port=SWD", "mode=hotplug",
             "-r32", hex(ADDR_FLAGS), "4", "-r32", hex(ADDR_TXF), "4", "-q"],
            capture_output=True, text=True, timeout=30).stdout
    except Exception as exc:
        print(f"  [warn] SWD 失败：{exc}")
        return None
    ws = re.findall(r"0x[0-9A-Fa-f]{8}\s*:\s*([0-9A-Fa-f]{8})", out)
    if len(ws) < 2:
        return None
    word = int(ws[0], 16)
    return ((word & 0xFF), ((word >> 8) & 0xFF), int(ws[1], 16))   # notch, mode, tx_frames


def main() -> int:
    ap = argparse.ArgumentParser(description="SWD 直读 f64.notch_on / f64.mode ＋ 判断 RUN/STOP")
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args()

    first = read_flags()
    if first is None:
        print("[FAIL] 读不到芯片状态 ⇒ 检查 ST-LINK 连接／是否被调试器占用")
        return 5
    notch, mode, txf = first
    t_prev, f_prev = time.monotonic(), txf
    print(f"起点：notch_on={notch}  mode={mode}  tx_frames={txf}")
    print("       （WinApp 里点『陷波 开/关』；★波形必须在跑，否则不算 RUN 态）")
    print()
    t_end = time.monotonic() + args.seconds
    changes = 0
    while time.monotonic() < t_end:
        time.sleep(1.0)
        got = read_flags()
        if got is None:
            continue
        n2, m2, tx2 = got
        now = time.monotonic()
        fps = (tx2 - f_prev) / max(1e-6, now - t_prev)
        f_prev, t_prev = tx2, now
        running = fps > 5.0
        tag = []
        if n2 != notch:
            tag.append(f"陷波 {notch}→{n2}")
        if m2 != mode:
            tag.append(f"工况 {mode}→{m2}")
        if tag:
            changes += 1
            print(f"  [{time.strftime('%H:%M:%S')}] 变化 #{changes}: {'、'.join(tag)}"
                  f"   {'RUN' if running else 'STOP'}  {fps:5.1f} f/s")
            notch, mode = n2, m2
        else:
            print(f"  [{time.strftime('%H:%M:%S')}] 无变化  notch={n2} mode={m2}"
                  f"   {'RUN' if running else 'STOP'}  {fps:5.1f} f/s")

    print()
    print(f"共观察到 {changes} 次真实变化。")
    print("🛑 判读前提：只有标注 RUN 的那几拍才算数 —— STOP 态下行本来就是 100%。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
