# CardioLite — ESP32-S3 心电显示终端

在 **Waveshare ESP32-S3-Touch-LCD-5（800×480）** 上实现的 ECG 显示终端：
双画布心电图、心电图纸标定、R 峰检测与心率显示，第一阶段播放 SD 卡上的录制文件。

> 需求依据：`cardiolite-ecg/docs/ECG显示终端_嵌入式UI_需求描述_v1.2_2026-10-22.md`
> 工程计划：`cardiolite-ecg/docs/CardioLite_ECG显示终端UI_实施计划_v1.0_20261023.md`
> **当前进度与交接锚点：`NOW.md`（新会话先读它）**

---

## 环境

| 项 | 值 |
|---|---|
| IDF | **ESP-IDF v6.1.0** |
| 图形库 | LVGL 8.4 + `espressif/esp_lvgl_adapter` 0.5.3 |
| 芯片 | ESP32-S3-WROOM-1-**N16R8**（16 MB flash / 8 MB OCT PSRAM） |
| 屏 | 800×480 RGB565，ST7262，**pclk 16 MHz**，三缓冲 |
| 触摸 | GT911 @ I2C 0x5D（SDA=GPIO8 / SCL=GPIO9） |
| IO 扩展 | CH422G（0x24 写 / 0x38 读）管 TP_RST / 背光 / LCD_RST / SD_CS |

## 目录

```
.
├── cardiolite-ecg/     主工程（main/ = 源码，docs/ = 需求·计划·仿真稿）
├── bsp_ws_lcd5/        共享板级驱动（**必需**，cardiolite-ecg 通过 EXTRA_COMPONENT_DIRS 引用）
├── lcd-bringup/        硬件点屏验证工程
├── tools/              构建入口 + 字库/数据生成 + 验证工具链
├── test_logs/          各阶段测试报告与原始日志（项目的证据链）
└── NOW.md              交接锚点
```

## 构建

```bash
# ⚠ 必须走 tools/idf.sh —— Git Bash 注入的 MSYSTEM 会让 idf.py 静默空跑
./tools/idf.sh -C cardiolite-ecg build
./tools/idf.sh -C cardiolite-ecg -p COM35 flash
```

首次构建会自动下载 `managed_components/`（LVGL、esp_lvgl_adapter 等，约 166 MB），
版本由 `main/idf_component.yml` 声明、`dependencies.lock` 锁定。

## 未入库的内容及恢复方式

| 未入库 | 原因 | 怎么恢复 |
|---|---|---|
| `cardiolite-ecg/build/` | 构建产物（约 230 MB） | `./tools/idf.sh -C cardiolite-ecg build` |
| `cardiolite-ecg/managed_components/` | 组件依赖（约 166 MB） | 首次构建自动下载 |
| `cardiolite-ecg/sdcard/` | 拷卡用测试数据 | `tools/gen_dual_testdata.py` 重新生成；或用自己的录制 |
| `mit_bih/` | MIT-BIH 数据集（约 100 MB） | `python tools/mitbih_fetch.py` 重新下载 |
| `.workbuddy/` | 项目本地记忆 | 不需要；关键结论已提炼进 `NOW.md` 与 `docs/` |
| `ESP32-S3-Touch-LCD-5/` | 厂家资料 PDF/原理图/demo（几十 MB） | Waveshare 官网该型号「资料下载」，**注意选 800×480 版本**。<br>引脚与时序的**事实出处**见 `cardiolite-ecg/docs/BSP引脚与时序出处_20261026.md` |

## 关键文档

| 文档 | 内容 |
|---|---|
| `NOW.md` | **交接锚点**：当前进度、下一步、已定案口径、踩坑清单 |
| `cardiolite-ecg/docs/…需求描述_v1.2…md` | UI 需求 |
| `cardiolite-ecg/docs/…实施计划_v1.0…md` | S0~S10 阶段划分与闸门 |
| `cardiolite-ecg/docs/ui-mockup.html` | 界面仿真稿（与实机一致） |
| `cardiolite-ecg/docs/BSP引脚与时序出处_20261026.md` | 每条引脚/时序事实出自哪份 PDF、怎么验证的 |
| `test_logs/README.md` | 各阶段报告索引 + 存疑登记（Q1~Q6） |
| `test_logs/CardioLite_QRS验证方案_心率阶梯与MIT-BIH_20261026.md` | QRS 检测器验证方法与首批结果 |

## 验证工具链（`tools/`）

| 脚本 | 用途 |
|---|---|
| `idf.sh` | **构建唯一入口**（清 MSYSTEM） |
| `gen_fonts.sh` / `check_font_chars.py` | 中文子集字库生成 / 缺字检查 |
| `mitbih_fetch.py` | MIT-BIH 分块并行下载（断点续传） |
| `mitbih_prep.py` | 360 Hz → 500 Hz 重采样 + 封成 T101 帧 |
| `mitbih_eval.py` | AAMI 口径评估（±150 ms、Se/+P/DER、排除首尾 5 s） |
| `ecg_batch_eval.py` | 解析固件批处理串口输出 |
| `make_sdcard_batch.py` | 生成 SD 卡批处理载荷 + 清单 |
| `gen_dual_testdata.py` / `gen_rp_selftest_data.py` | 双通道长数据 / 内嵌自检样本合成 |
| `measure_rpeak_dot.py` / `check_rpeak_localization.py` | 实机照片量测 / 识别准确率核查 |
| `rpeak_ref/` | C# 参考夹具（链接上位机的 `HeartRateDetector.cs`） |

## 已知坑（详见 `NOW.md` 与各报告）

1. **构建必须走 `tools/idf.sh`**（否则 `idf.py` 静默空跑）
2. **改 `sdkconfig` = ccache 全 miss = 全量重编 40~50 min**
3. **帧率对增益极敏感**：gain5 约 13.6 FPS、gain20 约 9.7 FPS；报性能数字必须带增益
4. **跨核只传相对量，不要传绝对索引**（否则换文件/清屏后标记永久错位）
5. **"被遮挡就省掉不刷"在本平台的显示适配器上不成立**（会丢整段波形）
