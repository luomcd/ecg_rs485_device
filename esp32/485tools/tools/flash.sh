#!/usr/bin/env bash
# ============================================================
#  CardioLite 烧录脚本
#
#  为什么要有这个脚本（而不是直接 idf.py flash）：
#    1. 必须先清 MSYSTEM —— 否则 idf.py 静默空跑（tools/idf.sh 负责）
#    2. 烧前校验产物时间戳，防止烧到旧镜像
#    3. 自动探测串口（COM35 是历史值，换 USB 口会变）
#    4. 分区表/bootloader 没变时支持只烧 app，快 ~10 倍
#    5. 救砖用的全片擦除有二次确认
#
#  用法:
#    ./tools/flash.sh              # 自动探测串口，全烧（bootloader+分区表+app）
#    ./tools/flash.sh -p COM35     # 指定串口
#    ./tools/flash.sh -a           # 只烧 app（分区表/bootloader 未变时用）
#    ./tools/flash.sh -m           # 烧完进串口监视（Ctrl+] 退出）
#    ./tools/flash.sh -M           # 只开串口监视，不烧录
#    ./tools/flash.sh -e           # 烧前全片擦除（救砖，会清 NVS）
#    ./tools/flash.sh -n           # dry-run，只打印将执行的命令
# ============================================================
set -u

WORKSPACE='C:/work2026/CardioLite'
PROJ_DIR="$WORKSPACE/cardiolite-ecg"
IDF_SH="$WORKSPACE/tools/idf.sh"
APP_BIN="$PROJ_DIR/build/cardiolite_terminal.bin"

PORT=''
APP_ONLY=0
DO_MONITOR=0
MONITOR_ONLY=0
DO_ERASE=0
DRY_RUN=0

usage() {
    sed -n '3,20p' "$0" | sed 's/^#//'
    exit "${1:-0}"
}

while getopts ':p:amMenh' opt 2>/dev/null; do
    case "$opt" in
        p) PORT="$OPTARG" ;;
        a) APP_ONLY=1 ;;
        m) DO_MONITOR=1 ;;
        M) MONITOR_ONLY=1 ;;
        e) DO_ERASE=1 ;;
        n) DRY_RUN=1 ;;
        h) usage 0 ;;
        *) usage 1 ;;
    esac
done

say()  { printf '%s\n' "$*"; }
die()  { printf '\n[失败] %s\n' "$*" >&2; exit 1; }

# ---------- 只开监视器模式 ----------
if [ "$MONITOR_ONLY" -eq 1 ]; then
    [ -n "$PORT" ] || die "监视模式必须用 -p 指定串口，例如: ./tools/flash.sh -M -p COM35"
    say "== 打开串口监视 $PORT （Ctrl+] 退出）=="
    exec "$IDF_SH" -C "$PROJ_DIR" -p "$PORT" monitor
fi

# ---------- 1. 校验产物 ----------
[ -f "$IDF_SH" ]  || die "找不到构建入口 $IDF_SH"
[ -f "$APP_BIN" ] || die "找不到固件 $APP_BIN，请先构建：
  ./tools/idf.sh -C $PROJ_DIR build"

APP_SIZE=$(wc -c < "$APP_BIN" | tr -d ' ')
APP_MTIME=$(date -r "$APP_BIN" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo '未知')

say ''
say '=========================================================='
say ' CardioLite 烧录'
say '=========================================================='
say " 固件      : $APP_BIN"
say " 大小      : $APP_SIZE B"
say " 编译时间  : $APP_MTIME"

# 编译产物超过 2 小时则提醒（防止烧到旧镜像）
if [ -n "$(command -v date)" ]; then
    NOW=$(date +%s)
    BIN_T=$(date -r "$APP_BIN" +%s 2>/dev/null || echo "$NOW")
    AGE=$(( (NOW - BIN_T) / 60 ))
    say " 距编译    : ${AGE} 分钟前"
    [ "$AGE" -gt 120 ] && say ' ⚠ 该镜像已超过 2 小时，确认不是旧产物'
fi

# ---------- 2. 探测串口 ----------
detect_ports() {
    powershell.exe -NoProfile -Command \
        "[System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object" 2>/dev/null \
        | tr -d '\r' | grep -iE '^COM[0-9]+$' | tr '\n' ' '
}

if [ -z "$PORT" ]; then
    AVAIL=$(detect_ports)
    if printf '%s' "$AVAIL" | grep -qw 'COM35'; then
        PORT='COM35'
    else
        COUNT=$(printf '%s' "$AVAIL" | wc -w | tr -d ' ')
        if [ "$COUNT" -eq 1 ]; then
            PORT=$(printf '%s' "$AVAIL" | tr -d ' ')
        else
            say ''
            say " 可用串口  : ${AVAIL:-（未探测到）}"
            die "无法自动确定串口。请用 -p 指定，例如: ./tools/flash.sh -p COM7"
        fi
    fi
fi
say " 串口      : $PORT"

# 提示串口被占用（常见原因：VS Code 的串口监视器没关）
if [ -n "$(detect_ports)" ] && ! printf '%s' "$(detect_ports)" | grep -qw "$PORT"; then
    say " ⚠ $PORT 当前不在可用串口列表里 —— 可能设备没插或驱动未就绪"
fi

# ---------- 3. 组装命令 ----------
ARGS=""
if [ "$APP_ONLY" -eq 1 ]; then
    say ' 范围      : 仅 app（bootloader 与分区表不动）'
    ACTION='app-flash'
else
    say ' 范围      : 全烧（bootloader + 分区表 + app）'
    ACTION='flash'
fi

say '=========================================================='
say ''

if [ "$DO_ERASE" -eq 1 ]; then
    say '⚠ 已选择全片擦除：会清掉 NVS（配色/增益等配置）与 storage 分区'
    printf '确认继续？输入 yes 回车: '
    read -r ans
    [ "$ans" = 'yes' ] || die '已取消'
    if [ "$DRY_RUN" -eq 1 ]; then
        say "[dry-run] $IDF_SH -C $PROJ_DIR -p $PORT erase-flash"
    else
        "$IDF_SH" -C "$PROJ_DIR" -p "$PORT" erase-flash || die '擦除失败'
    fi
fi

CMD="$IDF_SH -C $PROJ_DIR -p $PORT $ACTION"
if [ "$DRY_RUN" -eq 1 ]; then
    say "[dry-run] $CMD"
    exit 0
fi

say ">> 执行: $CMD"
say ''
"$IDF_SH" -C "$PROJ_DIR" -p "$PORT" "$ACTION"
RC=$?

say ''
if [ "$RC" -ne 0 ]; then
    say '=========================================================='
    say ' 烧录失败。常见原因：'
    say '   1. 串口被占用 —— 关掉 VS Code 的串口监视器 / 其他串口工具'
    say '   2. 设备没进下载模式 —— 按住 BOOT 再点 RESET，松开 BOOT 后重试'
    say '   3. 选错串口 —— 用 -p 指定，先用 ./tools/flash.sh -n 看将执行什么'
    say '=========================================================='
    exit "$RC"
fi

say '=========================================================='
say ' 烧录完成，设备已自动复位。上板验收四点：'
say '   1. 背光     亮'
say '   2. 页面     浅色纸底 + CardioLite 绿标题 + 四行信息 + TOUCH TEST 按钮'
say '   3. 触摸     点 TOUCH TEST，clicks 递增，串口打 "touch OK, clicks=N"'
say '   4. 核归属   串口出现 "[probe] LVGL worker task actually runs on core 0"'
say ''
say ' 另外确认这一行（验证抗撕裂是三缓冲）：'
say '   "Creating RGB panel, frame buffers: 3"'
say '=========================================================='

if [ "$DO_MONITOR" -eq 1 ]; then
    say ''
    say "== 进入串口监视 $PORT （Ctrl+] 退出）=="
    exec "$IDF_SH" -C "$PROJ_DIR" -p "$PORT" monitor
else
    say ''
    say "查看串口日志: ./tools/flash.sh -M -p $PORT"
fi
