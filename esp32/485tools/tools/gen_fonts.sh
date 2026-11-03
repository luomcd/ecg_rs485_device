#!/usr/bin/env bash
# ============================================================
#  CardioLite 中文子集字库生成
#
#  为什么要子集：完整中文字体几 MB，而本工程只用到 60 来个汉字。
#  lv_font_conv 按「字符集 + 字号」裁剪，每个尺寸约 12~18 KB。
#
#  字符集的来源（缺一个按钮就会显示方块，加字时两边都要改）：
#    需求 v1.2 §7 的字表  +  §8 各状态的按钮/覆盖文案  + 中文标点
#
#  用法:
#    ./tools/gen_fonts.sh              # 生成 10/12/13/14 四个尺寸
#    ./tools/gen_fonts.sh 13           # 只生成 13 px
#
#  依赖:
#    node + lv_font_conv 1.5.3（只产 LVGL 8 格式，正好配 LVGL 8.4）
#    字体: C:/Windows/Fonts/simhei.ttf（黑体，单文件 TTF；msyh/simsun 是 .ttc 集合，不适用）
# ============================================================
set -euo pipefail

NODE_DIR="C:/Users/luom/.workbuddy/binaries/node/versions/22.22.2-6"
LVFC="C:/Users/luom/.workbuddy/binaries/node/workspace/node_modules/lv_font_conv/lv_font_conv.js"
SRC_FONT="C:/Windows/Fonts/simhei.ttf"
OUT_DIR="C:/work2026/CardioLite/cardiolite-ecg/main/ui/font"

# ---- 字符集：需求 §7 字表 + §8 文案 + 标点 ----
# 组词便于核对（去重由 lv_font_conv 负责）：
#   按钮主行/副行: 开始 停止 采集 冻结 继续 清屏 缓冲 画面 模式 诊断 监护
#                  陷波 开关 通道 回放 播放 暂停 重播 记录
#   角标/覆盖:     心率 导联 窗口 无数据 等待 已 点 恢复 单 文件 画布
#   对话框:        删除 取消 制（录制数据）将被 不 可
#   SD 相关:       请 插入 含 的 卡
#   raw853 横幅:   时 基 未 经 标 定
#   状态词:        中（播放中）完（已播完）知（未知）
# ⚠ 漏字的表现是「方块」。加文案前先确认每个字都在这里，改完重跑本脚本。
SYMBOLS="开始停止采集冻结继续清屏缓冲画面增益走纸模式诊断监护陷波关通道回放播暂停中重记录完心率导联窗口无数据等待已点恢复单文件画布删除取消制请插入含的卡时基未经标定计将被不可知，。？！：（）「」—·、"

if [ ! -f "$SRC_FONT" ]; then
    echo "[错误] 找不到字体 $SRC_FONT" >&2
    exit 1
fi
if [ ! -f "$LVFC" ]; then
    echo "[错误] 找不到 lv_font_conv：$LVFC" >&2
    echo "       先执行: cd C:/Users/luom/.workbuddy/binaries/node/workspace && npm install lv_font_conv" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

# 支持只生成指定尺寸: ./gen_fonts.sh 13
SIZES=("$@")
if [ ${#SIZES[@]} -eq 0 ]; then
    SIZES=(10 12 13 14)
fi

echo "字符集长度（含重复，去重后约 62 个汉字 + 95 个 ASCII）: ${#SYMBOLS}"
echo ""

for SIZE in "${SIZES[@]}"; do
    OUT="$OUT_DIR/font_zh_${SIZE}.c"
    "$NODE_DIR/node.exe" "$LVFC" \
        --font "$SRC_FONT" \
        --range 0x20-0x7E \
        --symbols "$SYMBOLS" \
        --size "$SIZE" \
        --bpp 4 \
        --format lvgl \
        --lv-include "lvgl.h" \
        --lv-font-name "font_zh_${SIZE}" \
        --no-compress \
        -o "$OUT"
    SZ=$(wc -c < "$OUT")
    echo "  font_zh_${SIZE}.c  ${SZ} B"
done

echo ""
echo "完成。记得在 main/CMakeLists.txt 的 SRCS 里加入这些 .c 文件（新增尺寸时）。"
