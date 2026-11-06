#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t209d_alive_check.py —— STM32 死机体检（四层判据，零改固件）

为什么要分四层
--------------
「死机」是个含糊词，至少含四种完全不同的故障，判据各不同：

    ① 内核彻底停摆（halt / 深睡 / 关中断后 WFI）
       ⇒ `DWT->CYCCNT` 不涨（它按内核周期自增，与代码路径无关；halt 时冻结）
       🛑 但 CYCCNT 涨**不等于没死机**：卡在 HardFault 的 while(1) 里也照样涨。

    ② 卡在异常里（HardFault / BusFault / 某 IRQ 死等）
       ⇒ `SCB->ICSR.VECTACTIVE` ≠ 0（0 = 线程模式）
       ⇒ `SCB->CFSR` / `SCB->HFSR` 非 0 = 上电以来出过并且没被软件清掉
       ★这是判「死机」最硬的一个数，且 halt 时读到的就是 halt 那一刻的值。

    ③ 主循环卡死（卡在某个 while 等标志 / 死等 SPI）
       ⇒ **现有固件没有主线心跳变量**，本脚本判不了。
          要么在 `demo.c:136` 的 `while(1)` 里加一行 `g_loop_cnt++`，
          要么用 GDB 多次采样 PC（本机没装 gdbserver/pyOCD）。
       ⚠ CYCCNT 涨 + VECTACTIVE=0 也有可能主循环停着（例如卡在 SysTick 之外的
         硬件轮询里）—— 这一层必须靠心跳变量，别自欺。

    ④ 程序在跑但功能死了（比如 UART 接收链断了）
       ⇒ CYCCNT 在涨、VECTACTIVE=0，但 `comm_rx_events` / `comm_rx_loss` 不动
       ★2026-11-05 下行不通就是这一种 —— 所以「下行不通」不是「STM32 死机」。

另外三个附加判据
----------------
    `RCC->CSR`（0x40021094）的复位标志位 ⇒ 上一次复位的真实原因。
        PINRSTF=按了复位键 / SFTRSTF=SWD 软复位 / IWDGRSTF=看门狗
        ⇒ 若 IWDGRSTF 亮 ⇒ **真的死过并被看门狗救回来**，且能顺带查出复位循环。
    `_bconvstart`（0x20001421）⇒ 业务状态（0=STOP，1=RUN）

用法
----
    python _t209d_alive_check.py
    python _t209d_alive_check.py --hold 2.0     # CYCCNT 观测间隔
"""
import argparse
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CLI = r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"

# ---- RAM 符号（`arm-none-eabi-nm demo_ads1293.elf`，2026-11-04 构建） ----
RAM_BASE = 0x200010A8
RAM_SPAN = 0x400
RAM_OFF = {
    "tx_frames":  0x200010A8 - RAM_BASE,
    "tx_drops":   0x200010AC - RAM_BASE,
    "rx_events":  0x200011B4 - RAM_BASE,
    "bconvstart": 0x20001421 - RAM_BASE,
    "rx_loss":    0x20001430 - RAM_BASE,
}

UWTICK    = 0x200008F8
CYCCNT    = 0xE0001004        # DWT->CYCCNT，demo.c:88 起的
SCB_BASE  = 0xE000ED00
RCC_CSR   = 0x40021094

CFSR_BITS = [
    (0,  "IACCVIOL 取指违例"), (1, "DACCVIOL 取数违例"),
    (3, "MUNSTKERR 出栈失败"), (4, "MSTKERR 入栈失败"), (7, "MMARVALID MMFAR有效"),
    (8, "IBUSERR 取指总线错"), (9, "PRECISERR 精确总线错"),
    (10, "IMPRECISERR 非精确总线错"), (11, "UNSTKERR 出栈总线错"),
    (12, "STKERR 入栈总线错"), (13, "LSPERR 浮点延迟"), (15, "BFARVALID BFAR有效"),
    (16, "UNDEFINSTR 未定义指令"), (17, "INVSTATE 非法状态"),
    (18, "INVPC 非法PC"), (19, "NOCP 无协处理器"),
    (20, "STKOF 栈溢出"), (24, "UNALIGNED 非对齐访问"), (25, "DIVBYZERO 除零"),
]
HFSR_BITS = [(1, "VECTTBL 取向量失败"), (30, "FORCED 由下级故障升级"), (31, "DEBUGEVT 调试事件")]
CSR_BITS = [
    (24, "FWRSTF 选项字节加载复位"), (25, "OBLRSTF 选项字节失效复位"),
    (26, "PINRSTF 外部复位引脚"), (27, "BORRSTF 欠压复位"),
    (28, "SFTRSTF 软件复位（SWD/NVIC）"), (29, "IWDGRSTF 独立看门狗"),
    (30, "WWDGRSTF 窗口看门狗"), (31, "LPWRRSTF 低功耗管理器复位"),
]


def _cli_read(addr, n):
    path = Path(tempfile.gettempdir()) / f"_alive_{addr:08X}.bin"
    # 🛑 不能 text=True：进度条混 GBK 字节（0xB1），utf-8 解码会抛
    r = subprocess.run([CLI, "-c", "port=SWD", "mode=hotplug",
                        "-u", hex(addr), hex(n), str(path)],
                       capture_output=True, timeout=90)
    txt = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    if "Data read successfully" not in txt:
        tail = [l for l in txt.strip().splitlines() if l.strip()]
        raise RuntimeError((tail[-1] if tail else txt.strip())[:140])
    return path.read_bytes()


def rd32(addr):
    return struct.unpack_from("<I", _cli_read(addr, 4), 0)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=float, default=1.5, help="CYCCNT 观测间隔（秒）")
    a = ap.parse_args()

    try:
        ram = _cli_read(RAM_BASE, RAM_SPAN)
    except Exception as e:
        print(f"[FAIL] 读不到 STM32：{e}")
        print("       检查 ST-Link 是否插着（`STM32_Programmer_CLI -l` 能看到 ST-LINK 段）")
        return 2

    tick0 = rd32(UWTICK)
    cyc0 = rd32(CYCCNT)
    scb = _cli_read(SCB_BASE, 0x40)
    csr = rd32(RCC_CSR)
    time.sleep(a.hold)
    tick1 = rd32(UWTICK)
    cyc1 = rd32(CYCCNT)

    icsr = struct.unpack_from("<I", scb, 0x04)[0]
    cfsr = struct.unpack_from("<I", scb, 0x28)[0]
    hfsr = struct.unpack_from("<I", scb, 0x2C)[0]
    mmfar = struct.unpack_from("<I", scb, 0x34)[0]
    bfar = struct.unpack_from("<I", scb, 0x38)[0]
    vect = icsr & 0x1FF
    d_tick = (tick1 - tick0) & 0xFFFFFFFF
    d_cyc = (cyc1 - cyc0) & 0xFFFFFFFF

    print(f"观测间隔 {a.hold:.1f} s")
    print(f"  DWT->CYCCNT : {cyc0:>12d} -> {cyc1:>12d}   Δ{d_cyc}"
          f"   ({d_cyc / a.hold / 1e6:.1f} Mcyc/s)")
    print(f"  uwTick      : {tick0:>12d} -> {tick1:>12d}   Δ{d_tick}")
    print(f"  VECTACTIVE  : {vect}"
          + ("   ← 线程模式（正常代码）" if vect == 0 else f"   ← 卡在异常号 {vect}"))
    print(f"  CFSR        : 0x{cfsr:08X}")
    print(f"  HFSR        : 0x{hfsr:08X}")
    if cfsr & (1 << 7):
        print(f"  MMFAR       : 0x{mmfar:08X}")
    if cfsr & (1 << 15):
        print(f"  BFAR        : 0x{bfar:08X}")
    print(f"  RCC->CSR    : 0x{csr:08X}")
    print(f"  bconvstart  : {struct.unpack_from('<I', ram, RAM_OFF['bconvstart'])[0]}")
    print(f"  comm        : tx_frames={struct.unpack_from('<I', ram, RAM_OFF['tx_frames'])[0]} "
          f"tx_drops={struct.unpack_from('<I', ram, RAM_OFF['tx_drops'])[0]} "
          f"rx_events={struct.unpack_from('<I', ram, RAM_OFF['rx_events'])[0]} "
          f"rx_loss={struct.unpack_from('<I', ram, RAM_OFF['rx_loss'])[0]}")

    print("\n" + "=" * 68)
    core_alive = d_cyc > 1000
    print(f"层① 内核执行   : {'✅ 在取指执行' if core_alive else '❌ 停摆（halt/深睡/WFI/真死循环）'}")
    if vect == 0:
        print("层② 异常状态   : ✅ 线程模式，当前没卡在异常里")
    else:
        print(f"层② 异常状态   : ❌ 当前卡在异常 {vect}"
              + ("（HardFault）" if vect == 3 else ""))
    for b, name in CFSR_BITS:
        if cfsr & (1 << b):
            print(f"        CFSR.{b}: {name}")
    for b, name in HFSR_BITS:
        if hfsr & (1 << b):
            print(f"        HFSR.{b}: {name}")
    if cfsr == 0 and hfsr == 0:
        print("        且 CFSR/HFSR 全 0 ⇒ 上次复位以来没发生过任何故障")
    print("层③ 主循环     : ⚠ 现有固件无心跳变量，本脚本判不了（见文件头）")
    print(f"层④ 业务/通信  : {'采集 RUN 中' if struct.unpack_from('<I', ram, RAM_OFF['bconvstart'])[0] else 'STOP 态'}"
          f"（rx_events/rx_loss 的增量才说明 UART 收链活不活）")
    print("\n复位原因（RCC->CSR）：")
    any_rst = False
    for b, name in CSR_BITS:
        if csr & (1 << b):
            print(f"        {name}")
            any_rst = True
    if not any_rst:
        print("        （所有标志都被清过，无从判断）")
    if csr & (1 << 29):
        print("       🛑 IWDGRSTF 亮 ⇒ **真的死过、被看门狗救回来**，优先查这个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
