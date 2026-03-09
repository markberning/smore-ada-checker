"""Core orchestration logic, separated from CLI for future web app use."""
import os
import tempfile
from datetime import date
from urllib.parse import urlparse

from .scraper import launch_browser, close_browser, load_page, scrape_page, capture_issue_screenshots
from .checks import run_all_checks
from .report import generate_report


async def run_audit(url: str, output_dir: str = ".", verbose: bool = True) -> str:
    """Run a full accessibility audit on a Smore page.

    Args:
        url: The Smore post URL to audit.
        output_dir: Directory to save the PDF report.
        verbose: Whether to print progress messages.

    Returns:
        Path to the generated PDF report.
    """
    # Derive slug and output filename
    slug = urlparse(url).path.strip("/").split("/")[-1]
    today = date.today().strftime("%Y-%m-%d")
    output_filename = f"smore-report-{slug}-{today}.pdf"
    output_path = os.path.join(output_dir, output_filename)

    # Temp dir for screenshots
    screenshot_dir = tempfile.mkdtemp(prefix="smore_screenshots_")

    if verbose:
        print(f"Auditing: {url}")
        print(f"Output: {output_path}")
        print()

    pw, browser = await launch_browser()

    try:
        # 1. Load and scrape the page
        if verbose:
            print("Loading page...")
        page = await load_page(browser, url)

        if verbose:
            print("Scraping page structure...")
        page_data = await scrape_page(page, url)

        if verbose:
            print(f"Found {len(page_data.sections)} sections, "
                  f"{sum(len(s.images) for s in page_data.sections)} images, "
                  f"{sum(len(s.links) for s in page_data.sections)} links")
            print()

        # 2. Run all checks
        if verbose:
            print("Running accessibility checks...")
        issues = run_all_checks(page_data, verbose=verbose)

        if verbose:
            print(f"\nFound {len(issues)} issue(s)")
            print()

        # 3. Capture screenshots for each issue
        if issues:
            if verbose:
                print("Capturing screenshots...")
            await capture_issue_screenshots(page, issues, screenshot_dir)

        # 4. Generate report
        if verbose:
            print("Generating PDF report...")
        await generate_report(issues, url, page_data.title, output_path)

        if verbose:
            print(f"\nReport saved to: {output_path}")

        return output_path

    finally:
        await close_browser(pw, browser)
