import asyncio
import base64
import os
import tempfile
from collections import Counter
from datetime import date
from html import escape

from playwright.async_api import async_playwright

import re

from .models import Issue


def _format_description(text: str) -> str:
    """Escape HTML and wrap quoted values in <code> tags for inline styling."""
    escaped = escape(text).replace("\n", "<br>")
    # Replace "quoted text" with <code>quoted text</code>
    escaped = re.sub(r'&quot;(.+?)&quot;', r'<code class="inline-value">\1</code>', escaped)
    return escaped


def _image_to_data_uri(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return f"data:image/png;base64,{data}"


BADGE_COLORS = {
    "image": "#d97706",
    "flyer": "#dc2626",
    "link": "#2563eb",
    "heading": "#7c3aed",
    "video": "#dc2626",
    "emoji": "#ea580c",
}

BADGE_ICONS = {
    "image": "&#9881;",       # gear/image
    "flyer": "&#9881;",       # page
    "link": "&#128279;",      # link
    "heading": "&#9776;",     # heading
    "video": "&#9654;",       # play
    "emoji": "&#9888;",       # warning
}

PRIMARY = "#1e3a5f"      # dark navy
ACCENT = "#0d9488"       # teal
SUCCESS = "#059669"      # green
WARNING = "#d97706"      # amber
BG = "#edf2fb"           # light blue
CARD_BG = "#ffffff"


def generate_html_report(issues: list[Issue], page_url: str, page_title: str) -> str:
    today = date.today().strftime("%B %d, %Y")
    type_counts = Counter(i.issue_type for i in issues)
    total = len(issues)

    sections: dict[str, list[Issue]] = {}
    for issue in issues:
        sections.setdefault(issue.section_name, []).append(issue)

    # Summary stat boxes
    stat_boxes = ""
    for itype, count in sorted(type_counts.items()):
        color = BADGE_COLORS.get(itype, "#6b7280")
        label = itype.replace("_", " ").title()
        stat_boxes += f"""
        <div class="stat-box">
            <div class="stat-count" style="color:{color}">{count}</div>
            <div class="stat-label">{label}</div>
        </div>"""

    # Issue cards
    sections_html = ""
    issue_num = 0
    for section_name, section_issues in sections.items():
        cards = ""
        for issue in section_issues:
            issue_num += 1
            color = BADGE_COLORS.get(issue.issue_type, "#6b7280")

            # Build screenshot column
            screenshots_html = ""
            if issue.screenshot_path:
                data_uri = _image_to_data_uri(issue.screenshot_path)
                if data_uri:
                    screenshots_html += f'<img src="{data_uri}" alt="Screenshot of the issue" class="screenshot-img">'

            # Extra screenshots for duplicate alt text
            for extra_path in issue.extra_screenshots:
                data_uri = _image_to_data_uri(extra_path)
                if data_uri:
                    screenshots_html += f'<img src="{data_uri}" alt="Additional image with same alt text" class="screenshot-img">'

            # Build detail boxes
            detail_boxes = ""

            if issue.current_alt:
                detail_boxes += f'<div class="code-box current"><span class="code-label">Current Alt Text:</span><code>{escape(issue.current_alt)}</code></div>'

            # Missing details (flyer issues)
            for detail in issue.missing_details:
                detail_boxes += f'<div class="code-box current"><span class="code-label">Missing From Text:</span><code>{escape(detail)}</code></div>'

            # Suggested alt text / link text
            if issue.suggested_alt:
                label = "Suggested Link Text:" if issue.issue_type == "link" else "Suggested Alt Text:"
                for line in issue.suggested_alt.split("\n"):
                    if line.strip():
                        detail_boxes += f'<div class="code-box suggested"><span class="code-label">{label}</span><code>{escape(line.strip())}</code></div>'

            # Per-image suggested alts (duplicate groups)
            if issue.extra_suggested_alts:
                for idx, s in enumerate(issue.extra_suggested_alts):
                    if s:
                        detail_boxes += f'<div class="code-box suggested"><span class="code-label">Image {idx + 1} Suggested Alt Text:</span><code>{escape(s)}</code></div>'

            description_html = _format_description(issue.description)

            info_html = f"""
                <p class="issue-desc">{description_html}</p>
                {detail_boxes}
                <div class="fix-box">
                    <strong>How to fix:</strong> {escape(issue.suggestion)}
                </div>
            """

            if screenshots_html:
                screenshot_class = "issue-screenshot link-screenshot" if issue.issue_type == "link" else "issue-screenshot"
                body_html = f"""
                <div class="issue-columns">
                    <div class="{screenshot_class}">{screenshots_html}</div>
                    <div class="issue-info">{info_html}</div>
                </div>"""
            else:
                body_html = f'<div class="issue-info-full">{info_html}</div>'

            cards += f"""
            <div class="issue-card" style="--issue-color:{color}">
                <div class="issue-header">
                    <span class="issue-badge" style="background:{color}">{issue.category}</span>
                    <span class="issue-num">#{issue_num}</span>
                </div>
                <div class="issue-body">{body_html}</div>
            </div>"""

        sections_html += f"""
        <div class="section">
            <div class="section-header">
                <h2 class="section-title">{escape(section_name)}</h2>
            </div>
            {cards}
        </div>"""

    if total == 0:
        body_content = """
        <div class="no-issues">
            <h2>No accessibility issues found</h2>
            <p>Great job! This Smore post follows accessibility best practices.</p>
        </div>"""
    else:
        body_content = sections_html

    total_color = "#dc2626" if total > 0 else SUCCESS

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Accessibility Report - {escape(page_title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,300..800;1,9..40,300..800&display=swap" rel="stylesheet">
<style>
    @page {{
        size: letter;
        margin: 0.4in 0.5in;
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
        font-family: "DM Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        line-height: 1.5;
        color: #334155;
        background: {BG};
        max-width: 820px;
        margin: 0 auto;
        padding: 24px 16px 16px;
        font-size: 12.5px;
    }}

    /* Header */
    .report-header {{
        margin-bottom: 16px;
    }}
    .report-header h1 {{
        color: {PRIMARY};
        font-size: 22px;
        font-weight: 700;
        margin-bottom: 4px;
        letter-spacing: -0.02em;
    }}
    .report-meta {{
        color: #64748b;
        font-size: 11.5px;
        line-height: 1.6;
    }}
    .report-meta a {{
        color: {ACCENT};
        text-decoration: none;
    }}

    /* Summary dashboard */
    .summary {{
        background: {CARD_BG};
        border-radius: 8px;
        padding: 14px 18px;
        margin-bottom: 18px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
    }}
    .summary-top {{
        display: flex;
        align-items: baseline;
        gap: 10px;
        margin-bottom: 10px;
    }}
    .summary-total {{
        font-size: 28px;
        font-weight: 700;
        color: {total_color};
        line-height: 1;
    }}
    .summary-label {{
        font-size: 13px;
        color: #64748b;
        font-weight: 500;
    }}
    .stat-row {{
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
    }}
    .stat-box {{
        background: #f0f4fa;
        border-radius: 6px;
        padding: 6px 12px;
        text-align: center;
        min-width: 70px;
    }}
    .stat-count {{
        font-size: 18px;
        font-weight: 700;
        line-height: 1.2;
    }}
    .stat-label {{
        font-size: 10px;
        color: #64748b;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }}

    /* Sections */
    .section {{
        margin-bottom: 16px;
    }}
    .section-header {{
        break-after: avoid;
        page-break-after: avoid;
    }}
    .section-title {{
        color: {PRIMARY};
        font-size: 14px;
        font-weight: 700;
        padding: 5px 0 5px 12px;
        border-left: 3px solid {ACCENT};
        margin-bottom: 10px;
        letter-spacing: -0.01em;
    }}

    /* Issue cards */
    .issue-card {{
        background: {CARD_BG};
        border-radius: 8px;
        margin-bottom: 10px;
        overflow: hidden;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
        break-inside: avoid;
        page-break-inside: avoid;
    }}
    .issue-header {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 7px 12px;
        border-bottom: 1px solid #eef0f2;
    }}
    .issue-badge {{
        display: inline-block;
        padding: 2px 8px;
        border-radius: 5px;
        color: white;
        font-size: 10.5px;
        font-weight: 600;
        letter-spacing: 0.01em;
    }}
    .issue-num {{
        color: #94a3b8;
        font-size: 10.5px;
        font-weight: 500;
    }}
    .issue-body {{
        padding: 10px 12px;
    }}
    .issue-columns {{
        display: flex;
        gap: 12px;
        align-items: flex-start;
    }}
    .issue-screenshot {{
        flex: 0 0 auto;
        max-width: 220px;
        min-width: 120px;
        display: flex;
        flex-direction: column;
        gap: 6px;
    }}
    .issue-screenshot.link-screenshot {{
        flex: 0 0 55%;
        max-width: 55%;
        min-width: 200px;
    }}
    .screenshot-img {{
        width: 100%;
        border: 1px solid #e5e7ea;
        border-radius: 5px;
        display: block;
    }}
    .issue-info {{
        flex: 1 1 auto;
        min-width: 0;
    }}
    .issue-info-full {{
        width: 100%;
    }}
    .issue-desc {{
        margin: 0 0 8px 0;
        color: #1e293b;
        font-size: 12.5px;
        line-height: 1.55;
        padding: 6px 0 6px 10px;
        border-left: 3px solid var(--issue-color, #6b7280);
    }}

    /* Inline code for quoted values in descriptions */
    code.inline-value {{
        font-family: "Consolas", "Monaco", "Courier New", monospace;
        font-size: 11.5px;
        background: #f1f5f9;
        padding: 1px 4px;
        border-radius: 3px;
        border: 1px solid #e2e8f0;
    }}

    /* Code boxes for alt text and missing details */
    .code-box {{
        border-radius: 5px;
        padding: 6px 10px;
        margin-top: 8px;
        font-size: 11.5px;
        word-break: break-word;
    }}
    .code-box code {{
        font-family: "Consolas", "Monaco", "Courier New", monospace;
        font-size: 11.5px;
    }}
    .code-label {{
        display: block;
        font-size: 9.5px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 4px;
        color: #475569;
    }}
    .code-box.current {{
        background: #f1f5f9;
        border: 1px solid #e2e8f0;
    }}
    .code-box.suggested {{
        background: #ecfdf5;
        border: 1px solid #a7f3d0;
    }}
    .code-box.suggested .code-label {{
        color: {SUCCESS};
    }}

    /* Fix suggestion */
    .fix-box {{
        border-left: 3px solid {SUCCESS};
        padding: 6px 10px;
        margin-top: 8px;
        font-size: 11.5px;
        color: #475569;
        background: transparent;
    }}
    .fix-box strong {{
        color: #334155;
    }}

    /* No issues */
    .no-issues {{
        text-align: center;
        padding: 36px 20px;
        background: {CARD_BG};
        border-radius: 8px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
    }}
    .no-issues h2 {{
        color: {SUCCESS};
        font-size: 18px;
        margin-bottom: 6px;
    }}
    .no-issues p {{
        color: #64748b;
    }}

    /* Footer */
    .footer {{
        margin-top: 20px;
        padding-top: 10px;
        border-top: 1px solid #dcdfe3;
        color: #94a3b8;
        font-size: 10px;
        text-align: center;
    }}
    .footer a {{
        color: {ACCENT};
        text-decoration: none;
    }}
</style>
</head>
<body>

<div class="report-header">
    <h1>Accessibility Report</h1>
    <div class="report-meta">
        <strong>Page:</strong> {escape(page_title)}<br>
        <strong>URL:</strong> <a href="{escape(page_url)}">{escape(page_url)}</a><br>
        <strong>Date:</strong> {today}
    </div>
</div>

<div class="summary">
    <div class="summary-top">
        <span class="summary-total">{total}</span>
        <span class="summary-label">issue{"s" if total != 1 else ""} found</span>
    </div>
    <div class="stat-row">{stat_boxes}</div>
</div>

{body_content}

<div class="footer">
    Generated by EUSD Smore Accessibility Checker<br>
    For questions about this report, contact <a href="mailto:webdeveloper@eusd.org">webdeveloper@eusd.org</a>
</div>

</body>
</html>"""

    return html


async def save_pdf_report(html_content: str, output_path: str):
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html_content)
        tmp_html = f.name
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto(f"file:///{tmp_html.replace(os.sep, '/')}", wait_until="networkidle")
            await page.pdf(
                path=output_path,
                format="Letter",
                margin={"top": "0.4in", "bottom": "0.4in", "left": "0.5in", "right": "0.5in"},
                print_background=True,
            )
            await browser.close()
    finally:
        os.unlink(tmp_html)


async def generate_report(issues: list[Issue], page_url: str, page_title: str, output_path: str):
    html = generate_html_report(issues, page_url, page_title)
    await save_pdf_report(html, output_path)
    return output_path
