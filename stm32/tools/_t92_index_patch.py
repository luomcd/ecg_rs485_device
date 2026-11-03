# -*- coding: utf-8 -*-
"""T92：往 tools/INDEX.md 追加三条（报告清单 / 脚本清单 / 日报对照）＋ 续编号 T92 → T93。

★ 纪律（memory-distill §8.8）：
  ① **逐条顺序校验**（后一处可能依赖前一处结果）；
  ② 上限/唯一性断言放在写盘**之前**；
  ③ 每处打印净增量，让总账可核；
  ④ 先备份（`_bak_INDEX_pre_T92.md`）。
"""
import io
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(HERE, 'INDEX.md')
BAK = os.path.join(HERE, '_bak_INDEX_pre_T92.md')

# ---- 三条新增文本（末尾都带 `｜`，与既有条目风格一致）--------------------------------
REP_REPORT = (
    "**`T92_监护诊断落码与上板作业卡_2026-09-22.md`（★**监护/诊断首次上板：落码与执行作业卡**"
    "（🛑**纯准备，无源码改动**；明天照做即可）：**§0 冻结参数**＝A+B 第 15 节 `LP2@250`→**`LP2@45`**"
    "（`0.02211456900210405, 0.0442291380042081, 0.02211456900210405, -1.5374882597180526, 0.6259465357264689`）、"
    "**2 阶**、命令 **`test mode [diag|mon]`**、策略 **B**；**§2 四处可粘贴代码＋精确锚点**"
    "（`coeffs.h` 末尾加 `ECG_SECTION_TOPMOST`/`ECG_MODE_*`；`ecg_filter.h` f64 **+1 字段 `mode`（吃 padding ⇒ 912 B）**；"
    "`ecg_filter.c` 加 `ECG_MODE_MON_TOP_F64`＋`set_mode`/`mode_is_mon`＋init 一行；`vcomcmd.c` 加 `test mode` 块）；"
    "**§3 门禁三道**（`_t58_verify_struct` → `_t84_verify_land` → `_fw_build`）；"
    "**§4 上板序列**（`afe start`→`notch off`→`test mode` 回显→`mode mon`→抓 0.67/5/40 Hz）"
    "＋**§4.1 诊断回归**（150 Hz 应仍 **75.64%**）；**§5 mon vs diag 实算对照（整机／notch OFF）**："
    "**40 Hz 77.20% vs 98.23%**｜**150 Hz 5.68% vs 75.58%（13× ⇒ 判「切没切成功」最快）**｜50 Hz 61.05%｜100 Hz 16.49%｜0.67 Hz 99.99%；"
    "**§6 ★9 条坑**（🛑 `biquad_df2t_f64_set()` **清零 d1/d2** ⇒ 预置顺序「反解 v → set → 预置」不可换；"
    "🛑 MON 常量**不能叫 `ECG_FILTER_COEFFS_*`**（结构门禁会误抓成第 10 张表）；"
    "AFE ＝ **三级 5 阶且输入率递减**（漏阶次会高估余量 ~1.4 pp）；`raw853` 下须 `--fs 853.2423`）；"
    "**§7 回退三级**；**§8 不做清单**）**｜")

REP_SCRIPT = (
    "**`_t92_mon_measure.py` — ★T92 监护/诊断判读入口（自包含；`--selftest` 离线端到端自证已通过）**："
    "直读 A+B 系数 ＋ 自做「只换第 15 节」＋ 自建 **三级 5 阶·输入率递减** AFE 模型 ＋ 判限判决；"
    "参数 `--mode diag|mon`、`--ref`（监护 **5 Hz**／诊断 10 Hz）、`--fs`（raw853 时必给 853.2423）、`--selftest`；"
    "★ 替代 `_t70_fr_measure.py` 的理由：后者依赖 `_t69_fr_forecast.py` 的**旧宏解析**（A+/A+B 得 `SECTIONS=None`）"
    "且表达不了「只换一节」｜")

REP_DAILY = (
    "**§27 T92 落码/上板作业卡 ＋ 判读脚本（🛑 无源码改动）**："
    "作业卡＝四处可粘贴代码＋门禁三道＋上板序列＋**mon/diag 实算对照**（40 Hz 77.20/98.23%、"
    "**150 Hz 5.68/75.58% ＝ 13×**）；判读脚本 `--selftest` **通过**（40 Hz 合成 76.674% vs 设计 77.200%，差 −0.526 pp ≤ ±1.0）；"
    "★ 两条新踩的坑（`biquad set()` 清零 d1/d2 ⇒ 预置顺序；MON 常量命名须避开结构门禁）｜")


def main():
    s = io.open(P, encoding='utf-8').read()
    n0 = len(s)
    if not os.path.exists(BAK):
        shutil.copyfile(P, BAK)
        print('已备份 → %s' % os.path.basename(BAK))

    reps = [
        # ① 续编号 T92 → T93
        ('**INDEX 续编号 → T92**', '**INDEX 续编号 → T93**', '续编号'),
        # ② 报告清单：T92 条插在 T91 条之前（时间倒序）
        ('**）**｜**）**｜**`T91_监护诊断可选_最小改动评估_2026-09-21.md`',
         '**）**｜**）**｜' + REP_REPORT + '**`T91_监护诊断可选_最小改动评估_2026-09-21.md`',
         '报告条'),
        # ③ 脚本清单：`_t92_mon_measure.py` 插在 `_t91_mode_eval.py` 之前
        ('｜**`_t91_mode_eval.py` —', '｜' + REP_SCRIPT + '**`_t91_mode_eval.py` —', '脚本条'),
        # ④ 日报对照：§27 追加在 §26 之后（该行末）
        ('改动 **~64 行**；决策点 4 条｜）',
         '改动 **~64 行**；决策点 4 条｜' + REP_DAILY + '）', '日报条'),
    ]

    for old, new, tag in reps:
        c = s.count(old)
        assert c == 1, '%s 未命中（%d 处）：%s' % (tag, c, old[:46])
        s = s.replace(old, new)
        print('  %-8s %+6d 字符' % (tag, len(new) - len(old)))

    assert 'T92' in s and 'T93' in s
    io.open(P, 'w', encoding='utf-8', newline='\n').write(s)
    print('INDEX %d → %d（%+d）｜T92 命中 %d 处｜T93 命中 %d 处'
          % (n0, len(s), len(s) - n0, s.count('T92'), s.count('T93')))


if __name__ == '__main__':
    main()
