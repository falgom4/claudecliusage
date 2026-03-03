from playwright.sync_api import sync_playwright
import os


def main():
    user_data_dir = os.path.expanduser("~/.cursor-playwright-session")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
        )
        page = browser.new_page()
        page.goto("https://cursor.com/en-US/dashboard?tab=spending")
        print("Chromium abierto con perfil ~/.cursor-playwright-session. Cierra la ventana para salir.")
        browser.wait_for_event("close")


if __name__ == "__main__":
    main()

