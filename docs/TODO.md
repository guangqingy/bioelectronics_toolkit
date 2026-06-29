# TODO — 待办清单

> 复核结论（更新于 2026-06-29）：**没有全部完成。** 核心瘦身（P1 类型化收口）已基本做完，
> 另有几项 P4/P5 也完成了；但 **P2（安全收尾）、P3（收敛过度防御）、P4 的可访问性/JS 模块化**
> 基本还没动。当前测试绿：`pytest` 237 passed（仅 3 个 tkinter 环境失败）。

---

## ✅ 已完成（复核确认）

- **P1 类型化收口 —— 基本完成**
  - 所有 service 都**不再注入** `float_or`/`int_or`（`services/` 内已为空）。
  - web_api 的 `: Any =` 从 348 → **51**，其中真正属"请求字段"的剩余项基本都是**有意保留的多态结构**
    （fluorescence 的 `roi/rois/roi_polygons/crop_rects/bg_roi/extra_indices/channel_ranges/denoise`
    等 dict/list；`emg_analysis.downsample:"auto"`）。`context.py`(13) 是注入回调、`response.py`(1)
    是函数签名，本就该是 `Any`。
- **P4 命名一致性** —— 请求体变量已统一为 `body`（`payload = parse_` 现为 **0**，`body = parse_` 79）。
- **P4 G3 未转义 innerHTML** —— `showLogoutScreen()` 已改用 `createElement`+`textContent`（已修）。
- **P5 lock 版本注释** —— `requirements-lock.txt` 已是 `0.7.0`。

> P1 仅剩**少量可选的零头**（非多态、改起来无风险，想彻底归零再做）：
> `emg_analysis` 的 `channel → OptInt`、`dsf → OptInt`、`extensions → str`；
> `echem_lineshape.py` 的 2 个；`fluorescence_lif` 的 3 个。其余 `Any` 建议保留。

---

## ⬜ 还没做

### P2 · 安全收尾（都没动）

- [ ] **S2：`technical_details` 仅在 DEBUG 下返回**（`web_api/response.py:107-128`）。
      现在 `_public_error` 无论是否 DEBUG 都把完整 traceback 作为第 4 个返回值，`api_error`
      第 127-128 行再无条件塞进响应。改成非 DEBUG 时不附带（堆栈已 `LOG.error` 记录 + 给 `error_id`）。
- [ ] **会写盘的 GET 改 POST-only**（Host/Origin 守卫已加于 `web_app.py:150` ✅，这步是补刀）
  - `web_api/abf_viewer.py:193` `/api/abf/export`
  - `web_api/emg_analysis.py:127` `/api/emg/analysis/export_channel`
  - `web_api/emg_analysis.py:148` `/api/emg/analysis/export_processing`

### P3 · 收敛过度防御（都没动）

- [ ] 清理被 `@api_endpoint` 已覆盖的冗余 `try/except Exception`（web_api+services 现 **208** 处）。
- [ ] 收敛对**自产数据**的 `isinstance(x, dict)` 探测（现 **144** 处）；跨边界的（读文件/subprocess/SQLite）保留。

### P4 · 前端（剩两项）

- [ ] **G2 可访问性** —— 表单控件仍 **0** 个 `aria-label`；补 `<label for>`/`aria-label`，
      模态框加 `role="dialog"`/`aria-modal`+焦点陷阱；`tests/e2e/` 加 axe-core 冒烟。
- [ ] **G4 JS 模块化（大改，可选）** —— 62 个 JS 仍全局 `<script>` 加载（**0** 条 import/export）；
      中期迁 ES Modules 或单一 `window.DP` 命名空间。

### P5 · 锦上添花

- [ ] P1 零头收尾（见上方灰字：emg_analysis/lineshape/lif 的几个非多态 `Any`）。
- [ ] `peaks: list[Any]` 引入条目级 Pydantic 模型，进一步消除 `_float`/`_int`。
- [ ] 继续拆 ~700 行大模块（已有 CI 比率门兜底）。

---

## 验证方式（每改一项）

```
pytest tests --ignore=tests/e2e
ruff check services web_api tests --select E,F,W,I --ignore E402,E501
python dev_scripts/check_private_service_usage.py
python dev_scripts/check_no_pyplot.py
python dev_scripts/check_services_ratio.py
```
