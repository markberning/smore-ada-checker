"""Local Flask server for reviewing accessibility issues before PDF generation.

Spins up on localhost:5050, opens the review page in the default browser,
lets the user include/exclude issues and edit suggestions, then generates
the final PDF report and shuts down.
"""

import asyncio
import os
import signal
import threading
import webbrowser
from html import escape

from flask import Flask, request, send_file

from .models import Issue
from .report import generate_html_report, save_pdf_report
from .vision_router import provider_name as vision_provider_name

app = Flask(__name__)

# ---- Module-level state (single-use local server) ----

_state = {
    "issues": [],
    "page_url": "",
    "page_title": "",
    "output_path": "",
}

_shutdown_event = threading.Event()

# ---- Constants ----

# Badge colors (darkened from PDF report for 4.5:1 white-text contrast)
BADGE_COLORS = {
    "image": "#b45309",
    "flyer": "#b91c1c",
    "link": "#1d4ed8",
    "heading": "#6d28d9",
    "video": "#b91c1c",
    "emoji": "#c2410c",
}

# Which categories have editable fields
EDITABLE_ALT_CATEGORIES = {
    "Missing Alt Text", "Filename as Alt Text", "Alt Text Too Long",
    "Ineffective Alt Text", "Duplicate Alt Text",
}
EDITABLE_LINK_CATEGORIES = {
    "Generic Link Text", "Vague Link Text", "Raw URL as Link Text",
}
EDITABLE_FLYER_CATEGORIES = {
    "Flyer Info Not in Text",
}


def _get_edit_type(issue: Issue) -> str:
    """Return the edit type for an issue, or empty string if not editable."""
    if issue.category in EDITABLE_ALT_CATEGORIES:
        return "alt_text"
    if issue.category in EDITABLE_LINK_CATEGORIES:
        return "link_text"
    if issue.category in EDITABLE_FLYER_CATEGORIES:
        return "flyer_text"
    return ""


# ---- CSS (separate constant to avoid f-string brace escaping) ----

REVIEW_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

/* Skip link */
.skip-link {
    position: absolute;
    top: -60px;
    left: 0;
    background: #1e3a5f;
    color: #ffffff;
    padding: 10px 20px;
    z-index: 200;
    text-decoration: none;
    font-weight: 600;
    font-size: 14px;
    border-bottom-right-radius: 6px;
}
.skip-link:focus {
    top: 0;
    outline: 3px solid #2563eb;
    outline-offset: 2px;
}

/* Focus indicators on ALL interactive elements */
*:focus-visible {
    outline: 3px solid #2563eb;
    outline-offset: 2px;
}

body {
    font-family: "DM Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    line-height: 1.5;
    color: #334155;
    background: #edf2fb;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
}

.container {
    max-width: 900px;
    margin: 0 auto;
    padding: 0 20px;
    width: 100%;
}

/* Header */
header {
    background: #1e3a5f;
    color: #ffffff;
    padding: 24px 0;
}
header h1 {
    font-size: 24px;
    font-weight: 700;
    margin-bottom: 8px;
    letter-spacing: -0.02em;
}
.header-meta {
    font-size: 13px;
    color: #cbd5e1;
    line-height: 1.6;
}
.header-meta a {
    color: #5eead4;
    text-decoration: underline;
    text-decoration-thickness: 1px;
    text-underline-offset: 2px;
}
.header-meta a:hover {
    color: #99f6e4;
}

/* Status bar (sticky) */
.status-bar {
    position: sticky;
    top: 0;
    z-index: 100;
    background: #0f172a;
    color: #ffffff;
    padding: 12px 0;
    border-bottom: 3px solid #0d9488;
}
.status-bar .container {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
}
.status-text {
    font-size: 14px;
    font-weight: 500;
}
.status-actions {
    display: flex;
    gap: 12px;
    align-items: center;
}

/* Buttons */
.btn-generate {
    background: #0f766e;
    color: #ffffff;
    border: none;
    padding: 10px 24px;
    border-radius: 6px;
    font-family: inherit;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s;
}
.btn-generate:hover {
    background: #115e59;
}
.btn-generate:disabled {
    background: #6b7280;
    cursor: not-allowed;
}
.btn-cancel {
    color: #94a3b8;
    background: none;
    border: 1px solid #475569;
    padding: 8px 16px;
    border-radius: 6px;
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    text-decoration: none;
    display: inline-block;
}
.btn-cancel:hover {
    color: #e2e8f0;
    border-color: #94a3b8;
}

/* Main content */
main {
    flex: 1;
    padding: 24px 0;
}

/* Section headings */
.review-section {
    margin-bottom: 28px;
}
.review-section h2 {
    color: #1e3a5f;
    font-size: 16px;
    font-weight: 700;
    padding: 6px 0 6px 14px;
    border-left: 3px solid #0d9488;
    margin-bottom: 14px;
    letter-spacing: -0.01em;
}

/* Issue cards */
.issue-card {
    background: #ffffff;
    border-radius: 8px;
    margin-bottom: 12px;
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
    border: 2px solid transparent;
    transition: background 0.2s, border-color 0.2s;
}
.issue-card.excluded {
    background: #f1f5f9;
    border-color: #cbd5e1;
}
.issue-card.excluded .issue-desc,
.issue-card.excluded .fix-box,
.issue-card.excluded .detail-label,
.issue-card.excluded .detail-box code {
    color: #475569;
}
.issue-card.excluded .issue-badge {
    background: #6b7280 !important;
}
.issue-card.excluded .excluded-indicator {
    display: inline-block;
}

/* Issue header */
.issue-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 16px;
    border-bottom: 1px solid #eef0f2;
    gap: 12px;
    flex-wrap: wrap;
}
.issue-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 5px;
    color: #ffffff;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.01em;
}
.excluded-indicator {
    display: none;
    font-size: 12px;
    font-weight: 600;
    color: #6b7280;
    margin-left: 8px;
    padding: 3px 8px;
    border: 1px solid #9ca3af;
    border-radius: 4px;
}

/* Include toggle */
.include-toggle {
    display: flex;
    align-items: center;
    gap: 8px;
}
.include-toggle input[type="checkbox"] {
    width: 18px;
    height: 18px;
    accent-color: #0d9488;
    cursor: pointer;
    flex-shrink: 0;
}
.include-toggle label {
    font-size: 13px;
    font-weight: 500;
    color: #475569;
    cursor: pointer;
    user-select: none;
}

/* Issue body */
.issue-body {
    padding: 14px 16px;
}
.issue-columns {
    display: flex;
    gap: 16px;
    align-items: flex-start;
}
.issue-screenshot {
    flex: 0 0 auto;
    max-width: 220px;
    min-width: 120px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.issue-screenshot.link-screenshot {
    flex: 0 0 50%;
    max-width: 50%;
    min-width: 180px;
}
.screenshot-img {
    width: 100%;
    border: 1px solid #e2e8f0;
    border-radius: 5px;
    display: block;
}
.issue-info {
    flex: 1 1 auto;
    min-width: 0;
}
.issue-info-full {
    width: 100%;
}

/* Description */
.issue-desc {
    margin: 0 0 10px 0;
    color: #1e293b;
    font-size: 13.5px;
    line-height: 1.6;
    padding: 8px 0 8px 12px;
    border-left: 3px solid var(--issue-color, #6b7280);
}
code.inline-value {
    font-family: "Consolas", "Monaco", "Courier New", monospace;
    font-size: 12px;
    background: #f1f5f9;
    padding: 1px 5px;
    border-radius: 3px;
    border: 1px solid #e2e8f0;
}

/* Detail boxes (current alt text, missing details) */
.detail-box {
    border-radius: 5px;
    padding: 8px 12px;
    margin-bottom: 8px;
    font-size: 12.5px;
    word-break: break-word;
}
.detail-box code {
    font-family: "Consolas", "Monaco", "Courier New", monospace;
    font-size: 12px;
}
.detail-label {
    display: block;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 4px;
    color: #475569;
}
.detail-box.current-value {
    background: #f1f5f9;
    border: 1px solid #e2e8f0;
}

/* Form fields */
.form-group {
    margin-bottom: 10px;
}
.form-group label {
    display: block;
    font-size: 12px;
    font-weight: 600;
    color: #334155;
    margin-bottom: 4px;
}
.form-group textarea,
.form-group input[type="text"] {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    font-family: "Consolas", "Monaco", "Courier New", monospace;
    font-size: 12.5px;
    line-height: 1.5;
    color: #1e293b;
    background: #ffffff;
    transition: border-color 0.15s;
    resize: vertical;
}
.form-group textarea:focus,
.form-group input[type="text"]:focus {
    border-color: #0d9488;
}
.form-group textarea:disabled,
.form-group input[type="text"]:disabled {
    background: #f1f5f9;
    color: #64748b;
    cursor: not-allowed;
}

/* Fix box */
.fix-box {
    border-left: 3px solid #059669;
    padding: 8px 12px;
    margin-top: 10px;
    font-size: 12.5px;
    color: #475569;
}
.fix-box strong {
    color: #334155;
}
.fix-list {
    margin: 6px 0 0 20px;
    padding: 0;
    list-style-type: disc;
}
.fix-list li {
    margin-bottom: 2px;
}

/* Footer */
footer {
    background: #f1f5f9;
    border-top: 1px solid #dcdfe3;
    padding: 16px 0;
    color: #64748b;
    font-size: 12px;
    text-align: center;
}
footer a {
    color: #0d9488;
    text-decoration: underline;
}

/* Screenshot buttons (lightbox triggers) */
.screenshot-btn {
    display: block;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    border-radius: 5px;
    transition: opacity 0.15s;
    width: 100%;
}
.screenshot-btn:hover {
    opacity: 0.85;
}

/* Lightbox */
.lightbox-overlay {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: rgba(0, 0, 0, 0.8);
    align-items: center;
    justify-content: center;
    padding: 24px;
}
.lightbox-overlay.open {
    display: flex;
}
.lightbox-close {
    position: absolute;
    top: 16px;
    right: 16px;
    background: rgba(255, 255, 255, 0.15);
    border: 2px solid rgba(255, 255, 255, 0.4);
    color: #ffffff;
    width: 44px;
    height: 44px;
    border-radius: 50%;
    font-size: 22px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.15s;
    line-height: 1;
}
.lightbox-close:hover {
    background: rgba(255, 255, 255, 0.3);
}
.lightbox-close:focus-visible {
    outline: 3px solid #2563eb;
    outline-offset: 2px;
}
.lightbox-img {
    max-width: 90vw;
    max-height: 85vh;
    border-radius: 6px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}

/* No issues */
.no-issues {
    text-align: center;
    padding: 48px 24px;
    background: #ffffff;
    border-radius: 8px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.no-issues h2 {
    color: #059669;
    font-size: 20px;
    margin-bottom: 8px;
    border: none;
    padding: 0;
}
.no-issues p {
    color: #64748b;
    font-size: 14px;
}

/* Success page */
.success-card {
    background: #ffffff;
    border-radius: 8px;
    padding: 36px 32px;
    max-width: 600px;
    margin: 48px auto;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
    text-align: center;
}
.success-card h1 {
    color: #059669;
    font-size: 22px;
    margin-bottom: 16px;
}
.success-card .file-path {
    background: #f1f5f9;
    border: 1px solid #e2e8f0;
    border-radius: 5px;
    padding: 10px 14px;
    font-family: "Consolas", "Monaco", "Courier New", monospace;
    font-size: 13px;
    color: #1e293b;
    word-break: break-all;
    margin: 16px 0;
    text-align: left;
}
.success-card .count-info {
    color: #475569;
    font-size: 14px;
    margin-bottom: 20px;
}
.success-card .close-info {
    color: #94a3b8;
    font-size: 12px;
}
"""

# ---- Format helper (reused from report.py pattern) ----

import re

def _format_description(text: str) -> str:
    """Escape HTML and wrap quoted values in <code> tags."""
    escaped = escape(text).replace("\n", "<br>")
    escaped = re.sub(r'&quot;(.+?)&quot;', r'<code class="inline-value">\1</code>', escaped)
    return escaped


# ---- HTML Generators ----

def _generate_review_html(issues: list[Issue], page_url: str, page_title: str) -> str:
    """Generate the WCAG 2.1 AA compliant review page HTML."""
    total = len(issues)

    # Group issues by section
    sections: dict[str, list[tuple[int, Issue]]] = {}
    for i, issue in enumerate(issues):
        sections.setdefault(issue.section_name, []).append((i, issue))

    # Build cards
    sections_html = ""
    for sec_idx, (section_name, section_issues) in enumerate(sections.items()):
        cards_html = ""
        for idx, issue in section_issues:
            color = BADGE_COLORS.get(issue.issue_type, "#6b7280")
            edit_type = _get_edit_type(issue)

            # Screenshots
            screenshots_html = ""
            lightbox_label = f"Screenshot of issue: {issue.category}"
            if issue.screenshot_path and os.path.exists(issue.screenshot_path):
                screenshots_html += (
                    f'<button type="button" class="screenshot-btn" data-src="/screenshot/{idx}" data-label="{escape(lightbox_label)}">'
                    f'<img src="/screenshot/{idx}" alt="Screenshot showing the accessibility issue. Click to enlarge." class="screenshot-img">'
                    f'</button>'
                )
            for j, extra_path in enumerate(issue.extra_screenshots):
                if extra_path and os.path.exists(extra_path):
                    screenshots_html += (
                        f'<button type="button" class="screenshot-btn" data-src="/screenshot/{idx}/extra/{j}" data-label="{escape(lightbox_label)}">'
                        f'<img src="/screenshot/{idx}/extra/{j}" alt="Additional screenshot for this issue. Click to enlarge." class="screenshot-img">'
                        f'</button>'
                    )

            # Description
            desc_html = _format_description(issue.description)

            # Current value display
            current_html = ""
            if issue.current_alt:
                current_html = (
                    f'<div class="detail-box current-value">'
                    f'<span class="detail-label">Current value:</span>'
                    f'<code>{escape(issue.current_alt)}</code>'
                    f'</div>'
                )

            # Missing details display (flyer issues)
            missing_html = ""
            if issue.missing_details and edit_type != "flyer_text":
                for detail in issue.missing_details:
                    missing_html += (
                        f'<div class="detail-box current-value">'
                        f'<span class="detail-label">Missing from text:</span>'
                        f'<code>{escape(detail)}</code>'
                        f'</div>'
                    )

            # Editable fields
            edit_html = ""
            if edit_type == "alt_text":
                if issue.category == "Duplicate Alt Text":
                    # Duplicate alt text cards only show per-image fields, no top-level textarea
                    for j, extra_alt in enumerate(issue.extra_suggested_alts):
                        edit_html += (
                            f'<div class="form-group">'
                            f'<label for="edit-{idx}-extra-{j}">Image {j + 1} suggested alt text:</label>'
                            f'<textarea id="edit-{idx}-extra-{j}" name="edit_{idx}_extra_{j}" rows="2">{escape(extra_alt)}</textarea>'
                            f'</div>'
                        )
                else:
                    value = issue.suggested_alt or ""
                    edit_html += (
                        f'<div class="form-group">'
                        f'<label for="edit-{idx}">Suggested alt text:</label>'
                        f'<textarea id="edit-{idx}" name="edit_{idx}" rows="2">{escape(value)}</textarea>'
                        f'</div>'
                    )
            elif edit_type == "link_text":
                value = issue.suggested_alt or ""
                edit_html = (
                    f'<div class="form-group">'
                    f'<label for="edit-{idx}">Suggested link text:</label>'
                    f'<input type="text" id="edit-{idx}" name="edit_{idx}" value="{escape(value)}">'
                    f'</div>'
                )
            elif edit_type == "flyer_text":
                value = "\n".join(issue.missing_details)
                edit_html = (
                    f'<div class="form-group">'
                    f'<label for="edit-{idx}">Information to add to section text:</label>'
                    f'<textarea id="edit-{idx}" name="edit_{idx}" rows="4">{escape(value)}</textarea>'
                    f'</div>'
                )

            # Fix suggestion (render multi-line suggestions as a bulleted list)
            suggestion_lines = [line.strip() for line in issue.suggestion.split("\n") if line.strip()]
            if len(suggestion_lines) > 1:
                items = "".join(f"<li>{escape(line)}</li>" for line in suggestion_lines)
                fix_content = f'<strong>How to fix:</strong><ul class="fix-list">{items}</ul>'
            else:
                fix_content = f'<strong>How to fix:</strong> {escape(issue.suggestion)}'
            fix_html = f'<div class="fix-box">{fix_content}</div>'

            # Compose body (with or without screenshot column)
            info_html = f"""
                <p class="issue-desc" style="--issue-color:{color}">{desc_html}</p>
                {current_html}
                {missing_html}
                {edit_html}
                {fix_html}
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

            cards_html += f"""
            <div class="issue-card" id="card-{idx}" data-index="{idx}">
                <div class="issue-header">
                    <div>
                        <span class="issue-badge" style="background:{color}">{escape(issue.category)}</span>
                        <span class="excluded-indicator" aria-hidden="true">Excluded</span>
                    </div>
                    <div class="include-toggle">
                        <input type="checkbox" id="include-{idx}" name="include_{idx}" value="1" checked>
                        <label for="include-{idx}">Include in report</label>
                    </div>
                </div>
                <div class="issue-body">{body_html}</div>
            </div>"""

        section_id = f"section-{sec_idx}"
        sections_html += f"""
        <section class="review-section" aria-labelledby="{section_id}">
            <h2 id="{section_id}">{escape(section_name)}</h2>
            {cards_html}
        </section>"""

    if total == 0:
        body_content = """
        <div class="no-issues">
            <h2>No accessibility issues found</h2>
            <p>This Smore post follows accessibility best practices.</p>
        </div>"""
        status_html = '<span id="status-text">No issues to review</span>'
        form_open = ""
        form_close = ""
        actions_html = f"""
            <button type="submit" form="review-form" class="btn-generate">Generate Report</button>
            <a href="/cancel" class="btn-cancel">Cancel</a>
        """
        # Still allow generating a "clean" report
        form_open = '<form id="review-form" action="/generate" method="POST">'
        form_close = '</form>'
    else:
        body_content = sections_html
        status_html = f'<span id="status-text">{total} issues selected, 0 excluded</span>'
        form_open = '<form id="review-form" action="/generate" method="POST">'
        form_close = '</form>'
        actions_html = f"""
            <button type="submit" form="review-form" class="btn-generate">Generate Final Report</button>
            <a href="/cancel" class="btn-cancel">Cancel</a>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Review Accessibility Issues - {escape(page_title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
{REVIEW_CSS}
</style>
</head>
<body>

<a href="#main" class="skip-link">Skip to main content</a>

<header>
    <div class="container">
        <h1>Review Accessibility Issues</h1>
        <p class="header-meta">
            <strong>Page:</strong> {escape(page_title)}<br>
            <strong>URL:</strong> <a href="{escape(page_url)}">{escape(page_url)}</a><br>
            <strong>Vision provider:</strong> {escape(vision_provider_name)}
        </p>
    </div>
</header>

<div class="status-bar" role="status">
    <div class="container">
        <div aria-live="polite">{status_html}</div>
        <div class="status-actions">
            {actions_html}
        </div>
    </div>
</div>

<main id="main">
    <div class="container">
        {form_open}
        {body_content}
        {form_close}
    </div>
</main>

<footer>
    <div class="container">
        <p>EUSD Smore Accessibility Checker &middot;
        <a href="mailto:webdeveloper@eusd.org">webdeveloper@eusd.org</a></p>
    </div>
</footer>

<script>
(function() {{
    var checkboxes = document.querySelectorAll('.include-toggle input[type="checkbox"]');
    var statusText = document.getElementById('status-text');
    var total = document.querySelectorAll('.issue-card').length;

    function updateStatus() {{
        var included = 0;
        checkboxes.forEach(function(cb) {{
            var index = cb.id.replace('include-', '');
            var card = document.getElementById('card-' + index);
            if (!card) return;

            if (cb.checked) {{
                included++;
                card.classList.remove('excluded');
                // Enable editable fields
                var fields = card.querySelectorAll('textarea, input:not([type="checkbox"])');
                fields.forEach(function(f) {{ f.disabled = false; }});
                // Update label
                cb.nextElementSibling.textContent = 'Include in report';
            }} else {{
                card.classList.add('excluded');
                // Disable editable fields
                var fields = card.querySelectorAll('textarea, input:not([type="checkbox"])');
                fields.forEach(function(f) {{ f.disabled = true; }});
                // Update label
                cb.nextElementSibling.textContent = 'Excluded from report';
            }}
        }});

        var excluded = total - included;
        if (statusText) {{
            statusText.textContent = included + ' issue' + (included !== 1 ? 's' : '') + ' selected, ' + excluded + ' excluded';
        }}
    }}

    checkboxes.forEach(function(cb) {{
        cb.addEventListener('change', updateStatus);
    }});

    // Handle form submission - show loading state
    var form = document.getElementById('review-form');
    if (form) {{
        form.addEventListener('submit', function() {{
            var btn = document.querySelector('.btn-generate');
            if (btn) {{
                btn.disabled = true;
                btn.textContent = 'Generating report...';
                btn.setAttribute('aria-busy', 'true');
            }}
            if (statusText) {{
                statusText.textContent = 'Generating PDF report... Please wait.';
            }}
        }});
    }}
}})();
</script>

<div id="lightbox" class="lightbox-overlay" role="dialog" aria-modal="true" aria-label="">
    <button id="lightbox-close" class="lightbox-close" aria-label="Close screenshot">&times;</button>
    <img id="lightbox-img" class="lightbox-img" src="" alt="">
</div>

<script>
(function() {{
    var overlay = document.getElementById('lightbox');
    var lbImg = document.getElementById('lightbox-img');
    var lbClose = document.getElementById('lightbox-close');
    var lastTrigger = null;

    function openLightbox(src, label, trigger) {{
        lastTrigger = trigger;
        lbImg.src = src;
        lbImg.alt = label;
        overlay.setAttribute('aria-label', label);
        overlay.classList.add('open');
        document.body.style.overflow = 'hidden';
        lbClose.focus();
    }}

    function closeLightbox() {{
        overlay.classList.remove('open');
        document.body.style.overflow = '';
        lbImg.src = '';
        if (lastTrigger) {{
            lastTrigger.focus();
            lastTrigger = null;
        }}
    }}

    if (overlay) {{
        document.querySelectorAll('.screenshot-btn').forEach(function(btn) {{
            btn.addEventListener('click', function() {{
                openLightbox(btn.getAttribute('data-src'), btn.getAttribute('data-label'), btn);
            }});
        }});

        lbClose.addEventListener('click', closeLightbox);

        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape' && overlay.classList.contains('open')) {{
                closeLightbox();
            }}
        }});

        overlay.addEventListener('click', function(e) {{
            if (e.target === overlay) {{
                closeLightbox();
            }}
        }});

        overlay.addEventListener('keydown', function(e) {{
            if (e.key !== 'Tab') return;
            e.preventDefault();
            lbClose.focus();
        }});
    }}
}})();
</script>

</body>
</html>"""

    return html


def _generate_success_html(output_path: str, count: int, page_title: str) -> str:
    """Generate the success page shown after PDF is generated."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Report Generated - {escape(page_title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
{REVIEW_CSS}
</style>
</head>
<body>

<a href="#main" class="skip-link">Skip to main content</a>

<header>
    <div class="container">
        <h1>Report Generated</h1>
    </div>
</header>

<main id="main">
    <div class="container">
        <div class="success-card">
            <h2>PDF report saved successfully</h2>
            <p class="count-info">{count} issue{"s" if count != 1 else ""} included in the report.</p>
            <div class="file-path" role="status">{escape(output_path)}</div>
            <p class="close-info">You can close this window. The server will shut down automatically.</p>
        </div>
    </div>
</main>

<footer>
    <div class="container">
        <p>EUSD Smore Accessibility Checker &middot;
        <a href="mailto:webdeveloper@eusd.org">webdeveloper@eusd.org</a></p>
    </div>
</footer>

</body>
</html>"""


# ---- Routes ----

@app.route("/")
def review():
    """Serve the review page."""
    issues = _state["issues"]
    page_url = _state["page_url"]
    page_title = _state["page_title"]
    html = _generate_review_html(issues, page_url, page_title)
    return html


@app.route("/screenshot/<int:index>")
def screenshot(index):
    """Serve a screenshot image for an issue."""
    issues = _state["issues"]
    if 0 <= index < len(issues):
        path = issues[index].screenshot_path
        if path and os.path.exists(path):
            return send_file(path, mimetype="image/png")
    return "", 404


@app.route("/screenshot/<int:index>/extra/<int:extra_index>")
def extra_screenshot(index, extra_index):
    """Serve an extra screenshot (for duplicate alt text issues)."""
    issues = _state["issues"]
    if 0 <= index < len(issues):
        extras = issues[index].extra_screenshots
        if 0 <= extra_index < len(extras):
            path = extras[extra_index]
            if path and os.path.exists(path):
                return send_file(path, mimetype="image/png")
    return "", 404


@app.route("/generate", methods=["POST"])
def generate():
    """Process the review form, generate the final PDF, and shut down."""
    issues = _state["issues"]
    page_url = _state["page_url"]
    page_title = _state["page_title"]
    output_path = _state["output_path"]

    included_issues = []

    for i, issue in enumerate(issues):
        if not request.form.get(f"include_{i}"):
            continue  # Excluded by user

        edit_type = _get_edit_type(issue)

        if edit_type == "alt_text":
            edited = request.form.get(f"edit_{i}", "")
            if edited:
                issue.suggested_alt = edited
            # Extra alts for duplicate groups
            for j in range(len(issue.extra_suggested_alts)):
                extra_edited = request.form.get(f"edit_{i}_extra_{j}", "")
                if extra_edited:
                    issue.extra_suggested_alts[j] = extra_edited
        elif edit_type == "link_text":
            edited = request.form.get(f"edit_{i}", "")
            if edited:
                issue.suggested_alt = edited
        elif edit_type == "flyer_text":
            edited = request.form.get(f"edit_{i}", "")
            if edited:
                issue.missing_details = [
                    line.strip() for line in edited.split("\n") if line.strip()
                ]

        included_issues.append(issue)

    # Generate the PDF with only included issues (renumbered automatically)
    html = generate_html_report(included_issues, page_url, page_title)
    asyncio.run(save_pdf_report(html, output_path))

    count = len(included_issues)
    print(f"\nReport saved to: {output_path}")
    print(f"{count} issue{'s' if count != 1 else ''} included.")

    # Schedule server shutdown after response is sent
    threading.Timer(2.0, lambda: _shutdown_event.set()).start()

    return _generate_success_html(output_path, count, page_title)


@app.route("/cancel")
def cancel():
    """Cancel review and shut down the server."""
    _shutdown_event.set()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cancelled</title>
<style>{REVIEW_CSS}</style>
</head>
<body>
<main id="main">
    <div class="container">
        <div class="success-card">
            <h1>Review Cancelled</h1>
            <p class="close-info">No report was generated. You can close this window.</p>
        </div>
    </div>
</main>
</body>
</html>"""


# ---- Public API ----

def start_review_server(
    issues: list[Issue],
    page_url: str,
    page_title: str,
    output_path: str,
):
    """Start the review server, open the browser, and block until shutdown.

    This function blocks until the user generates the report or cancels.
    """
    _state["issues"] = issues
    _state["page_url"] = page_url
    _state["page_title"] = page_title
    _state["output_path"] = output_path

    _shutdown_event.clear()

    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 5050, app)
    server.timeout = 1  # Check shutdown event every second

    print(f"\nReview server running at http://localhost:5050")
    print("Opening browser...")
    print("(Press Ctrl+C to cancel)\n")

    # Open browser after a short delay to let the server start
    threading.Timer(0.8, lambda: webbrowser.open("http://localhost:5050")).start()

    # Serve until shutdown is requested
    try:
        while not _shutdown_event.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        print("\nCancelled by user.")
    finally:
        server.server_close()
        print("Review server stopped.")
