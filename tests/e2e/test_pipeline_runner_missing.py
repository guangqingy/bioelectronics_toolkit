import re

from playwright.sync_api import expect


def test_pipeline_runner_shows_missing_scripts(page, server_url):
    page.goto(f"{server_url}/scripts")

    expect(page.get_by_text(re.compile("local script missing", re.I)).first).to_be_visible()
