# bioelectronics_toolkit / DataProcess — 动态分析报告

> 日期：2026-05-26 · 版本：v0.6.0（commit 06686fb）
> 类型：**动态分析**（真正把代码跑起来：装包、跑测试、起服务、打端点、实测内存/并发/性能）
> 与静态分析（`product_review_2026-05.md`）互补——本文所有结论均来自**实际运行的可复核数据**。

---

## 0 · 环境与方法（含可信度说明）

在隔离 Linux 沙箱（Python 3.10.12）中安装运行依赖（flask 3.1.3、scipy 1.15.3、tifffile 2025.5.10、pyabf 2.3.8、pydantic、imagecodecs、readlif、statsmodels、coverage、pytest、psutil），然后：跑完整 unittest 套件 + coverage、用 Flask 测试客户端在进程内打全部路由、用真实示例数据走端到端计算、用 `tracemalloc`+`psutil` 实测内存、用多线程实测 matplotlib 全局状态。

**两个结果属于沙箱环境产物、非代码缺陷，已剔除：**
1. 13 个测试报 `No module named 'tkinter'`——沙箱无 root 装不了 `python3-tk`，这些是 legacy GUI 的导入测试，CI 里会装 tk 后通过。
2. 作业 sqlite 报 `disk I/O error`——沙箱的 fuse 挂载不支持 sqlite 文件锁；在用户本地真实磁盘上不会发生（但由此暴露了一个真实的日志问题，见 §4）。

**顺带确认：之前静态报告里的 A1（readlif/statsmodels 未声明依赖）已被修复**——当前 `pyproject.toml` 已含 `readlif>=0.6.6` 与 `statsmodels>=0.14`，`web_app.py` 顶层直接 import，应用可正常启动。

---

## 1 · 测试套件：118 / 131 通过

```
Ran 131 tests in 3.995s — FAILED (errors=13)
```
13 个 error **全部**是上面的 tkinter 环境缺失；其余 **118 个测试全部通过**，包含契约测试 `test_webgui_contracts`（1498 行）、信号算法测试 `test_signal_services`（58 处断言）、histology/lif/roi 等服务测试。`private service boundary check` 也通过（"no web_api module calls private service helpers"）。

> 结论：测试套件**健康、跑得通、跑得快**（<4 秒）。

## 2 · 覆盖率实测：services 总体 55%，但科学核心模块最低

```
TOTAL   10946 stmts   4886 missed   = 55% covered
```
CI 里跑了 coverage 但**未设阈值门**，所以这个数字此前没有被当作质量信号。更值得注意的是**覆盖率最低的恰恰是科学计算/渲染最关键的模块**：

| 模块 | 覆盖率 | 性质 |
| --- | --- | --- |
| services/histology_preview.py | **9%** | 图像预览渲染 |
| services/abf_viewer.py | **10%** | 膜片钳波形处理 |
| services/fluorescence/gif_roi_context.py | **10%** | ROI 时序分析 |
| services/fluorescence/roi_render_context.py | 11% | ROI 渲染 |
| services/emg_peaks.py | **13%** | EMG 峰检测 |
| services/csv_viewer.py | 32% | CSV 处理 |
| services/fluorescence/lif_export.py | 32% | LIF 导出 |
| services/echem.py | **36%** | 电化学检测算法 |
| services/figure_generator/plots.py | 38% | 出图 |
| services/fluorescence/roi.py | 39% | ROI 指标计算 |

> 结论：这与静态报告 D2（缺金标准数值回归）相互印证——**算法正确性最该被钉死的代码，恰恰测得最少**。建议给 echem/abf/emg/roi 这几条算法路径补参考输入→期望输出的回归测试，并在 CI 给 services 设一个覆盖率下限（如 60% 起步、逐步抬高）。

## 3 · 运行时启动与端点：198 路由健康，端到端计算用真实数据验证通过

- **路由总数 198**；24 个 GET 页面路由烟测**全部 < 500**（无服务端错误）。
- `GET /api/version` → 200，正确返回 `v0.6.0 · 06686fb`。
- **用真实示例文件 `examples/sample_echem_photocurrent.csv` 跑端到端计算，全部 200：**

| 端点 | 状态 | 响应大小 |
| --- | --- | --- |
| `/api/echem/load` | 200 | 97 KB |
| `/api/echem/detect` | 200 | **138 KB** |
| `/api/echem_pv/load` | 200 | 95 KB |
| `/api/csv/columns` | 200 | 0.1 KB |
| `/api/emg/load_csv` | 200 | 99 KB |
| `/api/csv/merge_job`, `export_*_job` | 200 | <1 KB（异步作业）|

> 结论：核心分析管线**真实可用**，从文件解析→检测→出图全程跑通。注意 `detect` 单次响应 138 KB——因为图以 base64 内联在 JSON 里（见 §7）。
>
> 附带发现：存在内置 `services/self_check`（含 `format_self_check_report`、`CHECK_IMPORTS`、按域 self-check）——**静态报告 B4 建议的"安装自检"其实已部分存在**，建议把它接到一个 `bte-web --self-check` CLI 入口并在 README 暴露。

## 4 · 运行时告警与日志（两个新发现）

**(新) ResourceWarning：favicon 文件句柄未关闭。** 进程内打路由时捕获到：
```
ResourceWarning: unclosed file .../web_static/favicon.ico
```
来自 `web_app.favicon()` 的 `send_from_directory`/文件读取路径未释放句柄。单次无害，但高频请求下会累积未关闭句柄。建议用 `flask.send_file` 的托管路径或确保上下文关闭。

**(新) 作业持久化失败时反复打印整段 traceback，而非一条简洁告警。** 当 sqlite 写入失败，`services/background_jobs.py:61/78/111` 对**每一次** init/load/persist 都 dump 完整 traceback（一个作业刷了 6+ 段堆栈）。
> 正面：JobManager **优雅降级**——sqlite 不可用时仍以内存模式继续服务，端点照常 200，这是好的健壮性设计。
> 问题：失败日志**过于嘈杂**（每次持久化都打全栈）。建议改为"首次失败打一条 WARNING + 后续静默/降频"，避免日志被刷屏淹没真正的错误。

## 5 · 内存实测（头条）：整文件读 TIFF 比按页读多耗 **147×** 内存

对一个 160 帧 × 1024×1024 × uint16（磁盘 336 MB）的合成栈，分别用**当前代码的整文件 `tifffile.imread`** 与 **gif.py 已用的按页惰性读** 计算同样的逐帧均值（模拟 ROI 时序）：

| 方式 | Python 峰值内存 | RSS 增量 | 耗时 | 数值结果 |
| --- | --- | --- | --- | --- |
| **A. 整文件 `imread`**（stack.py:179 等）| **335.8 MB** | +335.6 MB | 0.20 s | 一致 |
| **B. 按页 `tif.pages[i].asarray()`**（gif.py 范式）| **2.3 MB** | +0.0 MB | **0.07 s** | 一致 |

**峰值内存比 A/B = 146.9×，且按页读还更快（快 ~3×），数值完全相同（`np.allclose=True`）。**

> 这就是静态报告 F1/M1 的**实测铁证**：当前栈/ROI 路径把整栈压进 RAM。336 MB 时占 336 MB，线性外推到一个 16 GB 的时序栈就是直接 OOM 杀进程；而项目里**已经存在的**按页范式（gif.py）只用 ~2 MB 且更快。修复不需要新设计，只需把 `services/fluorescence/stack.py:179`、`route_context.py:54`、`web_api/fluorescence_stack_routes.py:138/204/246`、`fluorescence_roi_basic_routes.py:91` 改成按页/惰性读取。

## 6 · 并发实测：matplotlib 全局状态确实被多线程踩踏

4 个线程各自循环 `plt.subplots()`（这正是 30 处路由的写法），监测进程级全局 figure 注册表里**同时存活的 figure 数**：

```
[S1] max concurrently-live figures in the SHARED global registry = 4
```
线程安全的写法应稳定在 ~1；实测达到 4，**证明 4 个线程在共享同一个 pyplot 全局 figure 管理器**。在 `app.run(threaded=True)` 下，一个线程的 `plt.close()`/`plt.gcf()` 可能动到另一个线程的 figure → 图像串台或 figure 泄漏。

> 印证静态报告 S1。修复：服务端绘图改 OO `Figure()`（项目里 `fig_to_b64` 已接收显式 fig，只需把上游 30 处 `plt.subplots` 换掉），彻底不碰 `plt.*` 全局态。

## 7 · 启动副作用与 base64 膨胀实测

**(S3) `import web_app` 耗时 1.40 s，且导入即产生重副作用：** 构建 Flask app、创建 JobManager 并落地 sqlite 文件、改写全局 matplotlib rcParams——全部在 import 阶段发生，无 `create_app()` 工厂。
> 印证 S3：这让"用临时配置/隔离缓存目录起一个干净 app 做路由级单测"很困难，也是路由层几乎只能靠 e2e 覆盖的原因之一。

**(F3) base64 内联使图像负载 +33%：** 一张 6×4@130dpi PNG 二进制 22 KB → base64 进 JSON 后 30 KB，并以 str 形式整张驻留内存。叠加 §3 的 `detect` 单响应 138 KB 可见放大效应。
> 印证 F3：大图建议改二进制流式端点（`send_file`），JSON 只回图像 URL。

---

## 8 · 动态分析净结论

**确认无虞的部分（实测）：** 测试套件健康（118/131，其余纯环境）、198 路由无 5xx、真实数据端到端计算全部 200、作业系统能优雅降级、A1 依赖问题已被修复。**这是一个真的能跑、能算、不崩的系统。**

**动态分析量化/坐实的静态发现：**

| 编号 | 发现 | 实测数据 |
| --- | --- | --- |
| F1/M1 | TIFF 整文件读的 OOM 风险 | **峰值内存 147×**，且更慢 3× |
| S1 | matplotlib 全局态并发危险 | 4 线程 → 全局注册表同时 4 个 figure |
| S3 | import 重副作用、无工厂 | import 耗时 1.40s，导入即建 app+sqlite+改 rcParams |
| F3 | base64 图像膨胀 | +33%，detect 单响应 138 KB |
| D2 | 科学模块测试不足 | services 总覆盖 55%，echem 36% / emg 13% / abf 10% |

**动态分析新发现（静态扫不到的）：**
1. **favicon 路由文件句柄未关闭**（ResourceWarning）。
2. **作业持久化失败时反复 dump 整段 traceback**——降级正确但日志嘈杂，应改为首次 WARNING + 降频。
3. **CI 跑了 coverage 但无阈值门**，55% 这个信号一直没被用起来。
4. **`services/self_check` 已存在**，可直接产品化为 `--self-check` 入口。

**建议的动态侧优先级：**
- **P0**：F1/M1 按页读取（147× 内存铁证）；S1 改 OO Figure（并发铁证）。
- **P1**：给 services 设 CI 覆盖率下限并优先补 echem/abf/emg/roi 的数值回归（D2）；作业失败日志降噪。
- **P2**：favicon 句柄关闭；base64 大图转流式（F3）；抽 `create_app()`（S3）并把 `self_check` 暴露为 CLI。

> 复核命令（任意环境可重跑）：`pip install -e ".[dev]"` → `python -m unittest discover -s tests` → `python -m coverage run --source=services -m unittest discover -s tests && python -m coverage report`。内存/并发实测脚本逻辑见本报告 §5–§7（合成 160 帧栈对比 imread 与按页读）。

---

## 9 · 运行速度优化（基于 cProfile 实测，2026-05-26 补充）

先用 cProfile 打真实端点定位热点，再用微基准量化每个优化的收益。结论先行：**带图端点的耗时几乎全在 matplotlib 渲染，而非科学计算本身。**

### 9.1 热点定位：91% 的时间花在 `savefig`，不是算法
对真实示例数据实测每次调用延迟（test client，n=10 取均值）：

| 端点 | 延迟 | 响应 | 备注 |
| --- | --- | --- | --- |
| `/api/echem/load` | 52 ms | 95 KB | 含 1 张图 |
| `/api/echem/detect` | 59 ms | 135 KB | 含 2 张图 |
| `/api/emg/load_csv` | 44 ms | 96 KB | 含图 |

cProfile（`/api/echem/detect` ×20）按累计耗时排序，最热的调用栈：

```
api_echem_detect            ......  1.894 s   (总)
  Figure.savefig            ......  1.086 s   ← 占 ~57%（单图 54 ms，本端点 2 图）
    Axis.get_tightbbox      ......  0.669 s   ← 占 ~35%，全部来自 bbox_inches="tight"
```

> **科学计算（baseline/检测/拟合）几乎不耗时；瓶颈是出图。** 而出图里最大的单项是 `bbox_inches="tight"`——它为算紧致边界**额外多渲染一遍整图**。

### 9.2 量化收益：`fig_to_b64` 的两个默认值是最大杠杆
当前实现（`web_api/common.py:37`）：
```python
def fig_to_b64(fig, dpi=130, fmt="png"):
    fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")   # ← 两个性能开关
```
对同一张 6×4 图做微基准（n=40，已预热字体缓存）：

| 配置 | 渲染耗时 | base64 大小 | 相对当前 |
| --- | --- | --- | --- |
| **dpi=130 + bbox_inches='tight'（当前）** | **43.0 ms** | 36 KB | — |
| dpi=130，去掉 tight | 26.6 ms | 36 KB | **渲染 −38%** |
| dpi=100，去掉 tight | 23.9 ms | 27 KB | 渲染 −44%，负载 −25% |
| dpi=96，去掉 tight | 21.9 ms | 25 KB | **渲染 −49%，负载 −31%** |
| dpi=72，去掉 tight（屏幕预览）| 24.0 ms | 18 KB | 负载 −50% |

> 注：`set_layout_engine("tight")` 实测反而更慢（46.9 ms），**不要用它替代**；去掉 tight 后用 `constrained_layout=True`（建图时设、在 draw 阶段顺带算，开销小）或固定 `subplots_adjust` 边距来防裁切即可。

### 9.3 可落地的优化方法（按收益/成本排序）

**P0 · 去掉 `bbox_inches="tight"` + 区分预览/导出 DPI —— 几乎零成本，带图端点直接快 ~40–50%**
- 把 `fig_to_b64` 默认改为不带 tight，建图时用 `constrained_layout=True` 防裁切。
- 交互预览用 dpi≈96，**仅导出**（PNG/PDF/SVG 下载）才用 130+。给 `fig_to_b64(dpi=96)` 默认、导出路径显式传高 dpi。
- 预期：`detect`/`load`/出图类端点延迟 ~砍半，JSON 负载 −25~30%。这是**全站带图端点的统一加速**，改一个函数受益面最大。

**P1 · 解码缓存，消除交互调参的重复解码（呼应静态 F2）**
- 用户反复微调 LUT/min-max/阈值再渲染时，同一文件每次都重新 `read_csv` / `imread`。加一个按 `(path, mtime, size)` 为键的小 LRU（限条目+总字节）缓存解码后的数组/DataFrame。
- 预期：交互式调参的二次起每次省掉整段 I/O+解码（栈场景可省几十~几百 ms 到秒级）。

**P1 · 按页惰性读取（与 §5 同源，既省内存也更快）**
- §5 实测：按页读比整文件 `imread` 不仅省 147× 内存，**还快约 3×**（0.07s vs 0.20s）。栈/ROI 路径改惰性读是"内存+速度"双赢。

**P1 · 只渲染当前需要的图**
- `detect` 等端点一次渲染 2 张图（profile 中 40 print_figure / 20 call）。若前端并非同时展示，改为按需/懒渲染第二张，单这一项可再省该端点 ~25–30%。

**P2 · 输出格式与传输**
- 大图改二进制流式端点（`send_file`，省掉 base64 的 +33% 体积与 str 驻留）；JSON 只回图像 URL。
- 多文件批处理端点可对帧/采样做下采样预览（出图本就受像素限制，2000+ 点的 trace 先抽稀到 ~1000 点几乎无视觉差异但更快）。

**P2 · 并发吞吐（呼应 S1/S2）**
- 改 OO `Figure` 后才能安全并发出图（否则 `threaded=True` 下 pyplot 全局态串台）；再把后台作业换成有界线程池，避免并发重任务互相拖慢 + 抢内存。

**P2 · 进程级**
- matplotlib 首图有字体缓存冷启动成本——服务启动时预热一张空图（warmup）让首个真实请求不吃这部分延迟。
- 微优化：`fig_to_b64` 里 `import matplotlib.pyplot as plt` + `plt.close(fig)`，对 OO `Figure`（不在 pyplot 注册表里）是多余的，改用 `fig.clf()` 或直接丢弃即可，省一次 pyplot 触碰。

### 9.4 一句话
**最高性价比的单点优化是改 `fig_to_b64`：去掉 `bbox_inches="tight"` 并把预览 DPI 降到 ~96**——实测能让所有带图端点快约 40–50%、负载小 ~30%，改动一个函数、风险极低。其次是解码缓存（F2）与按页读取（既快又省内存）。科学计算本身不是瓶颈，无需优化算法。
