import re

from playwright.sync_api import expect


def test_command_palette_abf_jump(page, server_url):
    page.goto(server_url)
    page.keyboard.press("Control+K")

    expect(page.locator("#commandPalette")).to_have_class(re.compile(r"\bshow\b"))
    page.locator("#commandSearch").fill("abf")
    expect(page.locator("#commandList .command-item").first).to_contain_text("ABF Viewer")
    page.keyboard.press("Enter")
    page.wait_for_url("**/abf/viewer")
