import re

from playwright.sync_api import expect


def test_settings_modal_tabs_open(page, server_url):
    page.goto(server_url)
    page.get_by_role("button", name="Settings").click()

    overlay = page.locator("#prefsOverlay")
    expect(overlay).to_have_class(re.compile(r"\bshow\b"))
    for name, panel in [
        ("Defaults", "defaults"),
        ("Run History", "history"),
        ("Jobs", "jobs"),
        ("Advanced JSON", "json"),
    ]:
        overlay.get_by_role("button", name=name, exact=True).click()
        expect(overlay.locator(f'[data-prefs-tab-panel="{panel}"]')).to_be_visible()


def test_settings_modal_traps_tab_focus(page, server_url):
    page.goto(server_url)
    page.get_by_role("button", name="Settings").click()

    overlay = page.locator("#prefsOverlay")
    expect(overlay).to_have_class(re.compile(r"\bshow\b"))
    overlay.get_by_role("button", name="Close").focus()
    page.keyboard.press("Shift+Tab")

    active_is_inside = page.evaluate(
        "() => document.querySelector('#prefsOverlay')?.contains(document.activeElement)"
    )
    assert active_is_inside

    for _ in range(20):
        page.keyboard.press("Tab")
        active_is_inside = page.evaluate(
            "() => document.querySelector('#prefsOverlay')?.contains(document.activeElement)"
        )
        assert active_is_inside
