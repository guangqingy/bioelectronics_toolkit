import re

from playwright.sync_api import expect


def test_shortcut_button_opens_keyboard_shortcuts(page, server_url):
    page.goto(server_url)
    page.get_by_role("button", name="?", exact=True).click()

    modal = page.locator("#shortcutModal")
    expect(modal).to_have_class(re.compile(r"\bshow\b"))
    expect(modal.get_by_role("dialog")).to_contain_text("Keyboard Shortcuts")
