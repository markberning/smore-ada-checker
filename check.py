"""CLI entry point for Smore ADA Accessibility Checker."""
import asyncio
import os
import sys

# Fix Windows console encoding for emoji/unicode
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

from smore_checker.core import run_checks, run_audit
from smore_checker.report import generate_report
from smore_checker.review_server import start_review_server
from smore_checker.vision_router import clear_cache, set_verbose, reset_stats, get_stats, provider_name


def main():
    args = sys.argv[1:]

    # Handle flags
    verbose_mode = "--verbose" in args
    clear_cache_mode = "--clear-cache" in args
    no_review_mode = "--no-review" in args

    # Remove flags from args
    args = [a for a in args if not a.startswith("--")]

    if clear_cache_mode:
        clear_cache()
        print("Vision cache cleared.")
        if not args:
            return

    if not args:
        print("Usage: python check.py [--verbose] [--clear-cache] [--no-review] <smore-url>")
        print("Example: python check.py https://app.smore.com/n/jtycf")
        print()
        print("Options:")
        print("  --verbose      Show detailed vision API call logging")
        print("  --clear-cache  Clear the vision result cache before running")
        print("  --no-review    Skip the review step and generate the PDF directly")
        sys.exit(1)

    url = args[0]
    if "smore.com" not in url:
        print("Error: URL must be a Smore page (smore.com)")
        sys.exit(1)

    # Startup message
    print(f"Using vision provider: {provider_name}")

    # Set up verbose vision logging
    if verbose_mode:
        set_verbose(True)
    reset_stats()

    try:
        if no_review_mode:
            # Direct PDF generation (old workflow)
            asyncio.run(run_audit(url))
        else:
            # Two-stage workflow: scrape + check, then review
            issues, page_url, page_title, output_path = asyncio.run(run_checks(url))

            # Print vision stats
            stats = get_stats()
            print(f"\nVision stats: {stats['api_calls']} API calls, {stats['cache_hits']} cache hits, {stats['elapsed']:.1f}s total")

            if not issues:
                # No issues - generate directly, no review needed
                print("\nNo issues found. Generating clean report...")
                asyncio.run(generate_report(issues, page_url, page_title, output_path))
                print(f"Report saved to: {output_path}")
            else:
                # Start review server
                start_review_server(issues, page_url, page_title, output_path)

            return  # Skip stats printing below

    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)

    # Print stats summary (only for --no-review mode)
    stats = get_stats()
    print(f"\nVision stats: {stats['api_calls']} API calls, {stats['cache_hits']} cache hits, {stats['elapsed']:.1f}s total")


if __name__ == "__main__":
    main()
