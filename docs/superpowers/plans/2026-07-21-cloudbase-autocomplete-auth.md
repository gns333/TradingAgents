# CloudBase Autocomplete Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure headerless frontend GET requests, including stock autocomplete, carry the current CloudBase Bearer token on the authenticated app domain.

**Architecture:** Fix the shared `apiJson()` request boundary instead of special-casing autocomplete. When CloudBase auth is active, merge the refreshed token into either the caller's existing headers or a newly created empty headers object.

**Tech Stack:** Browser JavaScript, pytest static workbench contract tests, Node.js syntax checking

## Global Constraints

- Preserve local-mode behavior and existing caller-provided headers.
- Do not weaken `/api/stocks/search` or CloudBase gateway authentication.
- Do not change stock directory data, autocomplete UI, or debounce timing.
- Run only the focused Web workbench tests and JavaScript syntax check.

---

### Task 1: Authenticate Headerless CloudBase API Requests

**Files:**
- Modify: `tests/test_web_workbench_static.py`
- Modify: `tradingagents/web/static/workbench.js:576-582`

**Interfaces:**
- Consumes: `state.runtime.auth`, `state.cloudbaseAuth.getAccessToken()`, `state.accessToken`, and optional `options.headers`.
- Produces: `apiJson(url, options)` always passes `Authorization: Bearer <latest token>` for authenticated CloudBase requests, including callers that omit headers.

- [ ] **Step 1: Write the failing regression test**

Add this test beside the existing authenticated polling and autocomplete contracts:

```python
def test_api_json_authenticates_headerless_cloudbase_requests():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    api_start = js.index("async function apiJson(url, options = {})")
    api_body = js[api_start : api_start + 1000]
    assert "&& options.headers" not in api_body
    assert "...(options.headers || {})" in api_body
    assert "Authorization: `Bearer ${state.accessToken}`" in api_body
```

- [ ] **Step 2: Run the regression test and verify RED**

Run:

```bash
pytest tests/test_web_workbench_static.py::test_api_json_authenticates_headerless_cloudbase_requests -q
```

Expected: FAIL because the current guard contains `&& options.headers` and the merge does not create headers for a headerless GET.

- [ ] **Step 3: Implement the minimal shared-boundary fix**

Change `apiJson()` to:

```javascript
async function apiJson(url, options = {}) {
  if (state.runtime.auth === 'cloudbase' && state.cloudbaseAuth && state.accessToken) {
    const tokenResult = await state.cloudbaseAuth.getAccessToken();
    state.accessToken = tokenResult?.accessToken || tokenResult?.data?.accessToken || state.accessToken;
    options = {
      ...options,
      headers: {
        ...(options.headers || {}),
        Authorization: `Bearer ${state.accessToken}`
      }
    };
  }
  // Keep the remainder of apiJson unchanged.
}
```

- [ ] **Step 4: Run focused verification and verify GREEN**

Run:

```bash
pytest tests/test_web_workbench_static.py -q
node --check tradingagents/web/static/workbench.js
```

Expected: all workbench static tests PASS and Node exits successfully without syntax errors.

- [ ] **Step 5: Commit the regression fix**

```bash
git add tests/test_web_workbench_static.py tradingagents/web/static/workbench.js docs/superpowers/plans/2026-07-21-cloudbase-autocomplete-auth.md
git commit -m "fix(web): authenticate stock autocomplete requests"
```
