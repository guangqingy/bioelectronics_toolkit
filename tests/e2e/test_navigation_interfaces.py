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
