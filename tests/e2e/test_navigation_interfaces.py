from playwright.sync_api import expect

TOP_NAV_ROUTES = [
    "/",
    "/abf/viewer",
    "/abf/batch",
    "/abf/peaks",
    "/abf/figure",
    "/echem/photocurrent",
    "/echem/photovoltage",
    "/echem/lineshape",
    "/emg/analysis",
    "/emg/peak-selection",
    "/csv",
    "/fluorescence",
    "/fluorescence/lif",
    "/fluorescence/roi",
    "/fluorescence/gif",
    "/fluorescence/timecourse",
    "/fluorescence/kymograph",
    "/fluorescence/3d-stacking",
    "/histology/naming",
    "/histology/analysis",
    "/runs",
]


def test_top_nav_pages_load_without_interface_errors(page, server_url):
    browser_errors = []
    page.on("pageerror", lambda exc: browser_errors.append(str(exc)))
    page.on(
        "console",
        lambda msg: browser_errors.append(msg.text) if msg.type == "error" else None,
    )

    for route in TOP_NAV_ROUTES:
        browser_errors.clear()
        page.goto(f"{server_url}{route}")
        expect(page.locator(".top-nav")).to_be_visible()
        expect(page.locator("#errorBanner")).to_be_hidden()
        assert browser_errors == [], f"{route} emitted browser errors: {browser_errors}"


def test_visible_controls_have_accessible_names(page, server_url):
    for route in TOP_NAV_ROUTES:
        page.goto(f"{server_url}{route}")
        unnamed = page.evaluate(
            """
            () => {
              const visible = el => !!(el.offsetParent || el.getClientRects().length);
              const text = value => String(value || '').replace(/\\s+/g, ' ').trim();
              const labelledBy = el => text((el.getAttribute('aria-labelledby') || '')
                .split(/\\s+/)
                .map(id => document.getElementById(id)?.textContent || '')
                .join(' '));
              const nativeLabel = el => el.labels && el.labels.length
                ? text(Array.from(el.labels).map(label => label.textContent || '').join(' '))
                : '';
              const hasName = el => {
                if (text(el.getAttribute('aria-label')) || labelledBy(el) || nativeLabel(el)) return true;
                if (el.tagName === 'BUTTON') return !!text(el.textContent || el.value || el.title);
                if (/^(button|submit|reset)$/i.test(el.type || '')) return !!text(el.value || el.title);
                return false;
              };
              const controls = Array.from(
                document.querySelectorAll('input:not([type="hidden"]), select, textarea, button')
              ).filter(visible);
              return controls
                .filter(el => !hasName(el))
                .slice(0, 20)
                .map(el => el.outerHTML.slice(0, 220));
            }
            """
        )
        assert unnamed == [], f"{route} has unnamed visible controls: {unnamed}"


def test_visible_dialogs_have_modal_semantics(page, server_url):
    page.goto(server_url)
    page.get_by_role("button", name="Settings").click()
    dialogs = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('.modal-card'))
          .filter(el => !!(el.offsetParent || el.getClientRects().length))
          .map(el => ({
            role: el.getAttribute('role'),
            modal: el.getAttribute('aria-modal'),
            html: el.outerHTML.slice(0, 180),
          }))
        """
    )

    assert dialogs, "Expected at least one visible settings dialog"
    assert all(item["role"] == "dialog" for item in dialogs), dialogs
    assert all(item["modal"] == "true" for item in dialogs), dialogs
