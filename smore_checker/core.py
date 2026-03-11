"""Core orchestration logic, separated from CLI for future web app use."""
import os
import tempfile
from datetime import date
from urllib.parse import urlparse

from .scraper import launch_browser, close_browser, load_page, scrape_page, capture_issue_screenshots
from .checks import run_all_checks
from .report import generate_report
from .vision_router import provider_name, provider_label

from .models import Issue


async def run_checks(url: str, verbose: bool = True) -> tuple[list[Issue], str, str, str]:
    """Scrape a Smore page, run accessibility checks, and capture screenshots.

    Does NOT generate the PDF -- the caller decides what to do with the results
    (e.g. open the review server or generate the PDF directly).

    Args:
        url: The Smore post URL to audit.
        verbose: Whether to print progress messages.

    Returns:
        Tuple of (issues, page_url, page_title, output_path).
    """
    # Derive slug and output filename
    slug = urlparse(url).path.strip("/").split("/")[-1]
    today = date.today().strftime("%Y-%m-%d")
    output_filename = f"smore-report-{slug}-{today}-{provider_label}.pdf"

    # Save to reports/ folder in project root
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    output_path = os.path.join(reports_dir, output_filename)

    # Temp dir for screenshots
    screenshot_dir = tempfile.mkdtemp(prefix="smore_screenshots_")

    if verbose:
        print(f"Auditing: {url}")
        print(f"Output: {output_path}")
        print()

    pw, browser = await launch_browser()

    try:
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

        if verbose:
            print("Running accessibility checks...")
        issues = run_all_checks(page_data, verbose=verbose)

        if verbose:
            print(f"\nFound {len(issues)} issue(s)")
            print()

        if issues:
            if verbose:
                print("Capturing screenshots...")
            await capture_issue_screenshots(page, issues, screenshot_dir)

        return issues, url, page_data.title, output_path

    finally:
        await close_browser(pw, browser)


async def run_audit(url: str, verbose: bool = True) -> str:
    """Run a full accessibility audit and generate the PDF directly (no review step).

    Args:
        url: The Smore post URL to audit.
        verbose: Whether to print progress messages.

    Returns:
        Path to the generated PDF report.
    """
    issues, page_url, page_title, output_path = await run_checks(url, verbose)

    if verbose:
        print("Generating PDF report...")
    await generate_report(issues, page_url, page_title, output_path)

    if verbose:
        print(f"\nReport saved to: {output_path}")

    return output_path
