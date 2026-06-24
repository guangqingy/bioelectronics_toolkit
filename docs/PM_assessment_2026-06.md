# DataProcess / bioelectronics_toolkit — 产品评估报告

> 评估日期：2026-06-23 · 角色视角：产品经理 / 技术负责人
> 性质：**只评估、不修改**。本文给出问题判断、优先级与可执行的改造方向，具体改动由你本人实施。
> 评估范围：`services/`、`web_api/`、`web_app.py`、`web_templates/`、`web_static/js/`、`pipelines/`、`desktop_apps/`。

---

## 0. 执行摘要（TL;DR）

整体判断：**这是一个功能真实、科学逻辑扎实的本地科研工具，但产品边界过宽、结构债集中在三处**——一个对普通用户不可用的 Pipeline 子系统、一层永远走不到的"防御性"脚手架、以及一套缺乏统一规范的前端按钮/路径逻辑。这些都不是 bug，而是"半成品迁移 + 范围蔓延"留下的痕迹。

四个核心结论，与你的直觉一致：

1. **Pipeline 功能建议下线。** 注册表里 14 条 pipeline，**13 条指向仓库里根本不存在的 `2025_Subcutaneous/` 私有目录**，对任何正常用户都显示为 "Local script missing"。它带来一整套子进程执行器、注册表、校验、导航入口和测试，却几乎不可用。下线它的收益最高、风险可控。

2. **大量"防御代码"是死代码。** `HAS_SCIPY / HAS_TIFF / HAS_PIL / HAS_READLIF / HAS_ABF` 五个能力标志**全部硬编码为 `True`**，而这些库都是硬依赖（缺了 app 根本起不来）。于是所有 `if not has_tiff: return 错误` 分支**永远走不到**。这正是你说的"在不太可能的问题上花结构债"。

3. **"奇怪的路径"来自缺少统一的输出路径权威。** 启动时 `os.chdir(BASE_DIR)`，然后十几处用 `Path.cwd()` 兜底输出目录，与 `path_policy.resolve_output_dir()` 和各模块自定义逻辑混用。同一个"输出到哪里"的问题有三四种答案。

4. **UI 难用是规范缺失，不是个别按钮。** 275 个按钮里 **270 个用内联 `onclick=`**；按钮层级 **186 个 `btn-secondary` 对 24 个 `btn-primary`**——也就是每个页面一堆"次要"按钮、几乎没有视觉主行动点，用户不知道先点哪个。

下面分主题展开，每条都标注了**证据**、**影响**、**建议**和**优先级（P0 最高）**。

---

## 1. 产品范围：Pipeline 子系统应当下线（P0）

### 现状证据
- `pipelines/registry.json` 注册了 6 个分类、14 条 pipeline。
- 其中只有 **1 条** (`example_summary`，bundled example) 的脚本真实存在于仓库。
- 另外 **13 条**的 `script_path` 全部指向 `2025_Subcutaneous/Photocurrent/...`、`2025_Subcutaneous/EMG/...` 等**未被 git 跟踪的私有项目目录**。`pipelines/README.md` 自己也承认："The actual Subcutaneous analysis scripts are project-specific and are not tracked in the public repository."
- 为支撑这个功能，存在：`web_api/scripts_panel.py`（子进程执行 + pydantic 校验）、`services/scripts_panel.py`（进程管理、信号、线程锁、artifact 扫描）、`pipelines/registry.py`（加载/校验/可用性标注）、`web_templates/scripts.html`、`web_static/js/pages/scripts_runner.js`（**30KB**，是除 histology 外最大的页面 JS）、顶部导航 "Workflows ▸ Pipelines" 入口，以及 4 个测试文件。

### 产品判断
对一个面向研究者的本地工具，**"打开一个页面，里面 13/14 的条目都点不动"是明显的负体验**，还会让用户怀疑整个 app 的可靠性。这个功能本质上是你个人项目（Subcutaneous）的私有运行器，被错误地放进了通用产品里。它通过 `subprocess` 执行任意本地脚本，也是维护与安全复杂度的来源。

### 建议（与你的意向一致：删 pipeline，保留 app）
分两步、低风险：

1. **先隐藏，再删除。** 第一步只摘掉导航入口（`web_templates/partials/top_nav.html` 的 "Workflows" 菜单）并让 `/scripts` 路由 404 或重定向，观察是否有任何依赖。确认无碍后再物理删除。
2. **删除时的影响半径（已为你梳理好，仅这些文件需要动）：**
   - 删除：`pipelines/`、`web_api/scripts_panel.py`、`services/scripts_panel.py`、`web_templates/scripts.html`、`web_static/js/pages/scripts_runner.js`、`dev_scripts/check_analysis_scripts.py`
   - 解除注册：`web_app.py` 里的 `register_scripts_panel_routes(...)` 及其 import
   - 清理引用：`web_templates/partials/top_nav.html`、`web_templates/base.html`、`web_templates/index.html`、`web_static/js/dp_palette.js`（命令面板条目）、`web_static/js/dp_settings_schema.js`、`web_api/pages.py`、`web_api/system.py`
   - 删测试：`tests/test_pipeline_registry.py`、`tests/e2e/test_pipeline_runner_missing.py`，并清理 `tests/test_repository_assets.py` / `tests/test_webgui_contracts.py` / `tests/test_scripts_panel_shutdown.py` 中的相关断言

3. **务必保留的东西（不要误删）：** **Run History / Run Manifest 不属于 pipeline**。它被几乎所有工具（ABF、EChem、Fluorescence、Histology…）用来写产物清单，是独立子系统，应当保留。删除时只动 pipeline 运行器，不要碰 `run_history*`、`output_manifest`、`provenance`。

> 一句话：这是收益最高、边界最清晰的一刀。预计可净减约 **3,000+ 行**代码与一个完整导航板块。

---

## 2. 过度防御 / 结构债（P0–P1）

你的"在不太可能的问题上花太多结构债"在代码里有非常具体的对应物：

### 2.1 死掉的能力标志（P0，纯删除、零风险）
- `web_app.py` 顶部：`HAS_ABF = True`、`HAS_SCIPY = True`、`HAS_STATSMODELS = True`、`HAS_TIFF = True`、`HAS_PIL = True`、`HAS_READLIF = True`——**全部写死为 True**。只有 `HAS_RHD` 有真实的 try/except（因为 intan parser 是 vendored 可选件）。
- 而 `scipy / pillow / tifffile / readlif / statsmodels` 都在 `pyproject.toml` 的**硬依赖** `dependencies` 列表里，且在 `web_app.py` 顶部**无条件 import**。缺任何一个，app 在 import 阶段就崩了，根本到不了标志位。
- 这些标志被塞进 `WebApiContext`，再透传到 **34 处**调用点，催生了像 `if not has_tiff or not has_pil: return 错误`（见 `fluorescence_gif_basic_routes.py`、`fluorescence_3d_routes.py` 等）这样**永远为假、永不执行**的分支。

**建议：** 把这 5 个标志（保留 `HAS_RHD`）连同其 context 字段、34 处读取点、以及所有 `if not has_*` 守卫分支一并删掉。这是最干净的"还结构债"——删完功能完全不变，只是少了一层假装依赖可能缺失的脚手架。

### 2.2 双份响应封装（"临时兼容层"已长期化）（P1）
- `web_api/response.py` 的 `make_envelope()` 里有注释：*"Temporary compatibility layer: existing pages still read fields such as saved_path/files/img directly from the response object."*
- 于是每个 API 响应**同时**带有规范信封字段（`ok/data/outputs/warnings/error`）**和**散落在顶层的 legacy 字段，再加一个 `after_request` 钩子 (`register_api_envelope`) 对所有 `/api/` 响应二次包裹。
- 结果：前端有的页面读 `resp.data.x`，有的读 `resp.x`，两套契约并存。这就是"结构看起来很奇怪"的来源之一——**一个没走完的迁移被当成了稳定态。**

**建议：** 选定一种响应契约（建议保留信封 `data`），把前端读取点统一过去，然后删掉顶层透传与 `after_request` 二次包裹。这是中等工作量、但能显著降低"心智负担"的一次收口。

### 2.3 Context 的双重访问（同上一个未完成迁移）（P1）
- `web_api/context.py` 的 `WebApiContext` 同时实现了 `__getitem__/__setitem__/get`，注释明说：*"Route modules still use mapping-style access during the incremental migration."*
- 也就是说全代码库一半用 `ctx["err"]`、一半用 `ctx.err`。dataclass 的类型提示因此基本失效（还得手动维护 dict 语义）。

**建议：** 二选一并全量替换（推荐属性访问 `ctx.err`，能拿回类型检查），删掉 `__getitem__` 等兼容方法。可用一次性脚本批量改写，工作量可控。

### 2.4 `io_guards.py` 对本地单用户场景过度工程（P2）
- 为 TIFF/CSV 大小检查实现了**带线程锁的 LRU 缓存**（`_TIFF_ESTIMATE_CACHE`、`_FILE_SIZE_CACHE`，256 条上限）+ 三个**环境变量可调**上限（`DP_MAX_IMAGE_PIXELS` 等）。
- 对一个本机、单用户、自己选文件的研究工具来说，这套并发缓存 + 可配置阈值属于"为不会发生的并发/滥用场景做的设计"。

**建议：** 不必现在删（它没坏），但作为结构债登记。简化方向：去掉锁与 LRU，保留一个简单的尺寸上限常量即可。

### 2.5 异常处理的总量（P1，逐步收敛）
- `services/` + `web_api/` 合计 **443 处 `try/except`**，其中 **251 处是宽口径 `except Exception`**。`histology_project.py`（最大单文件，4,245 行）里多处 `except Exception: warnings.append(... fallback ...)`。
- 宽口径捕获会把真实 bug 吞成"warning + 继续"，调试时很难定位——这与"奇怪、难排查"的主观感受吻合。

**建议：** 不要一次性重写。设一条规则："新代码不写裸 `except Exception`，旧代码改到哪顺手收窄到哪"。优先收窄 I/O 读取这类能明确预期异常类型的地方（`OSError`、`ValueError`）。

---

## 3. "奇怪的路径"：缺少统一的输出路径权威（P1）

### 证据
- `web_app.py:277` 启动时 `os.chdir(BASE_DIR)`，把进程工作目录改成项目根。
- 之后**至少 8 处**用 `Path.cwd()` 作为输出/锚点目录的兜底：`path_policy.py`、`echem_lineshape.py`（`Path.cwd()/"plots_shape_average"`）、`emg_peaks.py`（`Path.cwd()/"emg_peaks.csv"`）、`fluorescence_gif_kymograph_routes.py`、`fluorescence_gif_roi_analysis_routes.py`、`fluorescence/roi_render_context.py` 等。
- 同时存在一个**本该是唯一权威**的 `web_api/path_policy.resolve_output_dir()`，但并非所有模块都走它——echem、emg、部分 fluorescence 路由各自手写路径拼接。
- 状态/缓存统一写在项目内 `.dataprocess_cache/`（run_history、file_profiles、jobs.sqlite、telemetry、settings），这本身 OK；但 **`examples/.dataprocess_cache/` 被提交进了 git**（含 `run_history.json`、`file_profiles.json`、`runs/`）——把运行期状态当成了仓库资产，是明显的味道。

### 影响
"输出到哪里"取决于：用户传没传 output_dir、当前 cwd 是什么、走的是哪个模块的自定义逻辑。三者交叉，导致同类操作产物落点不一致，也让"为什么文件跑到这儿了"难以解释。`os.chdir` + `cwd` 兜底还会让行为依赖**启动方式**（双击脚本 vs 命令行 vs 测试）。

### 建议
1. **确立单一权威：** 所有输出路径都必须经过 `path_policy`（或一个新的 `OutputPaths` 模块），禁止模块内直接 `Path.cwd()/...`。把 echem/emg/fluorescence 的自定义路径逻辑收编进去。
2. **去掉对 cwd 的隐式依赖：** 用 `BASE_DIR`/显式 `project_root` 取代 `Path.cwd()` 兜底，长期目标是能删掉 `os.chdir(BASE_DIR)`。
3. **从 git 移除运行期缓存：** 把 `examples/.dataprocess_cache/` 加进 `.gitignore` 并 `git rm --cached`，示例数据与示例**产物**分开。

---

## 4. UI / 交互：问题是规范缺失，不是单个按钮（P1）

### 证据
- **内联事件处理满天飞：** 275 个 `<button>` 中 **270 个**用内联 `onclick="DP.page.xxx()"`。逻辑、结构、行为耦合在 HTML 里，难以统一管理（也是"按钮设计不正常"的根因——每个按钮各自为政）。
- **没有行动层级：** `btn-secondary` 出现 **186 次**，`btn-primary` 仅 **24 次**。多数页面是"一排灰按钮"，用户看不出主操作是哪个。健康的页面应当是"一个主行动 + 若干次要"。
- **导航过载：** 顶栏 7 个下拉组（ABF / EChem / EMG / CSV / Fluorescence / Histology / Workflows），其中 Fluorescence 一个菜单就有 7 个子项。信息架构偏"工具罗列"，而非"任务导向"。
- **巨型页面脚本：** `histology_analysis.js` **78KB**、`scripts_runner.js` 30KB、`fluorescence_stack.js` 25KB。单文件过大，交互一复杂就难维护，bug 也更隐蔽。

### 建议（按性价比排序）
1. **先立按钮规范（低成本、高感知）：** 明确"每屏一个 `btn-primary` 主行动"，其余降级为 secondary/tertiary 或图标按钮。仅这一条就能让大量页面"看起来正常"。
2. **内联 `onclick` 改事件委托：** 用 `data-action="page.run"` + 一个集中监听器替代 270 处内联调用。可渐进式做，每页迁移互不影响。
3. **导航做任务化收敛：** 评估把 7 组菜单按"输入→分析→导出"或按使用频率重组；低频/私有入口收起。（Pipelines 入口随第 1 节一并去掉。）
4. **拆分巨型 JS：** 至少把 `histology_analysis.js`（78KB）按职责拆成 state / render / export 等模块，与 fluorescence 已有的拆分风格看齐。

> 注：UI 体验问题适合先做一轮**可用性自查**（拿 3–5 个真实任务走查"用户先点哪、卡在哪"），再定具体改动——这是产品视角比逐个改按钮更有效的做法。

---

## 5. 代码健康度杂项（P2，登记备查）

- **单文件巨石：** `services/histology_project.py` **4,245 行 / 170KB**，远超其他模块（次大 ~950 行）。histology 是整个工具里最重的一块（后端 4.2k 行 + 前端 78KB JS），建议作为独立的"重构候选"单独立项，按子功能拆分。
- **EMG 反复修补的信号：** git 近 30 条提交里有**十余条连续的 `fix(web): ...EMG baseline/peak...`**。说明 EMG 基线/峰值检测是个"反复没调稳"的点。产品角度值得问：是算法需求本身不清晰，还是缺少回归测试锁住行为？建议为它补**带真实样本的回归测试**，止住这种"打地鼠"。
- **依赖体检：** 仓库有 dependabot 分支若干（pydantic-core、virtualenv、platformdirs 等待合并）。属常规维护，安排一次集中处理即可。

---

## 6. 优先级矩阵与建议路线图

| 优先级 | 事项 | 影响 | 工作量 | 风险 |
|---|---|---|---|---|
| **P0** | 下线 Pipeline 子系统（§1） | 高（去掉对用户不可用的大板块，净减 ~3k 行） | 中 | 低（影响半径已界定，Run History 不受影响） |
| **P0** | 删除死掉的 `HAS_*` 能力标志与不可达守卫（§2.1） | 中（去脚手架、提升可读性） | 低 | 极低（行为不变） |
| **P1** | 统一输出路径权威 + 去 `cwd` 依赖（§3） | 高（消除"奇怪路径"，行为可预期） | 中 | 中（需逐模块收编 + 回归验证） |
| **P1** | 收口响应信封与 context 双访问（§2.2 / §2.3） | 中（结束未完成迁移，降心智负担） | 中 | 中（前后端契约需同步改） |
| **P1** | 建立按钮层级规范 + 内联 onclick 改委托（§4.1 / §4.2） | 高（直接改善"难用"） | 中 | 低（可逐页推进） |
| **P2** | `io_guards` 简化、`except Exception` 渐进收窄（§2.4 / §2.5） | 中 | 中 | 低 |
| **P2** | 拆分 histology 巨石、EMG 回归测试、导航重组（§4.3 / §5） | 中 | 高 | 中 |
| **P2** | `examples/.dataprocess_cache` 移出版本控制（§3） | 低 | 低 | 极低 |

### 建议执行顺序（每步都能独立交付、独立验证）
1. **第一波（清场，低风险高感知）：** 删 `HAS_*` 死代码 → 隐藏并下线 Pipeline → `examples/.dataprocess_cache` 移出 git。做完跑一遍测试，app 行为不应有任何可见变化（除了少一个 Pipelines 菜单）。
2. **第二波（收口未完成迁移）：** 统一响应信封 / context 访问方式。
3. **第三波（路径权威）：** 把所有输出路径收编到 `path_policy`，逐步去掉 `os.chdir` 与 `cwd` 兜底。
4. **第四波（UI 规范化）：** 按钮层级 + 事件委托 + 导航/巨石拆分，配一轮可用性走查。

---

## 7. 附录：量化快照

| 指标 | 数值 |
|---|---|
| Python 文件数 / 行数（不含 vendor、pycache） | 184 个 / ~45.5k 行 |
| `services/` Python 行数 | 24,642 |
| `web_api/` Python 行数 | 9,712 |
| `tests/` Python 行数 | 6,393 |
| `pipelines/` Python 行数 | 262（但牵动一整套执行器/前端/测试） |
| 前端 JS 行数 / 文件数 | ~18.7k 行 / 62 文件 |
| 最大后端单文件 | `histology_project.py` = 4,245 行 / 170KB |
| 最大前端单文件 | `histology_analysis.js` = 78KB |
| `try/except` 总数（services+web_api） | 443，其中宽口径 `except Exception` 251 |
| 注册 pipeline 数 / 仓库内真实可运行 | 14 / **1** |
| 死能力标志 | 5 个写死 True（`HAS_SCIPY/TIFF/PIL/READLIF/STATSMODELS`），34 处透传 |
| 按钮总数 / 内联 `onclick` | 275 / 270 |
| 按钮层级 | `btn-secondary` 186 vs `btn-primary` 24 |
| 顶部导航下拉组 | 7 |

---

### 一句话收尾
你已有的科学功能是这个产品的真正价值；**它显得"乱"，几乎全部来自三件事：一个该下线的私有功能、两处没走完的迁移、和一套缺规范的前端**。按上面的四波顺序推进，每一波都能独立验证、互不阻塞，整体结构债能在不改变核心功能的前提下显著下降。

> 本报告仅为评估，未对任何源码做改动。
