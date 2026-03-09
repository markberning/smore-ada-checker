# Smore ADA Accessibility Checker

Automated accessibility checker for Smore newsletter pages. Scrapes a Smore URL, runs accessibility checks, and generates a PDF report with screenshots of each issue found.

## What It Checks

- **Images** -- Missing or poor alt text, decorative images not marked correctly, flyer images with text that should be in the page body
- **Links** -- Generic link text ("click here", "read more"), broken links, links that redirect unexpectedly
- **Headings** -- Heading hierarchy issues (skipped levels, missing H1)
- **Embeds** -- Inaccessible embedded content
- **Emojis** -- Excessive emoji usage that impacts screen readers

The checker uses Claude AI vision to evaluate image alt text quality and identify flyer/infographic images whose content should also appear as text.

## Prerequisites

- **Python 3.12+** (developed on 3.14)
- **Anthropic API key** -- Required for the AI-powered image checks

## Setup

1. **Clone the repo:**

   ```
   git clone <repo-url>
   cd smore-ada-checker
   ```

2. **Create a virtual environment and install dependencies:**

   ```
   python -m venv venv
   venv\Scripts\activate        # Windows
   # source venv/bin/activate   # Mac/Linux
   pip install -r requirements.txt
   ```

3. **Install Playwright browsers:**

   ```
   playwright install chromium
   ```

4. **Set up your API key:**

   Copy the example env file and add your key:

   ```
   copy .env.example .env       # Windows
   # cp .env.example .env       # Mac/Linux
   ```

   Edit `.env` and replace `sk-ant-...` with your actual Anthropic API key:

   ```
   ANTHROPIC_API_KEY=sk-ant-your-key-here
   ```

## Usage

```
python check.py <smore-url>
```

**Example:**

```
python check.py https://app.smore.com/n/jtycf
```

The tool will:

1. Open the Smore page in a headless browser
2. Scrape the page structure (sections, images, links, headings)
3. Run all accessibility checks (including AI vision analysis of images)
4. Capture screenshots of each issue for the report
5. Generate a PDF report in the current directory

**Output file:** `smore-report-<slug>-<date>.pdf`

For the example above, the report would be saved as something like `smore-report-jtycf-2026-03-09.pdf`.

## Troubleshooting

- **"Error: URL must be a Smore page"** -- The URL must contain `smore.com`. Make sure you're using the full Smore newsletter URL.
- **Playwright errors** -- Make sure you ran `playwright install chromium` after installing packages.
- **API key errors** -- Check that your `.env` file exists and contains a valid `ANTHROPIC_API_KEY`.
- **Timeout errors** -- Some Smore pages are large. The page needs to fully load before scraping. Try again if you hit a timeout.
