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
- `--white`: `#FFFFFF` — panels and content surfaces.
- `--ash`: `#F4F4F4` — subtle hover backgrounds.
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
