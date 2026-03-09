import os
import re
import tempfile
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Page, Browser

from .models import (
    ImageInfo, LinkInfo, HeadingInfo, EmbedInfo,
    SmoreBlock, SmoreSection, PageData, Issue,
)

SMORE_BLOCK_SELECTOR = "section.block-wrapper .block-content"


async def launch_browser() -> tuple:
    """Launch Playwright and return (playwright, browser) tuple."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch()
    return pw, browser


async def close_browser(pw, browser: Browser):
    await browser.close()
    await pw.stop()


async def load_page(browser: Browser, url: str) -> Page:
    page = await browser.new_page(viewport={"width": 1280, "height": 900})
    await page.goto(url, wait_until="networkidle", timeout=30000)
    # Dismiss any cookie/overlay popups by clicking body
    await page.wait_for_timeout(1000)
    return page


def _clean_heading_text(raw: str) -> str:
    """Extract clean text from Smore's duplicated heading format."""
    # Smore headings duplicate text: SVG version + aria-label version
    # The aria-label on the h3 has the clean text
    # But from textContent we get something like "WORD1 \n  \n WORD2 \n...\nWORD1 WORD2"
    # Take the last line which is the clean version
    lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
    if lines:
        return lines[-1]
    return raw.strip()


async def scrape_page(page: Page, url: str) -> PageData:
    """Extract all structured data from a Smore page."""
    raw = await page.evaluate("""() => {
        const result = {sections: [], allHeadings: []};

        document.querySelectorAll('section.block-wrapper').forEach(sec => {
            const content = sec.querySelector('.block-content');
            if (!content) return;

            const blockType = content.dataset.blockType || '';
            const blockId = content.dataset.blockId || sec.id || '';

            const images = Array.from(sec.querySelectorAll('img[src]')).map(img => ({
                src: img.src,
                alt: img.alt || '',
                ariaHidden: img.getAttribute('aria-hidden'),
                selector: `section#${sec.id} img[src="${img.getAttribute('src')}"]`
            }));

            const links = Array.from(sec.querySelectorAll('a[href]')).map(a => ({
                href: a.href,
                text: a.textContent?.trim() || '',
                originalHref: a.getAttribute('o-href') || a.href,
                trackHref: a.getAttribute('track-href') || '',
                classes: a.className || '',
                selector: `section#${sec.id} a[href="${a.getAttribute('href')}"]`
            }));

            const headings = Array.from(sec.querySelectorAll('h1,h2,h3,h4,h5,h6')).map(h => ({
                tag: h.tagName,
                text: h.textContent?.trim() || '',
                ariaLabel: h.getAttribute('aria-label') || '',
                selector: `section#${sec.id} ${h.tagName.toLowerCase()}`
            }));

            const embeds = Array.from(sec.querySelectorAll('iframe, video, embed, object')).map(e => ({
                tag: e.tagName,
                src: e.src || e.getAttribute('data-src') || '',
                selector: `section#${sec.id} ${e.tagName.toLowerCase()}`
            }));

            // Get text content excluding image zoom links
            const textParts = [];
            sec.querySelectorAll('[data-field-key="content"], [data-field-key="title"]').forEach(el => {
                textParts.push(el.textContent?.trim() || '');
            });

            result.sections.push({
                sectionId: sec.id,
                blockId: blockId,
                blockType: blockType,
                textContent: textParts.join(' '),
                images: images,
                links: links,
                headings: headings,
                embeds: embeds
            });
        });

        // All headings for hierarchy check
        document.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(h => {
            const sec = h.closest('section.block-wrapper');
            result.allHeadings.push({
                tag: h.tagName,
                text: h.getAttribute('aria-label') || h.textContent?.trim() || '',
                sectionId: sec?.id || '',
                selector: sec ? `section#${sec.id} ${h.tagName.toLowerCase()}` : h.tagName.toLowerCase()
            });
        });

        return result;
    }""")

    title = await page.title()
    slug = urlparse(url).path.strip("/").split("/")[-1]

    # Build blocks and group into logical sections
    all_blocks = []
    for sec_data in raw["sections"]:
        block = SmoreBlock(
            block_id=sec_data["blockId"],
            block_type=sec_data["blockType"],
            section_id=sec_data["sectionId"],
            text_content=sec_data["textContent"],
        )

        for img_data in sec_data["images"]:
            if img_data.get("ariaHidden") == "true":
                continue
            block.images.append(ImageInfo(
                src=img_data["src"],
                alt=img_data["alt"],
                block_id=sec_data["blockId"],
                section_name="",  # filled later
                element_selector=f"section#{sec_data['sectionId']} img",
            ))

        for link_data in sec_data["links"]:
            # Skip Smore UI links (zoom buttons, etc.)
            if "skip-tracking" not in link_data.get("classes", "") and "fancy-pic" in link_data.get("classes", ""):
                continue
            if link_data["text"].startswith("zoom_out_map"):
                continue
            block.links.append(LinkInfo(
                href=link_data["href"],
                text=link_data["text"],
                original_href=link_data["originalHref"],
                block_id=sec_data["blockId"],
                section_name="",
                element_selector=link_data.get("selector", f"section#{sec_data['sectionId']} a[href]"),
            ))

        for h_data in sec_data["headings"]:
            clean_text = h_data.get("ariaLabel") or _clean_heading_text(h_data["text"])
            level = int(h_data["tag"][1])
            block.headings.append(HeadingInfo(
                tag=h_data["tag"],
                text=clean_text,
                block_id=sec_data["blockId"],
                section_name="",
                level=level,
            ))

        for e_data in sec_data["embeds"]:
            block.embeds.append(EmbedInfo(
                tag=e_data["tag"],
                src=e_data["src"],
                block_id=sec_data["blockId"],
                section_name="",
                element_selector=e_data["selector"],
            ))

        all_blocks.append(block)

    # Group blocks into logical sections using separators as delimiters
    sections = []
    current_section = SmoreSection(name="(Header)")
    for block in all_blocks:
        if block.block_type == "misc.separator":
            if current_section.blocks:
                sections.append(current_section)
            current_section = SmoreSection(name="(Untitled Section)")
            continue

        if block.block_type == "signature":
            continue

        if block.block_type == "text.title" and block.headings:
            current_section.name = block.headings[0].text

        current_section.blocks.append(block)

    if current_section.blocks:
        sections.append(current_section)

    # Set section names on all child items
    for section in sections:
        for block in section.blocks:
            for img in block.images:
                img.section_name = section.name
            for link in block.links:
                link.section_name = section.name
            for h in block.headings:
                h.section_name = section.name
            for e in block.embeds:
                e.section_name = section.name

    # Build all_headings list
    all_headings = []
    for h_data in raw["allHeadings"]:
        # Skip Table of Contents headings (Smore UI)
        if "Table of Contents" in h_data.get("text", ""):
            continue
        clean_text = h_data["text"]
        # Clean duplicated heading text
        lines = [l.strip() for l in clean_text.split("\n") if l.strip()]
        if lines:
            clean_text = lines[-1]
        level = int(h_data["tag"][1])
        section_name = ""
        for section in sections:
            for block in section.blocks:
                if block.section_id == h_data.get("sectionId"):
                    section_name = section.name
                    break
        all_headings.append(HeadingInfo(
            tag=h_data["tag"],
            text=clean_text,
            block_id=h_data.get("sectionId", ""),
            section_name=section_name,
            level=level,
        ))

    return PageData(
        url=url,
        title=title,
        sections=sections,
        all_headings=all_headings,
    )


async def take_element_screenshot(page: Page, selector: str, output_dir: str, name: str) -> str:
    """Take a screenshot of a specific element with padding. Returns the file path."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{name}.png")

    try:
        element = page.locator(selector).first
        # Check if the element exists and is visible
        if await element.count() == 0:
            return ""
        await element.scroll_into_view_if_needed(timeout=3000)
        await page.wait_for_timeout(300)

        # Get element bounding box and add padding
        bbox = await element.bounding_box()
        if not bbox:
            return ""

        padding = 30
        clip = {
            "x": max(0, bbox["x"] - padding),
            "y": max(0, bbox["y"] - padding),
            "width": bbox["width"] + padding * 2,
            "height": bbox["height"] + padding * 2,
        }
        await page.screenshot(path=path, clip=clip)
        return path
    except Exception:
        # Fall back to section-level screenshot
        try:
            section_selector = selector.split(" ")[0] if " " in selector else selector
            element = page.locator(section_selector).first
            if await element.count() > 0:
                await element.scroll_into_view_if_needed(timeout=3000)
                await page.wait_for_timeout(300)
                await element.screenshot(path=path)
                return path
        except Exception:
            pass
    return ""


async def take_link_screenshot(page: Page, selector: str, output_dir: str, name: str) -> str:
    """Take a screenshot of the sentence/paragraph containing a link.

    Finds the nearest paragraph or text container around the link, crops to
    the content column bounds (excluding Smore background decoration), and
    captures at the page's native zoom level.
    """
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{name}.png")

    try:
        link_el = page.locator(selector).first
        if await link_el.count() == 0:
            return ""

        await link_el.scroll_into_view_if_needed(timeout=3000)
        await page.wait_for_timeout(300)

        link_bbox = await link_el.bounding_box()
        if not link_bbox:
            return ""

        # Find the nearest text container (paragraph, list item, or small div)
        parent = None
        parent_bbox = None
        for xpath in [
            "xpath=ancestor::p[1]",
            "xpath=ancestor::li[1]",
            "xpath=ancestor::div[not(contains(@class,'block-content')) and not(contains(@class,'block-wrapper')) and not(contains(@class,'section'))][1]",
        ]:
            candidate = link_el.locator(xpath).first
            if await candidate.count() > 0:
                cbox = await candidate.bounding_box()
                if cbox and cbox["height"] < 250:
                    parent = candidate
                    parent_bbox = cbox
                    break

        # Find the content column bounds from .block-content
        content_el = link_el.locator("xpath=ancestor::div[contains(@class,'block-content')][1]").first
        if await content_el.count() > 0:
            content_bbox = await content_el.bounding_box()
        else:
            content_bbox = None

        # Horizontal bounds: use content column, not full viewport
        if content_bbox:
            clip_x = content_bbox["x"]
            clip_w = content_bbox["width"]
        else:
            # Fallback: estimate content column from link position
            clip_x = max(0, link_bbox["x"] - 30)
            clip_w = min(600, 1280 - clip_x)

        # Vertical bounds: use parent paragraph if found, otherwise link + context
        pad = 12
        if parent_bbox:
            clip_y = max(0, parent_bbox["y"] - pad)
            clip_h = parent_bbox["height"] + pad * 2
            # Ensure the link itself is fully within the clip
            link_bottom = link_bbox["y"] + link_bbox["height"] + pad
            if link_bottom > clip_y + clip_h:
                clip_h = link_bottom - clip_y
        else:
            # No paragraph found — use link bbox with context lines
            clip_y = max(0, link_bbox["y"] - 45)
            clip_h = link_bbox["height"] + 90

        clip = {
            "x": max(0, clip_x - pad),
            "y": clip_y,
            "width": clip_w + pad * 2,
            "height": clip_h,
        }
        await page.screenshot(path=path, clip=clip)
        return path
    except Exception:
        # Fall back to section-level screenshot
        try:
            section_selector = selector.split(" ")[0] if " " in selector else selector
            element = page.locator(section_selector).first
            if await element.count() > 0:
                await element.scroll_into_view_if_needed(timeout=3000)
                await page.wait_for_timeout(300)
                await element.screenshot(path=path)
                return path
        except Exception:
            pass
    return ""


async def take_full_page_screenshot(page: Page, output_dir: str) -> str:
    """Take a full-page screenshot. Returns path."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "full_page.png")
    await page.screenshot(path=path, full_page=True)
    return path


async def capture_issue_screenshots(page: Page, issues: list[Issue], output_dir: str):
    """Take screenshots for all issues."""
    for i, issue in enumerate(issues):
        if issue.element_selector:
            if issue.issue_type == "link":
                path = await take_link_screenshot(
                    page, issue.element_selector, output_dir, f"issue_{i}"
                )
            else:
                path = await take_element_screenshot(
                    page, issue.element_selector, output_dir, f"issue_{i}"
                )
            issue.screenshot_path = path

        # Capture extra screenshots (e.g., additional images in duplicate alt text groups)
        if issue.extra_screenshots:
            resolved_paths = []
            for j, selector in enumerate(issue.extra_screenshots):
                path = await take_element_screenshot(
                    page, selector, output_dir, f"issue_{i}_extra_{j}"
                )
                resolved_paths.append(path)
            issue.extra_screenshots = resolved_paths
