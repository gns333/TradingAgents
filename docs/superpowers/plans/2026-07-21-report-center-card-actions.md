# Report Center Card Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复报告中心历史报告卡片右侧决策徽标和删除按钮重叠、截断的问题。

**Architecture:** 保持现有 DOM 与交互不变，仅将历史卡片从叠加定位改为两列 CSS Grid。内容按钮位于可收缩主列，删除按钮位于独立操作列；小屏仅缩短按钮文案，不再使用覆盖定位。

**Tech Stack:** 原生 HTML/CSS/JavaScript、pytest 静态契约测试

## Global Constraints

- 删除按钮始终可见，并继续触发现有删除确认流程。
- 决策徽标继续位于报告标题行右侧。
- 不修改报告 API、数据结构或详情区域。
- 小屏下删除按钮显示紧凑的“×”图标。

---

### Task 1: 为历史报告卡片建立独立操作列

**Files:**
- Modify: `tests/test_web_workbench_static.py`
- Modify: `tradingagents/web/static/workbench.css:1397-1423`

**Interfaces:**
- Consumes: `renderHistoryList()` 生成的 `.history-item > .history-open + .history-delete` DOM 顺序。
- Produces: `.history-item` 两列网格契约，内容与删除操作不再共享覆盖区域。

- [ ] **Step 1: 写入失败的静态样式回归测试**

```python
def test_report_history_card_reserves_a_separate_delete_column():
    css = (STATIC_DIR / "workbench.css").read_text(encoding="utf-8")

    assert ".history-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; }" in css
    assert ".history-open { min-width: 0; width: auto;" in css
    assert "position: static;" in css
    assert "transform: none;" in css
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run: `python -m pytest tests/test_web_workbench_static.py::test_report_history_card_reserves_a_separate_delete_column -q`

Expected: FAIL，缺少 `.history-item` 两列网格规则。

- [ ] **Step 3: 实施最小 CSS 修复**

将报告卡片的后置覆盖规则调整为：

```css
.history-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; }
.history-open { min-width: 0; width: auto; gap: 7px; min-height: 66px; padding: 10px 13px; }
.history-delete {
  position: static;
  align-self: stretch;
  transform: none;
  min-height: 100%;
  padding: 0 10px;
  border: 0;
  border-left: 1px solid var(--line);
  color: var(--faint);
  opacity: 1;
}
```

将 `@media (max-width: 560px)` 中的删除按钮覆盖改为：

```css
.history-delete { width: 32px; padding: 0; font-size: 0; }
```

- [ ] **Step 4: 运行针对性测试并确认通过**

Run: `python -m pytest tests/test_web_workbench_static.py -q`

Expected: PASS。

- [ ] **Step 5: 检查静态资源语法与差异**

Run: `node --check tradingagents/web/static/workbench.js`

Expected: exit code 0。

Run: `git diff --check`

Expected: exit code 0，无输出。

- [ ] **Step 6: 提交修复**

```bash
git add tests/test_web_workbench_static.py tradingagents/web/static/workbench.css docs/superpowers/plans/2026-07-21-report-center-card-actions.md
git commit -m "fix(web): prevent report card action clipping"
```

