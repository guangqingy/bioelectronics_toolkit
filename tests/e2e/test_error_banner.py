from playwright.sync_api import expect


def test_error_banner_shows_and_dismisses_api_error(page, server_url):
    page.goto(server_url)
    page.evaluate(
        """
        async () => {
          const result = await api('/api/definitely_missing_endpoint', {});
          if (result.error) showErrorBanner(result.error);
        }
        """
    )

    banner = page.locator("#errorBanner")
    expect(banner).to_be_visible()
    banner.get_by_role("button", name="Dismiss").click()
    expect(banner).to_be_hidden()
