# Upbit Watch — Fully Configurable Dashboard + UI/UX Refresh

**Date:** 2026-06-21
**Status:** Approved (design)
**Scope:** Make the control panel (`static/index.html`) fully user-configurable and refine its UI/UX, keeping the dark control-panel identity and adding theming.

---

## 1. Goal & Non-Goals

**Goal.** Turn the live control panel into something the user can fully configure — detector behavior, alerts, appearance, and display — and sharpen its visual hierarchy, empty states, mobile layout, and accessibility. Add a real light mode + accent theming.

**Non-goals (YAGNI / stretch — not in v1):**
- Drag-and-drop reordering of panels/tabs.
- Custom KRW→USDT symbol-mapping overrides.
- Multiple named layout profiles.
- Any change to the detection strategy itself, or going-live/VPS concerns.

These can be added later behind the same config plumbing.

## 2. Approach

**Split static assets, single typed server config.** Keep the no-build-step, CDN-library architecture so it still deploys unchanged to the Seoul VPS. Split the one large HTML file into focused assets, and centralize a typed, validated server config.

- `static/index.html` — markup / Alpine templates only.
- `static/app.js` — Alpine `app()` logic + theme/prefs store (new).
- `static/theme.css` — CSS-variable theme system (new).
- `app.py` — expanded config model, expanded `GET/PUT /api/settings`, `StaticFiles` mount, runtime wiring of tunables.
- `config.json` — expanded server-side schema.
- `detector.py` / `notice.py` / `phase0.py` — read new tunables at runtime (with current values as defaults).

Settings UI is hand-built (not schema-auto-rendered) so it stays polished.

## 3. Persistence model (hybrid)

### Server-side — `config.json` (+ `.env` for secrets), via `PUT /api/settings`
Affects the running bot. GET never returns secrets.

| Setting | Default | Backend work |
|---|---|---|
| `poll_interval` | 1.0 | exists |
| `poll_interval_notice` | 5.0 | exists |
| `autostart` | true | exists in config; expose toggle |
| `phase0_offsets` (sec list) | `[0,10,30,60,300]` | parameterize `phase0.py` |
| `phase0_sources` `{bybit,binance}` | `{true,true}` | honor toggles in `phase0.py` |
| `notice_keywords` `{listing[], exclude[]}` | current hardcoded set | `notice.py` reads from config |
| `alert_on_listing` | true | gate `telegram.send` (engine) |
| `alert_on_notice` | true | gate `telegram.send` (notice) |
| `alert_on_error` | false | optional error alert (engine/notice) |
| `quiet_hours` `{enabled,start,end}` | disabled | gate Telegram server-side |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | exist (`.env`) |

Validation: clamped/typed ranges (e.g. `poll_interval >= 0.2`, `poll_interval_notice >= 1.0`, offsets sorted non-negative ints, keyword lists of non-empty strings, quiet-hours `HH:MM`). Invalid fields are rejected per-field, never crash.

### Browser-side — `localStorage` key `upbitwatch.prefs` (UI only)
- **Appearance:** `theme` (dark/light/system), `accent` (amber/violet/blue/green/rose), `density` (comfortable/compact), `fontScale` (sm/base/lg).
- **Layout:** `defaultTab`, `visibleTabs[]`, `visibleSidebarCards[]`.
- **Display:** `timeFormat` (local/utc/relative), `tablePageSize`, `numberFormat` (`{decimals, grouping}`).
- **Data:** `favorites[]` (pinned markets, "favorites first" sort + star toggle).
- **Notifications (client):** `toastDuration`, `toastSound` (bool), `toastEvents` (which events raise a toast).

## 4. Settings surface (redesign)

Promote the right-side drawer to a wider panel with a left rail of sections:

1. **Detector** — poll intervals, autostart, Phase 0 offsets + sources, Loop B keywords.
2. **Alerts** — alert-on rules, quiet hours (server); toast duration/sound/events (client).
3. **Appearance** — theme, accent, density, font scale.
4. **Display** — time format, table page size, number format, default tab, visible tabs/cards.
5. **Telegram** — token/chat id, send test (existing).
6. **Reset** — per-section + global "reset to defaults".

Each control: clear label, helptext, inline validation, "saved" feedback. A subtle badge distinguishes **server** ("affects the bot") vs **local** ("this browser") settings. Panel is focus-trapped, ESC-closes, fully keyboard-navigable.

## 5. Theming system (technical linchpin)

- Define theme colors as **RGB channel triplets** in CSS custom properties: `:root { --card: 30 41 59; ... }` (dark) and `:root[data-theme="light"] { ... }` (light).
- Repoint the Tailwind CDN config colors to `rgb(var(--card) / <alpha-value>)`. This keeps **every existing alpha utility** (`bg-muted/50`, `border-primary/50`, `bg-ok/15`, …) working in both themes with **zero class rewrites** — the key to a low-risk refit.
- **Accent presets** set `--primary` (+ `--accent`). **Density** via `:root[data-density="compact"]` overrides on key paddings/row heights. **Font scale** via root font-size.
- **No FOUC:** a tiny inline `<head>` script reads `localStorage` and sets `data-theme` / `data-accent` / `data-density` / font scale on `<html>` before first paint.
- "System" theme follows `prefers-color-scheme`; keep the existing `prefers-reduced-motion` handling.

## 6. Backend wiring (behavior changes)

- `phase0.py`: read `phase0_offsets` + `phase0_sources` from config (constructor/attr), defaulting to today's values; skip a source when toggled off; gaps stay non-fatal.
- `notice.py`: `parse_notice` reads listing/exclude keyword lists from config instead of hardcoded literals; defaults preserve current behavior.
- Alert gating: engine `_handle_new` and notice `_handle` check `alert_on_*` + quiet-hours before calling `telegram.send`; toasts/WS events are unaffected (UI still shows everything).
- `GET /api/settings` returns the full server config (no secrets); `PUT` validates per-field and applies at runtime (most values are already mutable live).

## 7. Components & boundaries

- **Config service (`app.py`)** — load/validate/save typed config; single source of truth for server tunables. Inputs: JSON body. Outputs: sanitized config dict.
- **Theme/prefs store (`app.js`)** — owns `localStorage` prefs, applies them to the DOM, exposes reactive getters to templates. Independent of server config.
- **Settings panel (`index.html` + `app.js`)** — renders the six sections; writes server settings via API, local prefs via the store.
- **Display helpers (`app.js`)** — time formatting, number formatting, pagination, favorites sort — pure functions driven by prefs.
- **Detector tunable readers (`phase0.py`, `notice.py`)** — each reads its slice of config at runtime.

Each unit is independently understandable and testable; the theme store and config service never depend on each other.

## 8. Verification

- **Backend (pytest):** `/api/settings` load → validate → save round-trip; rejection of out-of-range values; `phase0`/`notice` read config correctly; alert gating respects rules + quiet hours.
- **Frontend (Playwright MCP):** theme/accent/density switching with **no FOUC**; a server-setting change reflected in `config.json` and bot behavior; localStorage prefs persist across reload; mobile/responsive layout; a11y (focus trap, keyboard nav, contrast in both themes).
- **ui-ux-pro-max review pass** on the refined UI before calling it done.

## 9. Risks

- **Tailwind alpha utilities + CSS vars:** must use the channel-triplet + `<alpha-value>` pattern or alpha classes break. Mitigated by doing the color refit first and visually diffing dark mode against the current look.
- **Light-mode contrast:** the palette is dark-tuned; light mode needs its own carefully chosen tokens, validated for WCAG AA.
- **Config migration:** older `config.json` lacks new keys — loader fills defaults for any missing key so existing installs keep working.
