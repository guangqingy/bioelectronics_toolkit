# Design Tokens

This document records the shared WebGUI CSS tokens defined in
`web_static/style.css`. New page components should use these tokens before
introducing new colors, spacing values, or radii.

## Color Tokens

- `--blue`: `#3E6AE1` — primary actions, active navigation, links.
- `--blue-hover`: `#3460d4` — primary button hover state.
- `--success`: `#027A48` — success text and status.
- `--success-bg`: `#ECFDF3` — success status background.
- `--warning`: `#8a5b00` — warning text.
- `--warning-bg`: `#FFF9E8` — warning status background.
- `--error`: `#B42318` — destructive actions and errors.
- `--error-bg`: `#FFF1F0` — error banner and status background.
- `--toast-error`: `#c0392b` — transient error toast background.
- `--error-border-hover`: `#e38b8b` — hover border for destructive controls.
- `--error-text-strong`: `#8f1d14` — high-contrast destructive text.
- `--white`: `#FFFFFF` — panels and content surfaces.
- `--ash`: `#F4F4F4` — subtle hover backgrounds.
- `--app-bg`: `#F7F8FA` — application canvas background.
- `--nav-bg`: `rgba(255,255,255,0.96)` — translucent top navigation surface.
- `--surface-soft`: `#FAFAFA` — path inputs and very quiet input surfaces.
- `--surface-panel`: `#FAFBFD` — secondary cards, empty states, and preview panels.
- `--surface-blue`: `#F2F5FF` — active command/menu backgrounds.
- `--surface-blue-strong`: `#EEF2FF` — selected list rows.
- `--border-muted`: `#d9dde6` — non-primary panel borders.
- `--border-strong`: `#c2c8d4` — emphasized neutral borders and table hover outlines.
- `--border-blue-muted`: `#d7e0f5` — selected command borders.
- `--border-dashed`: `#d7dbe3` — dashed empty-state borders.
- `--code-bg`: `#101828` — JSON/code preview background.
- `--code-fg`: `#f8fafc` — JSON/code preview foreground.
- `--success-border`: `#ABEFC6` — success pill border.
- `--warning-border`: `#FEDF89` — warning pill border.
- `--error-border`: `#FECDCA` — error pill border.
- `--carbon`: `#171A20` — high-emphasis text.
- `--graphite`: `#393C41` — body text.
- `--pewter`: `#5C5E62` — secondary labels.
- `--silver`: `#8E8E8E` — section labels and metadata.
- `--cloud`: `#EEEEEE` — dividers and light borders.
- `--pale`: `#D0D1D2` — disabled borders and controls.

## Layout Tokens

- `--nav-h`: `52px` — fixed top navigation height.
- `--ctrl-w`: `450px` — default left control panel width.
- `--radius-sm`: `4px` — inputs and compact buttons.
- `--radius-md`: `8px` — panels, modals, and repeated cards.
- `--trans`: `0.25s` — standard UI transition duration.

## Usage Rules

- Primary action button: one visible `.btn-primary` per page state.
- Secondary commands: `.btn-secondary`.
- Destructive commands: `.btn-danger` with confirmation.
- Icon-only commands: `.btn-icon` and an accessible label or tooltip.
- Section labels: `.ctrl-label`, uppercase, using `--silver`.
- New controls should fit the page density budget in
  `docs/feature_integration_prompt.md`.
