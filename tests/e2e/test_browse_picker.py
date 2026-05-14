from playwright.sync_api import expect


def test_abf_browse_picker_scans_examples(page, server_url, repo_root):
    examples = str(repo_root / "examples")
    page.route("**/api/system/select_folder", lambda route: route.fulfill(json={"path": examples}))

    page.goto(f"{server_url}/abf/viewer")
    page.get_by_role("button", name="Choose").click()

    expect(page.locator("#fileList")).to_contain_text("sample_patch_clamp.abf")
