import re

from playwright.sync_api import expect


def test_dashboard_loads(page, server_url):
    page.goto(server_url)

    expect(page).to_have_title(re.compile("DataProcess"))
    for label in ["ABF", "EChem", "Fluorescence", "Histology"]:
        expect(page.get_by_role("button", name=label)).to_be_visible()
    expect(page.get_by_role("link", name="CSV Viewer", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Run History", exact=True)).to_be_visible()
