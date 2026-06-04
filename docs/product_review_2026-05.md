# bioelectronics_toolkit / DataProcess — 成熟产品视角审查报告

> 审查日期：2026-05-26 · 版本：v0.6.0（79 commits，git tag v0.2–v0.6）
> 目标定位：**公开开源科研工具**
> 审查维度：代码架构与可维护性 · 产品与用户体验 · 开源/发布成熟度 · 科学严谨与可复现性

---

## 一、总体评价

这是一个**工程成熟度明显高于一般科研代码**的项目。它已经具备了很多"成熟产品"的特征：分层的 `services/` ↔ `web_api/` 架构、用 ratchet 脚本守住分层边界、三版本 CI 矩阵 + Playwright e2e + Windows 冒烟、全量 Pydantic 请求校验、隐私友好的本地遥测、运行清单（run manifest）、ADR / 设计 token / 维护者上手文档、issue/PR 模板、dependabot、CITATION.cff。

也正因为底子好，剩下的问题大多不是"缺基础设施"，而是**"距离一个让外部研究者 clone 即用、可引用、可复现的公开项目还差最后一公里"**。下面按四个维度展开，每条都标注了证据、建议和优先级（P0 必做 / P1 应做 / P2 可做）与工作量估计。

| 维度 | 现状评分 | 一句话结论 |
| --- | --- | --- |
| 代码架构与可维护性 | 良好 | 分层清晰、有边界守卫；主要风险是 god-context、未声明依赖、legacy 双维护负担 |
| 产品与用户体验 | 良好 | Web 为权威形态、有示例与命令面板；缺移动端兜底与"我是谁/给谁用"的入口叙事 |
| 开源/发布成熟度 | 中等偏上 | 模板/标签/CHANGELOG 齐备；缺 PyPI 发布、依赖锁定、行为与声明的一致性 |
| 科学严谨与可复现性 | 中等 | 有 manifest 与数值测试；但依赖未锁、manifest 未记录版本、缺 DOI 与金标准回归 |

---

## 二、代码架构与可维护性

### A1 · 文档化功能依赖未声明，安装后静默失效 — P0，小工作量
`web_app.py` 通过 `try/except` 可选导入 `readlif`（LIF 查看器）与 `statsmodels`（ANOVA 事后多重比较），但 `pyproject.toml` 的 `dependencies` 里**两者都没有**。结果：用户按 README 执行 `pip install -e .` 后，文档中明确列出的 LIF 工作流（`web_api/lif_viewer.py`，667 行，含 `fluorescence_lif.html` 页面）会在 `HAS_READLIF=False` 下静默不可用，统计显著性功能同理。

> 建议：把 `readlif` 加入主依赖；`statsmodels` 要么进主依赖、要么收进一个 `[stats]` extra 并在 UI/文档里明确说明。对"已写进 README 的功能"不应依赖可选导入。

### A2 · `WebApiContext` 是一个 ~30 字段的 god-context / service locator — P1，中等工作量
`web_app.py` 构造一个携带约 30 个字段的 `WebApiContext`（helper 函数 + 各种 `HAS_*` 能力标志 + `pyabf/rhd/tifflib/Image/find_peaks/...` 库句柄 + jobs 管理器），再注入每一个 `register_*_routes`。这是一种隐式全局耦合：任意路由都能拿到全部能力，单测某个路由必须伪造整个 context，新增依赖要改动这个中心结构，可选库标志被层层透传。

> 建议：拆分为更小的、按领域分组的依赖（如 `SignalDeps`、`ImagingDeps`、`RenderHelpers`），或改用 Flask 的 app extension / 模块级单例 + 显式 import；让每个路由只声明自己真正需要的东西。这能显著降低后续加功能的回归面。

### A3 · 对"必装依赖"也做可选导入，防御是死代码 — P2，小工作量
`scipy`、`tifffile`、`Pillow` 都是 `pyproject` 里的**硬依赖**，但 `web_app.py` 仍对它们 `try/except` 并设置 `HAS_SCIPY/HAS_TIFF/HAS_PIL`。这些分支在正确安装下永远为真，属于无法被触发的死防御，反而增加阅读成本和"是否真的可选"的歧义。

> 建议：硬依赖直接顶层 import；只对"声明为 optional extra"的库保留 `HAS_*` 模式，并保持声明与代码一致。

### A4 · `services/fluorescence` 复杂度高度集中 — P2，监控为主
ratchet 基线显示 `services/fluorescence` 单包 6241 行，被 13 个 `web_api/fluorescence_*` 路由模块共享；`services/histology_project.py`（1819 行）也偏大。这本身是分层做得好的副作用（逻辑沉到了 service），但复杂度集中意味着该领域的改动半径大。

> 建议：把荧光 service 按子能力（stack / roi / gif / lut / 3d / kymograph）进一步切成显式子模块并各自配套测试；持续用现有 ratchet 监控，不必激进重构。

### A5 · legacy Tkinter 双维护负担需要明确"退役时间表" — P1，中等工作量
`desktop_apps/legacy/` 约 1.4 万行、单文件高达 2323 行（`fluorescence_roi_gui.py`），且被有意排除在严格 lint 之外、与 Web 只是"部分 parity"。对一个以 WebGUI 为权威形态的公开项目，长期保留一套不被严格质量门覆盖、功能又落后的并行 GUI，是持续的认知与维护成本。

> 建议：在 CHANGELOG / docs 里给出明确的弃用政策（哪些 legacy 命令将在 v0.x 标记 deprecated、v1.0 移除），并把 `services/fluorescence/lut.py` 这类"卡住迁移的最后一块"排进路线图，让 `--legacy` 真正成为过渡期开关而非永久分叉。

### A6 · 测试运行器不统一（unittest + pytest 并存）— P2，小工作量
单测用标准库 `unittest`（`python -m unittest discover`），但 e2e 用 `pytest`，且 `pytest` 已是 dev 依赖。两套 runner 增加心智负担，也让"统一覆盖率 / 标记 / 参数化"难做。

> 建议：统一到 `pytest`（它能直接跑现有 unittest 用例），CI 用单一 `pytest` 入口同时收单测与 e2e，简化 README 的"提交前命令"清单。

### A7 · `web_app.py` 混入了本应属于 `web_api/common` 的工具函数 — P2，小工作量
`browse_files`、`browse_files_recursive`、`float_or`、`int_or`、`fig_to_b64`、`apply_axes_limits` 等通用 helper 直接定义在应用入口里再注入 context，入口文件因此承担了"装配 + 工具库"双重职责。

> 建议：迁到 `web_api/common.py`，`web_app.py` 只保留应用装配与启动逻辑。

---

## 三、产品与用户体验

### B1 · 入口缺少"这是什么 / 给谁用"的一句话叙事与架构图 — P1，小工作量
README 直接进入功能命令表，信息密度高。对首次到访的外部研究者，缺一个 30 秒能读懂的定位段（解决什么科研痛点、覆盖哪些仪器、与同类工具的差异）和一张 Web ↔ services ↔ desktop 的关系图。

> 建议：README 顶部加一段"Who is this for / What problem it solves"，并放一张简单的架构示意（可用现有 `docs/repository_structure.md` 内容生成 Mermaid 图）。

### B2 · 移动/平板明确不支持，但缺优雅兜底 — P2，小工作量
README 与 WEB_README 都声明只支持桌面浏览器。这对科研工具可以接受，但目前似乎没有在窄屏给出明确提示。

> 建议：加一个窄屏断点的友好提示条（"本工具为桌面浏览器设计，请在更宽的窗口打开"），避免移动端用户看到错乱布局而误判为 bug。

### B3 · 只有 Flask 开发服务器作为唯一启动方式 — P2，文档即可
`app.run(..., debug=False, threaded=True)` 默认绑定 `127.0.0.1`，作为单用户本地工具是合理且安全的选择。但作为"成熟产品"，应在文档里明确它是**本地单用户**形态、不是为多用户/公网部署设计的（路径策略 `path_policy.py` 允许访问用户提供的任意本地路径，这在本地单用户下无害，一旦对外暴露即为风险）。

> 建议：在 WEB_README 增加一节"部署边界与安全假设"，明确"勿将该端口暴露到不可信网络"，并说明如要多用户化需要的额外工作。

### B4 · 错误处理与可发现性已较好，建议补"安装自检" — P1，小工作量
中心化 `api_error` 信封 + 错误横幅 e2e 测试 + 命令面板 + 参数术语表（`dp_param_glossary.js`）都很到位。缺的是一个"装好之后怎么确认装对了"的自检。

> 建议：提供 `bte-web --self-check`（或一个 `examples` 冒烟脚本），跑一遍三个示例文件并对关键输出做断言，给新用户即时的"安装成功"信号。

---

## 四、开源 / 发布成熟度

### C1 · 声明遵循 Conventional Commits，但提交历史并未遵循 — P1，小工作量（一致性问题）
README 有 Conventional Commits 徽章、CONTRIBUTING 给了 `feat()/fix()` 范例，但实际 git log 是 `Improve histology...`、`Add ...`、`Refactor ...`，并非 `feat:/fix:` 前缀。声明与现实不一致会直接削弱公开项目的可信度。

> 建议：二选一——要么加 `commitlint` + commit-msg 钩子真正强制，要么把徽章/声明降级为"鼓励但不强制"。我倾向前者，因为它还能驱动自动化 CHANGELOG。

### C2 · 依赖全部用 `>=` 下界、无锁文件 — P0（兼顾复现性），小工作量
`pyproject` 里所有依赖只有下界（`numpy>=1.24`、`scipy>=1.10` …），没有任何上界或锁定。对科研工具这是复现性与"未来某天 clone 装不上/结果变了"的主要风险来源。

> 建议：提交一个经过测试的 `requirements-lock.txt`/`constraints.txt`（`pip freeze` 产物）或在 `[dev]` 锁定 CI 实际通过的版本组合，并在 README 标注"已测试版本矩阵"。这条同时服务于 §C 和 §五。

### C3 · 未发布到 PyPI — P1，中等工作量
项目有 `bte-*` 入口、版本号、tag（v0.2–v0.6），但 `[project.urls]` 只有 GitHub，没有 PyPI 包。外部用户只能 git clone + `pip install -e .`，无法 `pip install bioelectronics-toolkit`。

> 建议：建立 `release` workflow（tag 触发 → build sdist/wheel → 通过 OIDC trusted publishing 发到 PyPI），让安装从"克隆源码"升级为"装包即用"。

### C4 · `Development Status :: 3 - Alpha` 与实际成熟度不符 — P2，极小工作量
有完整 CI、e2e、5 个发布 tag、稳定的输出契约，分类器却仍标 Alpha，可能让评估者低估项目。

> 建议：升到 `4 - Beta`（或在 README 说明为何保持 Alpha）。

### C5 · pipeline registry 指向非公开的本地项目数据树 — P1，小工作量
`pipelines/registry.json` 注册的模型脚本指向 `2025_Subcutaneous/` 等本地项目树，外部 clone 会看到 `Local script missing`。虽然 README 已说明，但公开仓库内置一堆指向不存在私有数据的条目，观感上像"半成品"。

> 建议：把私有项目条目移出默认 registry（或放进一个 `examples` 命名空间），并至少提供 1 个**完全自包含、用 `examples/` 数据即可跑通**的示范 pipeline，让"Pipeline Runner"对外部用户是活的而非占位。

### C6 · 缺 CODE_OF_CONDUCT — P2，极小工作量
issue/PR 模板、dependabot、.github/SECURITY.md 都有，唯独没有行为准则。对接受外部贡献的公开项目这是常见缺口。

> 建议：加一份标准 `.github/CODE_OF_CONDUCT.md`（Contributor Covenant 即可）。

---

## 五、科学严谨与可复现性

### D1 · run manifest 未记录工具版本 / 依赖版本 / commit — P0，小工作量
`services/output_manifest.py` 的 `manifest_comment(...)` 默认 `version="bioelectronics_toolkit"`——是一个**字面字符串而非真实语义版本号**。也就是说生成物里没有可靠地嵌入"用哪个版本、哪个 commit、什么依赖版本算出来的"。这是科研复现性的核心短板：拿到一张图/一份 CSV 无法回溯到确切的计算环境。

> 建议：在 manifest / 导出注释 / SVG `<metadata>` 中统一注入 `APP_VERSION`、`APP_COMMIT`（`web_app.py` 已经能算出二者）以及关键科学依赖版本（numpy/scipy/pandas/tifffile）。让每个产物自描述其计算来源。

### D2 · 缺金标准（golden）数值回归 — P1，中等工作量
`tests/test_signal_services.py` 有 58 处断言，数值层面已有覆盖，值得肯定。但 `examples/` 只有输入样本、没有"已知正确输出"，缺少把核心算法（峰检测、基线、ΔF/F0、电化学检测）钉死在参考数值上的回归测试。

> 建议：为每个核心算法准备一组小型参考输入 + 期望输出（容差断言），纳入 CI。这样任何无意改变科学结果的改动都会被立即捕获——这是科研软件区别于普通软件的关键质量门。

### D3 · 有 CITATION.cff 但无 DOI — P1，小工作量
项目可被 BibTeX 引用，但没有版本化 DOI，难以在论文里稳定引用某一具体版本。

> 建议：接入 Zenodo–GitHub 集成，为每个 release 自动铸造 DOI，并把 DOI 徽章加到 README 与 CITATION.cff。

### D4 · 参数透明度尚可，建议补"方法学说明" — P2，中等工作量
前端有参数术语表（`dp_param_glossary.js`），是很好的起点。但对发表级使用，研究者需要知道每个分析背后的具体算法与默认参数依据。

> 建议：在 `docs/pipelines/` 下为每类分析补一页简短"Methods"说明（用了什么滤波/检测算法、默认窗口/阈值的来源、单位约定），可直接被论文 Methods 章节引用。

---

## 六、优先级行动清单（建议执行顺序）

**P0 — 影响"装得上、用得了、可复现"，应尽快做：**
1. 把 `readlif`（必要）与 `statsmodels` 加入依赖声明，修复 LIF/统计功能静默失效（A1）。
2. 提交经测试的依赖锁定文件并标注已测试版本矩阵（C2）。
3. 让 run manifest / 导出物自动嵌入工具版本 + commit + 关键依赖版本（D1）。

**P1 — 提升专业度与可信度，近期排期：**
4. 统一 commit 规范（commitlint 强制或降级声明）（C1）。
5. 给 legacy Tkinter 制定明确弃用时间表（A5）。
6. 发布到 PyPI + tag 触发的 release workflow（C3）。
7. 提供 1 个用 `examples/` 即可跑通的自包含 pipeline，私有条目移出默认 registry（C5）。
8. 加金标准数值回归测试（D2）。
9. 接入 Zenodo DOI（D3）。
10. README 顶部加定位叙事 + 架构图；提供安装自检命令（B1、B4）。

**P2 — 打磨项，有空再做：**
11. 拆解 `WebApiContext` god-context（A2）。
12. 清理对硬依赖的死可选导入（A3）；helper 迁入 `web_api/common`（A7）。
13. 统一测试 runner 到 pytest（A6）。
14. 窄屏友好提示（B2）；部署边界安全说明（B3）。
15. 升级 Development Status 分类器（C4）；加 CODE_OF_CONDUCT（C6）。
16. 持续监控 `services/fluorescence` 复杂度（A4）；补方法学文档（D4）。

---

## 七、已经做得很好、建议保持的部分

- **分层 + 边界守卫**：`services/` ↔ `web_api/` 分层，配 `check_services_ratio.py`（route 变厚即报警）与 `check_private_service_usage.py`，是很多成熟产品都没有的自动化纪律。
- **CI 深度**：3 版本矩阵 + compileall + 单测 + 覆盖率 + Playwright e2e + Windows 冒烟 + 两级 lint。
- **请求校验现代化**：v0.6.0 已把全部路由迁到 Pydantic schema 并由此生成 OpenAPI。
- **隐私优先的遥测**：默认关闭、本地存储、契约文档明确"绝不记录路径/文件名/参数/数据"。
- **工程文档齐全**：ADR、设计 token、维护者上手、桌面/Web parity 矩阵、仓库结构、遥测协议。
- **开箱即试**：`examples/` 合成样本 + "30 秒上手"段落 + 演示 GIF/截图。

这些是项目最值得保留的资产；上面的修改建议本质上是"把已经很扎实的工程基础，补齐成一个外部研究者可信赖、可引用、可复现的公开发布"。

---

# 附录：深度结构审视 + 文件处理效率 + 文件/内存安全（2026-05-26 补充）

本节是针对"结构层面"和"文件处理效率/内存保护"的二次深入审查，结论基于对 `services/fluorescence/`、`web_api/fluorescence_*`、`services/background_jobs.py`、`services/histology_*`、`web_app.py`、`path_policy.py` 等真实代码的逐行阅读。每条都给出**文件:行号级别的证据**、**风险**、**修复方向**与优先级。

## 八、结构层面的深入问题

### S1 · matplotlib 用全局 `pyplot` 状态 + Flask `threaded=True` —— 并发下的经典竞态 — P0，中等工作量
- 证据：`web_app.py` 末尾 `app.run(..., threaded=True)`；全代码库 `plt.subplots()/plt.figure()` 共 **30 处**（`web_api/echem_pc.py`、`fluorescence_roi_basic_routes.py`、`fluorescence_gif_kymograph_routes.py`、`services/rhd_viewer.py`、`services/abf_viewer.py`、`services/figure_generator/plots.py` …），而面向对象的 `Figure()` 只有 **3 处**。
- 风险：`pyplot` 维护一个**进程级全局**的当前-figure 管理器，**不是线程安全的**。在 `threaded=True` 下，两个并发请求（或一个后台任务 + 一个前台请求）同时 `plt.subplots()` / `plt.close()` 会互相踩踏：关错 figure、图像内容串台、或某个 figure 未被回收导致内存持续增长。这是 Flask + matplotlib 最常见的隐藏 bug，单用户多标签页也能触发。
- 修复：服务端渲染一律改用 OO API——`from matplotlib.figure import Figure; fig = Figure(); FigureCanvas(fig); ax = fig.subplots()`，彻底不碰 `plt.*` 全局状态；`fig_to_b64` 接收显式 `fig` 已是对的，只需把上游 30 处 `plt.subplots` 替换掉。可加一条 lint/grep 守卫禁止在 `web_api/`、`services/` 出现 `plt.subplots`/`plt.figure`。

### S2 · 后台任务每次 `submit` 直接起一个裸线程，无并发上限 — P1，中等工作量
- 证据：`services/background_jobs.py:162` 每次 `submit` 都 `threading.Thread(target=self._run, ..., daemon=True).start()`，没有线程池/队列/最大并发数。
- 风险：用户连点几个"导出"，就并发起 N 个重型任务（每个都可能整文件读 TIFF，见 §九）。线程数与峰值内存都不可控，单进程 Flask 很容易被自己拖垮，且会放大 §F1 的 OOM。
- 修复：换成有界 `ThreadPoolExecutor(max_workers=N)` 或一个串行/小并发的作业队列；`max_workers` 可配置（默认 2–3）。这同时给了"同一时刻最多吃多少内存"的天然护栏。

### S3 · 大量 import-time 副作用，缺 `create_app()` 工厂，损害可测试性 — P1，中等工作量
- 证据：`web_app.py` 在模块导入时就 `app = Flask(...)`、改写全局 `plt.rcParams`、构造 `_job_manager`、构造 30 字段的 `_web_api_ctx` 并注册所有路由。没有应用工厂函数。
- 风险：`import web_app` 即产生重副作用（建 Flask app、连 sqlite、改全局 matplotlib 配置）；想在测试里用不同配置（如临时缓存目录、禁用 jobs）启动一个干净 app 很困难，e2e 之外的路由级单测难写。
- 修复：抽出 `create_app(config) -> Flask` 工厂，把 ctx 构造、路由注册、rcParams 设置都放进去；模块级只保留 `app = create_app()` 供 WSGI 用。配合 §A2 拆 `WebApiContext` 收益叠加。

### S4 · `WebApiContext` god-context（与正文 A2 同源，结构视角补强）— P1
- 结构视角补充：这个 30 字段对象把**能力探测标志**（`HAS_*`）、**第三方库句柄**（`pyabf/tifflib/Image/find_peaks…`）、**通用 helper**、**jobs 管理器**混在一层，等于一个手写的全局服务定位器。它和 S3 的 import 副作用叠加，使"单元测试一个路由"几乎必须把整个应用立起来。
- 修复方向见 A2：按领域拆分依赖 + 工厂注入。

### S5 · `services/fluorescence` 内部还有"路由上下文"对象，分层出现回流 — P2
- 证据：`services/fluorescence/route_context.py`(21KB)、`gif_roi_context.py`(24KB)、`tiff_volume_context.py`(26KB) 这些 `*_context` 模块用闭包工厂返回一大包 `_fl_*` 函数（如 `route_context.py:53` 定义 `_fl_read_tiff_as_pages` 闭包）。这其实是把"路由层装配逻辑"下沉进了 service 层，service 因此反向依赖了请求/上下文形态。
- 风险：service 层本应是纯函数式、可独立测试的算法层；闭包包裹 + 上下文耦合削弱了这一点，也是该包体积畸大的部分原因。
- 修复：把 `_fl_*` 闭包改写为模块级纯函数（显式传参），上下文只负责挑选/组合，不再承载算法实现。

## 九、文件处理效率

### F1 · TIFF 栈"整文件读入内存"，且与 GIF/3D 的惰性读取**不一致** — P0，中等工作量
- 证据：栈/ROI 路径用 `tifffile.imread(path)` **一次性把整个多页 TIFF 读成一个 ndarray** 再切片——`services/fluorescence/stack.py:179` `read_tiff_as_pages`、`web_api/fluorescence_stack_routes.py:138/204/246`、`web_api/fluorescence_roi_basic_routes.py:91`、`services/fluorescence/route_context.py:54`、`services/histology_tiff_project.py:378`。
- 对照：GIF 路径是**按页惰性读取**——`services/fluorescence/gif.py:129-133` `tif.pages[i].asarray()` 只取需要的帧；3D 体渲染做了**降采样上限**——`tiff_volume_context.py:309-336` `max_points/max_xy/max_z` 钳制。说明"省内存的正确姿势"项目里已经有了，只是没用到栈/ROI 这条最常用的路径上。
- 风险：一段时间序列荧光栈轻松到 2000 帧 × 2048×2048 × uint16 ≈ **16 GB**，`imread` 会瞬间把它全压进 RAM，直接 OOM 杀掉整个单进程服务（连带 §S2 的并发任务）。
- 修复：栈/ROI 路径统一改成 `with tifffile.TiffFile(path) as tif: tif.pages[i].asarray()` 只解码需要的帧；或用 `tifffile.imread(path, aszarr=True)` / `memmap` 惰性视图。ROI 时序分析按帧迭代累加统计量，避免一次性持有整栈。

### F2 · 同一文件被多个端点反复重读、重复解码，无解码缓存 — P1，中等工作量
- 证据：`fluorescence_stack_routes.py` 的 browse/preview/export 等多个端点各自 `_fl_read_tiff_as_pages(p)` 或 `tifflib.imread(...)`；用户在前端反复微调 LUT/min-max 再渲染时，**同一个大 TIFF 每次请求都重新从磁盘解码**。
- 风险：交互式调参体验差（每次都等整栈解码），磁盘/CPU 浪费。
- 修复：加一个小的、按 `(path, mtime, size)` 作键的 LRU 解码缓存（限制条目数与总字节，配合 §F1 的惰性读取只缓存当前帧/降采样视图），并提供失效策略。

### F3 · 每张图都 base64 内联进 JSON 响应 — P2，小～中工作量
- 证据：`web_app.py` `fig_to_b64` 把图渲染成 PNG → `base64.b64encode` → 放进 JSON；几乎所有绘图端点都走这条路。
- 风险：base64 体积 +33%，且整张图在内存里以 str 形式驻留；多面板/批量响应会显著放大内存与传输负担。
- 修复：对大图改用二进制流式端点（`send_file`/`Response(mimetype="image/png")`），JSON 里只回一个图像 URL；或至少对 DPI、单响应图数量设上限。

### F4 · CSV 全量读入，无行数上限 — P2，小工作量
- 证据：`services/csv_tools.py:50` 与 `services/csv_viewer.py:194` 都是 `pd.read_csv(path)` 全量读取；表头探测已用 `nrows`（`csv_tools.py:11`，做得好）。
- 风险：文件夹叠图场景下 N 个 DataFrame 同时在内存；遇到异常大的导出 CSV 会吃满内存。
- 修复：给单文件加可配置行数/字节上限并在超限时提示降采样；叠图时考虑只读绘图所需列（`usecols`）与下采样。

## 十、文件与内存安全（含内存保护）

### M1 · 像素/尺寸预检护栏只在 histology 有，未覆盖荧光栈等路径 — P0，小～中工作量
- 证据：histology 做得很到位——`services/histology_tiff_project.py:34` `DEFAULT_MAX_IMAGE_PIXELS = 300_000_000`，可经 `DP_HISTOLOGY_MAX_IMAGE_PIXELS` 调（:346），超限时给出可操作的报错（:410 "Export or downsample…"）。但这套护栏**没有**应用到 `fluorescence_stack_routes` / `read_tiff_as_pages` / CSV / RHD。
- 风险：荧光栈/大 CSV 没有任何"这文件多大、要不要拒绝/降采样"的预检，单个超大输入即可 OOM（与 F1 同根）。
- 修复：把 histology 的护栏抽成通用工具（如 `services/io_guards.py`：根据 `TiffFile.series` 的 `shape×itemsize`、CSV 字节数做 pre-flight 估算），在所有重型读取入口统一调用，超限则拒绝或强制降采样路径，阈值用统一环境变量族 `DP_MAX_*` 暴露。

### M2 · 导出默认 `overwrite=True`，静默覆盖既有结果 — P1，小工作量
- 证据：`web_api/fluorescence_request_schemas.py:289/316`、`web_api/lif_viewer.py:70/84/92` 及多处 `d.get("overwrite", True)`、`services/fluorescence/volume3d_exports.py:103` 默认都为 `True`；而 `path_policy.unique_path` 其实**支持**自动加 `_2/_3` 后缀的非覆盖模式，只是没被设为默认。
- 风险：科研数据工具的核心产物是图/CSV/manifest，默认覆盖意味着同名重跑会**无提示地销毁上一次的分析结果**——典型数据丢失隐患。
- 修复：默认改为非覆盖（自动版本化），覆盖需用户显式勾选；或在覆盖前于响应里明确告知"将覆盖 N 个已存在文件"。

### M3 · 路径策略无根目录约束（本地单用户可接受，但需文档化边界）— P1（文档）/ P2（加固）
- 证据：`web_api/path_policy.py` 的 `resolve_output_dir` 直接 `Path(raw_output).expanduser()`，可指向磁盘任意位置；`web_app.browse_files` 可列任意目录。无 allowlist 根。
- 风险：本地单用户、绑定 `127.0.0.1` 下基本无害；但一旦有人 `--host 0.0.0.0` 暴露端口，这就是任意路径读/写。属于"安全假设"问题。
- 修复：文档明确"本地单用户、勿暴露公网"（与 B3 合并）；如要加固，可选地引入一个可配置的工作根目录，对越界路径拒绝（path containment 校验）。

### M4 · 解压炸弹 / 恶意图像防护仅 histology 有 — P2，小工作量
- 证据：仅 histology 设了 `MAX_IMAGE_PIXELS`；其余 PIL/tifffile 读取未显式设 `Image.MAX_IMAGE_PIXELS` 或对异常压缩比做检查。
- 风险：本地工具风险低，但"打开同事发来的 TIFF/PNG"也属于不完全可信输入；一个超高压缩比文件可在解码时撑爆内存。
- 修复：在统一 IO 入口（见 M1）设置进程级 `Image.MAX_IMAGE_PIXELS` 与解码前尺寸估算，作为兜底。

### M5 · 值得保留的良好实践（内存/并发安全方面）
- **原子写**：`web_api/preferences.py` 与 `services/file_profiles.py` 都用 `tmp.write_text(...)` + `os.replace(...)` 原子替换，并以 `threading.Lock` 串行化 read-modify-write（`preferences.py:21/95-147`、`file_profiles.py:10/177-243`），有效避免了配置文件的并发丢更新与半写损坏。**这套模式应推广到所有 JSON sidecar/manifest 的写入。**
- **递归浏览有上限**：`web_app.browse_files_recursive(max_files=300)` 给目录扫描设了硬上限，避免巨型目录拖垮浏览。
- **sqlite 作业持久化**有 `timeout=10` 与 `Lock`（`background_jobs.py:46/56`），方向正确（仅需补 §S2 的并发上限）。

## 十一、新增优先级清单（结构 / 效率 / 安全）

**P0（正确性/稳定性，尽快）**
1. F1 + M1：荧光栈/ROI 改惰性按页读取，并接入通用尺寸预检护栏（最高收益，直接消除 OOM 主因）。
2. S1：服务端绘图全部改 OO `Figure`，禁用 `plt.*` 全局状态，加 lint 守卫。

**P1（健壮性/可维护性，近期）**
3. S2：后台任务改有界线程池，给出并发/内存护栏。
4. M2：导出默认非覆盖、自动版本化，杜绝静默数据丢失。
5. S3 + S4：抽 `create_app()` 工厂、拆 `WebApiContext`，提升可测试性。
6. F2：加按 `(path,mtime,size)` 的 LRU 解码缓存，改善交互式调参。
7. M3：文档化"本地单用户/勿暴露公网"的安全边界。

**P2（打磨）**
8. F3：大图改流式端点；F4：CSV 行数上限与按列读取。
9. S5：把 `services/fluorescence/*_context` 的 `_fl_*` 闭包下沉为模块级纯函数。
10. M4：统一设置 `Image.MAX_IMAGE_PIXELS` 等解码炸弹兜底。
11. M5：把原子写 + 锁的模式推广到所有 JSON sidecar/manifest 写入。

> 一句话：**F1+M1+S1 是这一轮最该先动的三项**——前两者解决"一个大文件就能把服务 OOM 掉"的根因，S1 解决"并发渲染图像串台/泄漏"的隐患。三者改动范围可控，且项目里都已存在正确范式（GIF 的惰性读取、histology 的像素护栏、`fig_to_b64` 的显式 figure），属于"把好做法推广到全局"而非从零设计。

---

# 附录二：全库静态分析（一次性缺陷清单，2026-05-26）

## 扫描方法（可复核）
不靠肉眼扫 4.6 万行，而是用**项目自带的 ruff 0.15** 对全部 165 个 `.py` 开全规则集（`E,F,W,I,B,C90,UP,SIM,PERF,RUF,A,RET,ARG`）跑了三遍（maintained / legacy / vendor 分开），再叠加针对运行时风险与安全的全库正则扫描。下面是去重后的完整结果，你可用同样命令复核：
```
python3 -m ruff check services web_api desktop_apps/launchers web_app.py \
  --select E,F,W,I,B,C90,UP,SIM,PERF,RUF,A,RET,ARG --ignore E402,E501 --statistics
```

## 0 · 先说结论：没有致命缺陷
| 致命类别 | 全库结果 |
| --- | --- |
| 未定义名 / 语法错误（F82x, E9）| **0**（maintained + legacy + 自身代码全清） |
| 裸 `except:`（无类型）| **0** |
| 可变默认参数 B006 / 默认值含函数调用 B008 | **0**（All checks passed） |
| `eval` / `exec` / `os.system` / `shell=True` / `pickle.load` / `yaml.load` | **0**（无任何注入面） |
| 未解析 import / 循环 import 致错 | **0**（compileall 通过） |

也就是说——**代码底子是干净的，下面所有问题都属于"健壮性/可维护性/效率"层面，而非"会崩"的硬 bug。**

## 1 · 缺陷分类总览（maintained 代码，255 项）
| 类别 | 数量 | 性质 | 严重度 |
| --- | --- | --- | --- |
| C901 函数过于复杂 | **79** | 可维护性 | 高 |
| RUF046 多余的 int() 转换 | 55 | 代码整洁 | 低 |
| B905 zip 未加 strict= | 23 | **正确性隐患** | 中 |
| UP035 已弃用的 import | 17 | 现代化 | 低 |
| PERF203 循环内 try/except | 12 | 性能 | 低 |
| RUF005 字面量拼接 | 11 | 性能/整洁 | 低 |
| SIM105 可用 suppress 替代的 except | 11 | 整洁 | 低 |
| PERF401 可用列表推导 | 9 | 性能 | 低 |
| I001 import 未排序 | 7 | 整洁 | 低 |
| 其余（SIM/UP/ARG/RET/F401…）| ~31 | 整洁 | 低 |

legacy（`desktop_apps/legacy`）另有 42 项，以 20 个 C901、7 个 E741（歧义变量名 `l/I/O`）、3 个 F401 未用 import、1 个 B904 为主——但同样**无致命项**。

## 2 · 高优先：C901 复杂度热点（共 99 个函数超阈值）
复杂度根因是**每个路由模块把全部 endpoint 处理器塞进一个 `register_*_routes(app, ctx)` 闭包**，导致单函数圈复杂度爆表（阈值 10）。最严重的：

| 函数 | 圈复杂度 | 文件 |
| --- | --- | --- |
| `register_fluorescence_gif_kymograph_routes` | **86** | web_api/fluorescence_gif_kymograph_routes.py |
| `register_fluorescence_stack_routes` | **80** | web_api/fluorescence_stack_routes.py |
| `register_lif_viewer_routes` | **78** | web_api/lif_viewer.py |
| `register_histology_routes` | 65 | web_api/histology.py |
| `register_fluorescence_roi_sequence_routes` | 63 | web_api/fluorescence_roi_sequence_routes.py |
| `register_fluorescence_gif_basic_routes` | 61 | web_api/fluorescence_gif_basic_routes.py |
| `register_fluorescence_gif_roi_analysis_routes` | 49 | …gif_roi_analysis_routes.py |
| `register_echem_pc_routes` | 47 | web_api/echem_pc.py |
| `register_rhd_viewer_routes` | 45 | web_api/rhd_viewer.py |
| `register_fluorescence_3d_routes` | 44 | web_api/fluorescence_3d_routes.py |
| `register_csv_viewer_routes` | 42 | web_api/csv_viewer.py |
| `register_echem_pv_routes` | 40 | web_api/echem_pv.py |
| （另有 ~15 个 register_* 在 12–38 之间）| | |

**纯算法类**复杂函数（与路由无关，更值得就地拆分）：`process_payload`(30)、`load_histology_preview_pair`(39)、`infer_outputs`(21)、`process_trace`(17)、`parse_datetime_text`(17)、`parse_slice_spec`(16)、`timestamp_from_element`(16)、`distribution_payload`(15)、`clean_trace_svg`(14)。

> 修复：路由层把内嵌 handler 提成模块级函数 + 用 `app.add_url_rule` 显式注册（或蓝图），`register_*` 只做装配；算法类函数按职责拆小。这同时直接消化掉团队已自标注的 14 个 `TODO(structure-debt): exceeds the 200-line route budget`。

## 3 · 中优先：B905 — 23 处 `zip()` 未加 `strict=`（正确性隐患）
位置（节选）：`services/echem.py:253,424`、`services/fluorescence/lif_export.py:117`、`lif_volume.py:99`、`roi_radial.py:177`、`tiff_volume_context.py:143,255`、`volume3d_exports.py:271,347`、`services/rhd.py:321`、`web_api/echem_pc.py:195`、`fluorescence_gif_kymograph_routes.py:181,333`、`fluorescence_roi_basic_routes.py:186,190,198,202`、`fluorescence_roi_sequence_routes.py:279,374,378`、`plot_export.py:85` 等。
> 风险：两个序列长度不一致时 `zip` **静默截断**，在"时间轴 vs 数据"、"通道名 vs 通道数据"这类配对里可能悄悄丢尾部数据而无报错。修复：Python 3.10+ 一律 `zip(a, b, strict=True)`，长度不匹配即抛错。

## 4 · 中优先：~37 处 `except Exception: pass` 静默吞异常
分布（services 25 处 + web_api 12+ 处），代表位置：`services/echem.py:76`、`abf_viewer.py:76`、`fluorescence/roi.py:143,153`、`fluorescence/lif_metadata.py:102,129,156,187,203`（一个文件 5 处）、`tiff_metadata_context.py:66,99,336`、`lif_dimensions.py:110`、`scripts_panel.py:53,70`、`web_api/system.py:66,70`、`lif_viewer.py:480,590`。
> 风险：解析失败被无声吞掉，问题难定位（尤其 LIF/TIFF 元数据解析这种"读不到就静默降级"的地方，用户根本不知道元数据丢了）。修复：至少 `logging.debug/warning` 记录被吞的异常，或用 `contextlib.suppress(SpecificError)` 缩窄到预期的异常类型，避免连 `KeyboardInterrupt` 之外的真错误也一起吞。

## 5 · 运行时风险（与附录一呼应，此处给全库完整清单）
**整文件图像读取（OOM 根因，F1/M1）——全库共 5 处在荧光热路径：**
`services/fluorescence/stack.py:179`、`services/fluorescence/route_context.py:54`、`web_api/fluorescence_roi_basic_routes.py:91`、`web_api/fluorescence_stack_routes.py:138,204,246`。
（histology 的 `tifffile.imread`/`Image.open` 已有 `MAX_IMAGE_PIXELS` 护栏；`self_check.py:52` 是小样本，安全。）

**`subprocess` 使用（已确认全部为 list 参数、无 shell 注入）：** `system_picker.py:62,196,210`、`provenance.py:64`、`scripts_panel.py:266`、`web_api/system.py:82,98`、`web_api/scripts_panel.py:191,193,195`。
> 唯一需留意：`services/scripts_panel.py:266` `subprocess.Popen([sys.executable, script_path])` 会执行用户脚本——这是"Scripts 面板"的设计功能，本地单用户可接受，但应在文档明确"会运行任意本地 Python，勿在该面板跑不可信脚本"，并与 M3 的安全边界说明合并。

## 6 · 团队已自标注的技术债（14 个 TODO，值得肯定）
绝大多数是 `# TODO(structure-debt): this route module exceeds the 200-line route budget`，出现在 `echem_pc.py`、`echem_pv.py`、`abf_viewer.py`、`emg_peaks.py`、`fluorescence_3d_routes.py`、`fluorescence_gif_basic_routes.py`、`fluorescence_gif_kymograph_routes.py`、`fluorescence_roi_basic_routes.py`、`fluorescence_roi_sequence_routes.py`、`fluorescence_roi_export_routes.py` 等文件首行。
> 说明团队对路由膨胀**已有清醒认知**并显式记账——这是成熟工程习惯。第 2 节的 handler 提取重构正是这些 TODO 的统一解法。

## 7 · 全库缺陷修复优先级
- **P1**：第 2 节 C901（路由 handler 提取 + 算法函数拆分，一并清掉 14 个 structure-debt TODO）；第 3 节 B905 全部加 `strict=`（低成本、防静默丢数据）。
- **P2**：第 4 节静默 except 改为带日志/缩窄类型；legacy 的 E741 歧义变量名重命名。
- **P3（一键可改）**：`ruff check --fix` 可自动修掉约 50 项（unused import、import 排序、弃用 import、多余 encode 等）；`--unsafe-fixes` 再覆盖约 101 项，建议人工 review 后批量应用。

> 总评：**这是一份"问题都在表层、根基很稳"的代码库**。没有任何会导致崩溃或安全漏洞的硬缺陷；255 项里真正影响质量的就两类——路由闭包的复杂度（已自标注）和 23 处 `zip` 缺 `strict`。把附录一的 F1/M1/S1（OOM 与并发画图）加上本节的 C901/B905，就是这个项目走向"成熟产品"在代码层面需要做的全部核心工作。——前两者解决"一个大文件就能把服务 OOM 掉"的根因，S1 解决"并发渲染图像串台/泄漏"的隐患。三者改动范围可控，且项目里都已存在正确范式（GIF 的惰性读取、histology 的像素护栏、`fig_to_b64` 的显式 figure），属于"把好做法推广到全局"而非从零设计。
