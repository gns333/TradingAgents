# TradingAgents Web Workbench Redesign

Date: 2026-07-10
Status: Approved for planning

## Problem

The current web UI is a single page that mixes login identity, single-stock analysis,
sector screening, agent progress, reports, and status cards. The admin page uses a
separate visual system. This makes the product hard to learn, hard to operate during
long-running analysis, and harder to extend for CloudBase deployment.

## Goals

- Turn the app into a coherent analysis workbench with clear top-level navigation.
- Separate ordinary user workflows from admin-only configuration.
- Make login and whitelist status visible before users start expensive operations.
- Make single-stock analysis, sector screening, reports, and admin management distinct
  surfaces while keeping fast movement between them.
- Preserve existing FastAPI endpoints and SSE behavior unless a small UI-support API is
  clearly needed.
- Keep the first implementation within the current static HTML and vanilla JavaScript
  stack to avoid pausing product work for a frontend framework migration.

## Non-Goals

- Do not solve the core-vs-peripheral sector relevance engine in this UI pass.
- Do not migrate to React, Vue, or a build tool in this iteration.
- Do not change the database storage model for encrypted API keys.
- Do not change the multi-agent graph orchestration.

## Information Architecture

The app becomes one integrated shell:

- `个股分析`: run a single-stock multi-agent analysis.
- `板块筛选`: generate a candidate pool and optionally send one candidate into analysis.
- `报告中心`: read generated report sections with stable tabs and larger reading space.
- `后台管理`: configure whitelist and model credentials, visible only after admin login.

The root route `/` serves the workbench. The existing `/admin` route remains available
as a compatibility entry during this iteration, but it links users back to the integrated
admin workspace. The primary admin experience lives inside the workbench shell.

## App Shell

Desktop layout:

- Left sidebar: product name, primary navigation, user identity summary, admin entry.
- Top bar: current page title, global run state, active identity, quick admin/login action.
- Main content: one active workspace page.
- No default right rail in this iteration; the active run summary stays inside the current
  workspace to keep the static implementation small.

Mobile layout:

- Top bar with current page and identity state.
- Bottom or top segmented navigation for the four main sections.
- Single-column forms and stacked results.

The shell must not show unrelated forms on the same screen. Each page should have one
primary task and one primary action.

## Login And Identity

The app distinguishes two identity modes:

- User identity: email or CloudBase-provided identity used for whitelist checks.
- Admin session: bearer token or cookie returned by `/api/admin/login`.

User identity is entered or detected once and displayed in the shell. Analysis and
screening requests reuse that identity instead of embedding an email field inside each
form. If the account is missing or not whitelisted, the relevant page shows a clear
blocked state before or immediately after the request.

Admin login opens an inline panel or modal from the shell. If no admin password exists,
the setup flow is shown first. After login, `后台管理` becomes accessible.

## Single-Stock Analysis Page

Primary controls:

- Stock code input.
- Analysis date input.
- Analyst module multi-select with clear labels: market, news, fundamentals, social.
- Primary action: `开始分析`.

Main content:

- Agent team status board with pending, active, done, and failed states.
- Run timeline with collapsed long output by default.
- Reports preview area that links to `报告中心`.

Behavior:

- Starting a run resets only the run-specific state.
- SSE connection states are visible: connecting, running, completed, failed, disconnected.
- The start button disables during active analysis and re-enables on terminal states.
- A candidate selected from `板块筛选` fills the stock code and switches to this page without
  auto-starting analysis.

## Sector Screening Page

Primary controls:

- Board type selector: auto, concept, industry.
- Sector input for this iteration. Board-list selection is a later feature and is outside
  this UI restructuring pass.
- Candidate count.
- AI review toggle.
- Primary action: `筛选板块`.

Main content:

- Candidate table optimized for scanning: rank, stock, score, component scores, reasons,
  risks, AI review, action.
- Result summary above the table: sector, board type, candidate count, AI review status.
- Each row has `带入分析`, which fills the single-stock analysis form and switches pages.

Behavior:

- Screening never auto-runs single-stock analysis.
- Loading, empty, and failure states are shown in place.
- Large Markdown screening reports remain available below the table but are visually
  secondary to the candidate table.

## Report Center

The report center provides a stable reading surface for the current run:

- Tabs: market, news, fundamentals, social, research manager, trader, final decision.
- Empty sections remain visible as pending tabs when the run has started.
- Markdown is rendered consistently with tables, lists, headings, code blocks, and
  readable line lengths.
- The final decision is visually emphasized after completion, but not hidden inside a
  decorative card.

Reports should be available during and after the run. The run timeline remains separate
from the report reading area.

## Admin Management

Admin management is part of the same shell and uses the same visual system.

Sections:

- Admin session: setup/login/logout state.
- Model configs: provider, display name, base URL, quick model, deep model, API key,
  enabled/default status.
- Whitelist: email, UID, status, daily limit, note.

Security presentation:

- API keys are never displayed raw after save.
- Copy explains that keys are encrypted in the app database with an app-managed key.
- Admin-only actions call existing protected endpoints with the admin token.

## Visual System

Style direction: quiet, professional, data-workbench UI.

Principles:

- Use a restrained neutral palette with one primary accent and semantic status colors.
- Avoid large marketing-style hero sections.
- Avoid nested cards and decorative background effects.
- Use tables for dense candidate/admin data.
- Use segmented controls, tabs, checkboxes, selects, and plain icon/text buttons where
  they fit the task.
- Keep page sections full-width inside the workspace instead of floating card stacks.
- Text must fit on mobile and desktop without overlap.

Suggested token families:

- Background: neutral off-white.
- Surface: white.
- Border: light neutral.
- Text: dark neutral.
- Muted text: medium neutral.
- Accent: restrained green or blue-green for primary actions.
- Status: success, warning, danger with text labels, not color alone.

## Accessibility And Responsiveness

- All form controls have visible labels.
- Interactive targets are at least 44px high on touch layouts.
- Focus states remain visible.
- Navigation has active states and keyboard-reachable controls.
- Error messages appear near the related area and include a recovery path.
- Mobile layout avoids horizontal page scroll; data tables may scroll inside their own
  container when necessary.
- Reduced-motion users should not depend on animation for understanding state changes.

## Implementation Boundaries

First implementation should stay within:

- `tradingagents/web/static/index.html`
- `tradingagents/web/static/admin.html` as a compatibility bridge into the workbench
- `tradingagents/web/api.py` for the minimum support endpoints required by the UI
- Existing admin and analysis APIs

If the single HTML file becomes too large during implementation, split CSS and JavaScript
into plain static files in the same pass. Do not introduce a frontend build pipeline.

## Testing And Verification

Manual UI verification:

- Open the workbench at `/`.
- Confirm navigation switches between all major sections.
- Confirm user identity is entered once and reused by analysis and screening.
- Confirm blocked whitelist states are visible.
- Confirm single-stock analysis starts, streams, completes, and shows reports.
- Confirm sector screening returns candidates and `带入分析` fills the analysis form without
  starting a run.
- Confirm admin setup/login, whitelist editing, and model config editing work.

Automated or lightweight verification:

- Python syntax check for touched backend files.
- Existing admin API tests once `pytest` is available.
- Browser smoke check of static page interactions with the local workbench URL.

## Rollout

Implement in one UI-focused pass:

1. Create the app shell and navigation.
2. Move identity handling into the shell.
3. Rebuild the single-stock analysis workspace.
4. Rebuild sector screening as a separate workspace.
5. Integrate admin management into the shell.
6. Preserve `/admin` as compatibility or link it to the integrated admin view.
7. Verify the four core workflows end to end.
