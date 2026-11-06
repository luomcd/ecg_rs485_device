"""抓 ESP32 经 485 发到 COM27 的原始字节（T208 诊断）。

目的：把「ESP32 到底有没有把 AA 55 80 01 00 01 5D 发上总线」变成可打印的十六进制，
而不是靠串口助手的文本模式猜乱码。

用法：python probe_com27.py
前置：COM27 = USB-485 转换器，COM35 = ESP32 USB；串口助手请先关掉（会抢口）。
"""
import sys
import time

import serial


def main():
    try:
        p27 = serial.Serial("COM27", 115200, timeout=0.2)
    except Exception as e:
        print(f"[FAIL] 打不开 COM27：{e}")
        print("       → 串口助手是否还占着？关掉再跑。")
        return 2

    try:
        p35 = serial.Serial("COM35", 115200, timeout=0.2)
    except Exception as e:
        print(f"[FAIL] 打不开 COM35：{e}")
        p27.close()
        return 2

    try:
        time.sleep(0.3)
        p27.reset_input_buffer()
        p35.reset_input_buffer()

        # 唤醒 CLI（避免第一条命令被行首吞掉）
        p35.write(b"\r\n")
        time.sleep(0.5)
        p35.reset_input_buffer()

        p35.write(b"485-start\r\n")
        time.sleep(1.2)
        p35.reset_input_buffer()

        p27.reset_input_buffer()
        p35.write(b"485-send start\r\n")

        # 收 2 s（若有回显/tx 也会一起进 p35，这里不看 p35）
        buf = bytearray()
        t0 = time.time()
        while time.time() - t0 < 2.0:
            n = p27.in_waiting
            if n:
                buf += p27.read(n)
            else:
                time.sleep(0.02)

        if not buf:
            print("[结果] COM27 一无所获 —— ESP32 的字节没有上到这条总线")
            return 1

        print(f"[结果] COM27 共收到 {len(buf)} B：")
        print("       " + " ".join(f"{b:02X}" for b in buf))

        want = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x01, 0x5D])
        if want in buf:
            print("[判定] ✅ 命中 START 整帧 AA 55 80 01 00 01 5D —— ESP32 发送正常")
            return 0
        print("[判定] ⚠ 收到了字节但不是 START 整帧（看上面 hex，可能有丢位/粘包）")
        return 1
    finally:
        try:
            p35.close()
        except Exception:
            pass
        p27.close()


if __name__ == "__main__":
    sys.exit(main())
