"""S3 回放对账：抓串口 `mit-run` / `ladder-run` 的峰序列输出。

固件侧输出格式（cli_console.c + ecg_pipeline.c）：
```
BATCH-BEGIN total=1
# CardioLite S3 replay: fs=500 Hz, file=mit_105_500.bin, speed=8x, 峰号=文件内相对样本号
P 3 501 909 ...               ← 每行最多 16 个峰，文件内相对样本号
...
FILE mit_105_500.bin frames=.. samples=.. peaks=.. bpm=..
# dropped=0 speed=8 (逐位对账要求 dropped=0)
```

用法：
  python tools/s3_replay_capture.py --rec 105 --speed 8
  python tools/s3_replay_capture.py --bpm 60 --speed 4 --out test_logs/xxx.log
  python tools/s3_replay_capture.py --cmd "result-dump mit_105"   # 回传 SD 卡结果文件

判据：日志里出现 `# dropped=`（回放）或 `---- END`（回传）⇒ 收工，可以退出。
"""
import argparse
import os
import sys
import time

try:
    import serial
except ImportError:
    print("缺 pyserial（当前解释器里没有）。三选一：", file=sys.stderr)
    print("  ① 交给 tools\\py.cmd 跑（固定用 WorkBuddy 环境，包最全）：", file=sys.stderr)
    print("     tools\\py.cmd tools\\s3_replay_capture.py --rec 105 --speed 8", file=sys.stderr)
    print("  ② 给当前解释器装上：python -m pip install pyserial", file=sys.stderr)
    print("  ③ 换用 WorkBuddy 环境解释器：", file=sys.stderr)
    print("     %s\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe"
          % os.environ.get("USERPROFILE", r"C:\Users\<你>"), file=sys.stderr)
    print(f"当前解释器：{sys.executable}", file=sys.stderr)
    sys.exit(2)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_TOKENS = (b"cardiolite>", b"cardiolite")
EOF_TOKEN = b"# dropped="


def open_port(port, baud, reset=True):
    """打开串口；reset=True 时复位一次（DTR=False 保持不复位在部分板子上更稳）。

    ⚠ 本项目实测：open 本身会拉 DTR/RTS 导致复位；显式打一个 RTS 脉冲后
    立刻开始读，能拿到完整启动输出。
    🛑 `reset=False`（`--no-reset`）的用途：**批处理已登记**时，一复位就会立刻
    开跑，此时若还想发 `restart`，必须先复位过再发 —— 否则会打断正在跑的批次。
    """
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = 0.2
    ser.dtr = False
    ser.rts = False
    ser.open()
    # 🛑 大文件回传（`result-dump batch_mit` = 771 KB）必须加大接收缓冲。
    # Windows 串口驱动默认 RX buffer 只有 4 KB 左右，接收循环里但凡有一次
    # `write`+`flush` 卡几十毫秒，驱动缓冲就溢出 ⇒ **静默丢字节，不报错**
    # （实测丢过 500 连续字节：5 行 P 整块消失 + 1 行被削掉前 3 字节）。
    # 1 MB 足够装下整个批次输出，配合下面「攒够 64 KB 才 flush」双保险。
    try:
        ser.set_buffer_size(rx_size=1 << 20, tx_size=1 << 16)
    except (AttributeError, NotImplementedError, ValueError, OSError):
        pass
    time.sleep(0.2)
    ser.setDTR(False)
    if reset:
        ser.setRTS(True)
        time.sleep(0.15)
        ser.setRTS(False)
    ser.reset_input_buffer()
    return ser


def wait_prompt(ser, limit_s=60.0):
    """等 CLI 提示符出现（上电静默 ⇒ 只有提示符会打出来）。"""
    t0 = time.time()
    buf = bytearray()
    while time.time() - t0 < limit_s:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            if PROMPT_TOKENS[0] in buf:
                return True, bytes(buf)
    return False, bytes(buf)


def drain(ser, seconds):
    """丢弃若干秒内的字节。

    ⚠ 必须做：`open()` 打的 RTS 复位脉冲会让 ESP32-S3 的 USB-Serial-JTAG **重新枚举**，
    重连那几百毫秒里主机收到的常是残片（实测拿到过 `escardiolite>` 这种拼接物）。
    不清掉，残片会跟后面的命令回显搅在一起 ⇒ 命令被截断成 `ult-dump batch_mit`
    ⇒ "Unrecognized command" 然后白等到超时。"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        ser.read(4096)


def send_cmd(ser, cmd, echo_wait=1.5, tries=3):
    """发一条命令并**校验回显**，不完整就重发。

    USB-JTAG CDC 会丢字节/混残片（实测丢过 `result-d` 五个字符），盲发一次不保险。
    判据：回显里必须出现命令的第一个 token（`mit-run` / `result-dump` ...）。
    返回 (ok, echo_bytes) —— ok=True 时 echo 里可能已经含着正文数据，必须计入日志。"""
    echo = bytearray()
    for i in range(tries):
        ser.reset_input_buffer()
        ser.write((cmd + "\r\n").encode())
        ser.flush()
        t0 = time.time()
        echo = bytearray()
        while time.time() - t0 < echo_wait:
            chunk = ser.read(512)
            if chunk:
                echo += chunk
        text = echo.decode("utf-8", "replace")
        if cmd.split()[0] in text:
            return True, bytes(echo)
        print(f"    ⚠ 第 {i + 1} 次回显不完整（{text.strip()[:60]!r}），重发")
    return False, bytes(echo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM35")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--rec", type=str, default=None, help="MIT 记录号，如 105")
    ap.add_argument("--bpm", type=int, default=None, help="心率阶梯目标，如 60")
    ap.add_argument("--cmd", type=str, default=None,
                    help="直接发一条原始命令（如 'result-dump mit_105'），与 --rec/--bpm 二选一")
    ap.add_argument("--until", type=str, default=None,
                    help="自定义收尾标记（默认 mit-run/ladder-run 用 '# dropped='；--cmd 用 '---- END'）")
    ap.add_argument("--speed", type=int, default=1, choices=[1, 2, 4, 8])
    ap.add_argument("--gain", default=None,
                    help="增益：10（mm/mV）或 2x（相对 5mm/mV 的倍数）；不给则沿用当前档位")
    ap.add_argument("--out", default=None, help="日志输出路径")
    ap.add_argument("--timeout", type=float, default=900.0, help="最长抓取秒数")
    ap.add_argument("--wait-port", type=float, default=0.0,
                    help="串口被占用时最多等多少秒（每 2 s 重试一次，默认 0=不等）")
    ap.add_argument("--no-reset", action="store_true",
                    help="开端口时不打 RTS 复位脉冲（批处理已登记时用它，避免一复位就开跑）")
    a = ap.parse_args()

    if sum(x is not None for x in (a.rec, a.bpm, a.cmd)) != 1:
        print("--rec / --bpm / --cmd 必须且只能指定一个", file=sys.stderr)
        return 2

    gain_s = f" {a.gain}" if a.gain else ""
    gain_tag = f"_gain{a.gain}" if a.gain else ""

    if a.cmd is not None:
        cmd = a.cmd
        slug = "_".join(a.cmd.split()[:2]).replace("-", "").replace("/", "_")
        default_name = f"s3_{slug}.log"
    elif a.rec is not None:
        cmd = f"mit-run {a.rec}{gain_s} --speed {a.speed}"
        default_name = f"s3_mit_run_{a.rec}{gain_tag}_speed{a.speed}.log"
    else:
        cmd = f"ladder-run {a.bpm}{gain_s} --speed {a.speed}"
        default_name = f"s3_ladder_run_{a.bpm}{gain_tag}_speed{a.speed}.log"

    out = a.out or os.path.join(ROOT, "test_logs", default_name)
    # 收尾标记：回放等 "# dropped="，回传等 "# ---- END ... ----"
    until = a.until.encode() if a.until else (b"---- END" if a.cmd else EOF_TOKEN)

    print(f"[1/4] 打开 {a.port} ...")
    # 串口常被串口调试助手占着（Windows 独占总线）⇒ 可选地等它释放
    deadline = time.time() + a.wait_port
    tried_wait = False
    while True:
        try:
            ser = open_port(a.port, a.baud, reset=not a.no_reset)
            break
        except serial.SerialException as e:
            if time.time() >= deadline:
                print(f"⚠ 串口打不开：{e}")
                print(f"   若串口调试助手开着，请先关闭它（它独占串口）。")
                return 3
            if not tried_wait:
                print(f"    串口被占用，最多等 {a.wait_port:.0f}s 释放 ...")
                tried_wait = True
            time.sleep(2.0)

    print("[2/4] 丢弃 USB 重连期残片，然后等 CLI 提示符 ...")
    drain(ser, 2.0)
    ok, pre = wait_prompt(ser, 20.0)
    if not ok:
        # prompt 可能刚好被上面的 drain 清掉了 ⇒ 打个空回车催它重画一次
        ser.write(b"\r\n")
        ser.flush()
        ok, pre = wait_prompt(ser, 20.0)
    if not ok:
        print("⚠ 20 s 内没等到 `cardiolite>` 提示符，放弃（不盲发命令，免得被截断）")
        print("--- 已收到 ---")
        print(pre.decode("utf-8", "replace")[-400:])
        ser.close()
        return 3

    print(f"[3/4] 发送: {cmd}（带回显校验，最多重发 3 次）")
    sent, echo = send_cmd(ser, cmd)
    if not sent:
        print("⚠ 命令回显连续 3 次不完整，放弃（查波特率 / 接线 / USB 线）")
        print(echo.decode("utf-8", "replace")[-400:])
        ser.close()
        return 3

    t0 = time.time()
    buf = bytearray(pre) + echo
    done = until in buf          # 小文件（result-dump）可能在回显段里就已经收全
    failed = (b"Command returned non-zero" in buf
              or b"\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0" in buf)
    last_report = t0

    # 边收边写：即使超时/被中断，日志也留得下来（上一版只在末尾写盘，
    # 命令失败时白等 15 分钟且一无所获）。
    fp = open(out, "wb")
    flushed = 0
    try:
        fp.write(buf)
        fp.flush()

        while time.time() - t0 < a.timeout and not done and not failed:
            chunk = ser.read(8192)
            if chunk:
                buf += chunk
                fp.write(chunk)
                # 攒够 64 KB 才 flush：每块都 flush 会在接收循环里插磁盘 syscall，
                # 卡几十毫秒就够把串口驱动缓冲打爆 ⇒ 丢字节（见 open_port 注释）。
                if len(buf) - flushed >= 65536:
                    fp.flush()
                    flushed = len(buf)

                # 命令被拒绝 / 文件找不到 ⇒ 立即收工，别盲等
                if b"Command returned non-zero" in buf or b"\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0" in buf:
                    failed = True
                    break

                if until in buf:
                    # 再多读 1 s，把 FILE 行后面可能的数据收干净
                    t_end = time.time() + 1.0
                    while time.time() < t_end:
                        more = ser.read(4096)
                        if more:
                            buf += more
                            fp.write(more)
                    done = True
                    break
            if time.time() - last_report >= 10.0:
                last_report = time.time()
                np = buf.count(b"PROGRESS ")
                print(f"    已收 {len(buf)/1024:.1f} KB，进度行 {np} 条 ... "
                      f"({time.time()-t0:.0f}s)")
    finally:
        fp.close()

    print(f"[4/4] 抓取结束（{'命令失败' if failed else ('正常收到收尾标记' if done else '超时')}），"
          f"共 {len(buf)/1024:.1f} KB")

    # 收尾：确保固件停止回放
    try:
        ser.write(b"run-stop\r\n")
        ser.flush()
        time.sleep(0.5)
        tail = ser.read(4096)
        if tail:
            buf += tail
            with open(out, "ab") as f:      # 追加（前面已边收边写）
                f.write(tail)
    except Exception:
        pass
    ser.close()

    print(f"已写 {out}")

    # 快速摘要
    text = buf.decode("utf-8", "replace")
    n_p = text.count("PROGRESS ")
    file_lines = [l for l in text.splitlines() if l.startswith("FILE ")]
    drop_lines = [l for l in text.splitlines() if l.startswith("# dropped=")]
    print(f"进度行 {n_p} 条；FILE 行 {len(file_lines)} 条；dropped 行 {len(drop_lines)} 条")
    print("  （串口已不输出 P 峰：完整峰序列在 SD 卡的 *_result.txt 里）")
    for l in file_lines[-3:]:
        print("  " + l)
    for l in drop_lines[-3:]:
        print("  " + l)
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
