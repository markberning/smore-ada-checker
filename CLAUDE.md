When you need my input, are asking me a question, or have finished a task, run this PowerShell command to alert me:

New-BurntToastNotification -Text "Claude Code", "Waiting for your input"

## Workflow

The tool runs in two stages:

1. **Scrape + Check** (`core.run_checks`): Launches Playwright, scrapes the Smore post, runs all accessibility checks, captures screenshots. Returns issues list and metadata.
2. **Review** (`review_server.start_review_server`): Spins up a local Flask server on `localhost:5050`, opens the review page in the default browser. User includes/excludes issues and edits suggestions. Clicking "Generate Final Report" produces the PDF via Playwright and shuts down the server.

If no issues are found, the PDF is generated directly without the review step.

Use `--no-review` to skip the review step and generate the PDF immediately (old behavior).

## Vision Providers

Configurable via `VISION_PROVIDER` env var in `.env`:
- `claude` (default) — Claude Sonnet (`claude-sonnet-4-20250514`) via `anthropic` package. Requires `ANTHROPIC_API_KEY`.
- `gemini` — Gemini Flash (`gemini-2.5-flash`) via `google-genai` SDK. Requires `GOOGLE_API_KEY`.

Architecture: `vision_claude.py` and `vision_gemini.py` are thin API-only provider modules. `vision_router.py` handles all shared infrastructure (caching, batching, pre-filtering, rate limiting, verbose logging) and delegates to the active provider.

## Review Page — Card Types and Editable Fields

Cards with editable fields (user edits go into the final PDF):
- **Image alt text** (Missing Alt Text, Filename as Alt Text, Alt Text Too Long, Ineffective Alt Text, Duplicate Alt Text): `<textarea>` pre-populated with AI suggested alt text
- **Link text** (Generic Link Text, Vague Link Text, Raw URL as Link Text): `<input type="text">` pre-populated with AI suggested link text
- **Flyer content** (Flyer Info Not in Text): `<textarea>` pre-populated with missing details

Cards with exclude toggle only (factual findings, standardized messages):
- Heading issues (Skipped Heading Level)
- Emoji issues (Emoji in Heading, Consecutive Emojis, Emojis as Bullet Points, Emoji at Start of Text)
- File download links (Links to File Download)
- Video issues (Direct Video File Link, Non-YouTube Video Embed)
- QR code issues (QR Code Without Link)

## WCAG 2.1 AA Compliance Requirement

This tool checks Smore posts for accessibility. The review page and ALL future UI must itself be fully WCAG 2.1 AA compliant. Apply every item on this checklist:

### Structure and Semantics
- "Skip to main content" link as the very first focusable element — visually hidden by default, visible on focus
- Proper HTML5 landmark regions: `<header>`, `<nav>`, `<main>`, `<footer>` — no divs standing in for landmarks
- One `<h1>` per page, proper heading hierarchy with no skipped levels
- Unique descriptive `<title>` tag on every page

### Tables (if used)
- `<th>` for all header cells with `scope="col"` or `scope="row"`
- `<caption>` describing the table content
- Tables for tabular data only, never for layout

### Forms
- Every input has a `<label>` with matching `for`/`id` pair — placeholder text alone is not sufficient
- Required fields indicated both visually and with the `required` attribute
- Error messages linked to their field with `aria-describedby`
- Related fields grouped with `<fieldset>` and `<legend>`
- Errors never conveyed by color alone — must include icon or "Error:" text

### Keyboard and Focus
- Tab order follows visual reading order — DOM order must be logical
- Visible focus indicators on every interactive element — never `outline: none` without replacement
- No keyboard traps — modals must trap focus inside, Escape closes and returns focus to trigger
- Custom interactive elements must have proper ARIA roles and handle keyboard events

### Color and Contrast
- Normal text: 4.5:1 minimum contrast ratio
- Large text (18pt+ or 14pt+ bold): 3:1 minimum
- UI components and icons: 3:1 against adjacent colors
- Grayed-out excluded cards must still meet 4.5:1
- Never use color as the only indicator — errors, states, categories must also use text or icons
- No `user-scalable=no` in viewport meta tag

### Images and Icons
- All `<img>` elements have `alt` attribute — empty `alt=""` for decorative, descriptive text for content
- Icon-only buttons must have `aria-label`

### Dynamic Content and ARIA
- Content that updates without page reload must use `aria-live` region — `aria-live="polite"` for non-urgent
- Issue count status must be in an `aria-live="polite"` region
- Loading states communicated to screen readers
- Fix bad HTML before reaching for ARIA
- `aria-expanded` on buttons controlling collapsible content
- `aria-current="page"` on active nav link when navigation exists

### Modals (if used)
- `role="dialog"`, `aria-modal="true"`, `aria-labelledby` pointing to modal heading
- Focus moves into modal on open, trapped inside, returns to trigger on close

### Links and Buttons
- Links navigate, buttons perform actions — use correctly
- Links opening new tab must warn users with visually hidden text or icon with `aria-label`

### Language
- `lang` attribute on `<html>` element
- `lang` attribute on any element that switches to a different language

## Lightbox

Screenshot thumbnails on the review page are clickable. Clicking opens a WCAG-compliant lightbox dialog showing the full-size image:
- `role="dialog"` and `aria-modal="true"` on the overlay
- `aria-label` set dynamically to describe the issue (e.g., "Screenshot of issue: Consecutive Emojis")
- Focus moves to the close button when opened
- Escape key closes the lightbox
- Close button is a `<button>` with `aria-label="Close screenshot"`
- Focus traps inside the lightbox while open (only the close button is focusable)
- Focus returns to the thumbnail button that triggered the lightbox on close
- Clicking the backdrop also closes the lightbox

## Screenshot Behavior

- **Image/flyer/heading/emoji issues**: Screenshot captures the `<section>` element with padding
- **Link issues**: Screenshot captures the paragraph/sentence containing the link, cropped to the content column
- **Duplicate alt text**: Primary screenshot + extra screenshots for each additional image in the group

## Efficiency Features

### Result Caching
- Vision API results cached in `smore_checker/vision_cache.json` (gitignored)
- Cache key: SHA-256 hash of function name + image URL + context
- Clear cache: `python check.py --clear-cache`

### Image Pre-filtering
- Images under 100x100 pixels skipped as decorative (no API call)
- Dimensions read from raw image bytes (PNG/JPEG/GIF/WebP) without PIL

### Batch Classification
- Up to 5 images classified per API call
- Each result individually cached

### Rate Limiting
- Configurable per provider (0s for Claude, 2s for Gemini free tier)
- Non-vision checks run first per section to use rate-limit dead time

### CLI Flags
- `--verbose` — Detailed vision API logging (calls, cache hits, timing)
- `--clear-cache` — Clear vision result cache before running
- `--no-review` — Skip review step, generate PDF directly

## Architecture Decisions

- **PDF generation**: Playwright renders HTML to PDF (WeasyPrint requires GTK on Windows)
- **Review server**: Flask on `localhost:5050`, single-use, shuts down after PDF generation
- **Form data flow**: Issues stored in Flask module state, form only sends include/exclude toggles and edited text values. On submit, Flask filters issues, updates edited fields, calls `report.generate_html_report()` + `report.save_pdf_report()`.
- **Provider pattern**: `vision_router.py` delegates to `vision_claude.py` or `vision_gemini.py` based on `VISION_PROVIDER` env var. Providers expose identical interfaces: `call_vision()`, `call_vision_batch()`, `call_text()`.
