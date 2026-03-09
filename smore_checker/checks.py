import re
from urllib.parse import urlparse

import httpx

from .models import PageData, SmoreSection, Issue, ImageInfo, LinkInfo, HeadingInfo, EmbedInfo
from .vision import classify_image, compare_flyer_to_section_text, evaluate_alt_text, suggest_link_text

# Generic/meaningless link text patterns (all lowercase for comparison)
GENERIC_LINK_TEXT = {
    "here", "click here", "read more", "learn more", "more info",
    "more information", "this link", "link", "click", "info",
    "enlace", "use this link",
    # Spanish equivalents
    "aquí", "clic aquí", "haga clic aquí", "más información",
    "link de inscripción", "enlace de inscripción",
    "haga clic", "presione aquí",
}

# Incomplete link text patterns (starts with action phrase)
# These are checked, but "click here to [specific action]" is allowed if descriptive
INCOMPLETE_LINK_PATTERNS = [
    r"^click here for\b",
    r"^click to\b",
    r"^read more about\b",
    r"^learn more about\b",
    r"^go here to\b",
    r"^tap here to\b",
]

# "Click here to" phrases that are too vague even with the action
VAGUE_CLICK_HERE_ACTIONS = {
    "learn more", "read more", "find out", "find out more", "see more",
    "get more info", "get more information", "register", "sign up",
    "view", "view it", "see it", "check it out", "see details",
}

# Phrases that are still vague even when followed by "here"
# e.g. "more information here" is vague, but "Register for back to school night here" is fine
VAGUE_BEFORE_HERE = {
    "more information", "more info", "learn more", "read more",
    "register", "sign up", "click", "go", "see more", "find out more",
    "más información", "regístrese", "inscríbase",
}

FILE_DOWNLOAD_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xls", ".xlsx",
    ".zip", ".rar", ".7z", ".csv",
}

VIDEO_FILE_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".wmv", ".mkv", ".webm", ".flv",
}

YOUTUBE_DOMAINS = {"youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com"}

# Regex to match emoji characters (covers most common emoji ranges)
EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002702-\U000027B0"  # dingbats
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero width joiner
    "\U000023CF-\U000023FA"  # misc technical
    "\U00002600-\U000026FF"  # misc symbols
    "\U00002B50-\U00002B55"  # stars
    "\U0000203C\U00002049"   # exclamation marks
    "\U0000231A\U0000231B"   # watch/hourglass
    "\U000025AA-\U000025FE"  # geometric shapes
    "]+"
)


def _is_url_text(text: str) -> bool:
    """Check if link text looks like a raw URL."""
    return bool(re.match(r"^https?://", text.strip())) or bool(re.match(r"^www\.", text.strip()))


def _is_filename_alt(alt: str) -> bool:
    """Check if alt text looks like a filename or raw URL."""
    alt = alt.strip()
    if re.match(r"^https?://", alt):
        return True
    if re.match(r"^www\.", alt):
        return True
    # Check for common file extensions
    if re.search(r"\.(jpg|jpeg|png|gif|svg|webp|bmp|tiff|pdf)$", alt, re.IGNORECASE):
        return True
    # Check for hash-like filenames
    if re.match(r"^[a-f0-9]{8,}$", alt, re.IGNORECASE):
        return True
    return False


def _resolve_url(url: str) -> str:
    """Follow redirects to get the final URL. Returns the resolved URL."""
    try:
        resp = httpx.head(url, follow_redirects=True, timeout=10)
        return str(resp.url)
    except Exception:
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=10)
            return str(resp.url)
        except Exception:
            return url


def _get_extension(url: str) -> str:
    """Get file extension from URL path."""
    path = urlparse(url).path
    if "." in path:
        return "." + path.rsplit(".", 1)[-1].lower()
    return ""


def _is_youtube(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return any(yt in domain for yt in YOUTUBE_DOMAINS)


def _is_vimeo(url: str) -> bool:
    return "vimeo.com" in urlparse(url).netloc.lower()


def check_images(section: SmoreSection) -> list[Issue]:
    """Check all images in a section for accessibility issues."""
    issues = []
    content_images = []  # (img, classification, eval_result_or_None)

    section_text = section.text

    for img in section.images:
        # Use Claude vision to classify the image
        try:
            classification = classify_image(img.src, section_text=section_text)
        except Exception as e:
            print(f"  Warning: Could not classify image {img.src}: {e}")
            classification = {"is_content": True, "is_flyer": False, "has_qr_code": False}

        if not classification.get("is_content", True):
            continue  # Skip decorative images

        suggested_alt = classification.get("suggested_alt", "")
        eval_result = None  # Track for dedup with duplicate check

        # Check: missing alt text
        if not img.alt.strip():
            issues.append(Issue(
                issue_type="image",
                category="Missing Alt Text",
                description="This image is missing alternative text, which means screen reader users won't know what it shows.",
                suggestion="Add descriptive alt text to this image.",
                section_name=section.name,
                element_selector=img.element_selector,
                suggested_alt=suggested_alt,
            ))
        elif _is_filename_alt(img.alt):
            issues.append(Issue(
                issue_type="image",
                category="Filename as Alt Text",
                description="The alt text appears to be a filename or URL rather than a meaningful description.",
                suggestion="Replace with descriptive alt text that explains what the image shows.",
                section_name=section.name,
                element_selector=img.element_selector,
                current_alt=img.alt,
                suggested_alt=suggested_alt,
            ))
        elif len(img.alt) > 200:
            issues.append(Issue(
                issue_type="image",
                category="Alt Text Too Long",
                description=f"The alt text is {len(img.alt)} characters long, which is too verbose for a screen reader. It appears the full content of a flyer may have been placed in the alt text.",
                suggestion="Keep alt text short and descriptive (under 125 characters). If this image contains important text content, put that content in the post body text instead.",
                section_name=section.name,
                element_selector=img.element_selector,
                current_alt=img.alt[:150] + "...",
                suggested_alt=suggested_alt,
            ))
        else:
            # Check: ineffective alt text (vision-based evaluation)
            try:
                eval_result = evaluate_alt_text(img.src, img.alt, section_text=section_text)
                if not eval_result.get("is_effective", True):
                    reason = eval_result.get("reason", "")
                    new_alt = eval_result.get("suggested_alt", suggested_alt)
                    issues.append(Issue(
                        issue_type="image",
                        category="Ineffective Alt Text",
                        description=f"This alt text doesn't effectively describe the image. {reason}",
                        suggestion="Replace with more descriptive alt text.",
                        section_name=section.name,
                        element_selector=img.element_selector,
                        current_alt=img.alt,
                        suggested_alt=new_alt,
                    ))
            except Exception as e:
                print(f"  Warning: Could not evaluate alt text for {img.src}: {e}")

        content_images.append((img, classification, eval_result))

        # Flyer-specific checks
        if classification.get("is_flyer"):
            issues.extend(_check_flyer(img, section, classification))

    # Check: duplicate alt text within section (merges with ineffective if both apply)
    issues = _merge_duplicate_alt_text(content_images, section, issues)

    return issues


def _merge_duplicate_alt_text(content_images: list[tuple], section: SmoreSection, existing_issues: list[Issue]) -> list[Issue]:
    """Check for duplicate alt text. If images are also flagged as ineffective, merge into one issue."""
    # Group content images by alt text
    alt_groups: dict[str, list[tuple]] = {}
    for img, classification, eval_result in content_images:
        alt = img.alt.strip()
        if alt:
            alt_groups.setdefault(alt, []).append((img, classification, eval_result))

    for alt, group in alt_groups.items():
        if len(group) < 2:
            continue

        count = len(group)
        first_img = group[0][0]

        # Collect unique suggested alts from vision
        suggestions = []
        for img, classification, eval_result in group:
            suggested = ""
            if eval_result and not eval_result.get("is_effective", True):
                suggested = eval_result.get("suggested_alt", "")
            if not suggested:
                suggested = classification.get("suggested_alt", "")
            if suggested and suggested != alt and suggested not in suggestions:
                suggestions.append(suggested)

        # Check if any of these images already have an ineffective alt text issue
        ineffective_selectors = set()
        for img, classification, eval_result in group:
            if eval_result and not eval_result.get("is_effective", True):
                ineffective_selectors.add(img.element_selector)

        if ineffective_selectors:
            # Remove individual ineffective alt text issues for these images — we'll merge them
            existing_issues = [
                iss for iss in existing_issues
                if not (iss.category == "Ineffective Alt Text" and iss.element_selector in ineffective_selectors)
            ]

            # Create merged issue
            description = (
                f"{count} images in this section share the same alt text, and it doesn't effectively describe "
                f"what's in the images. Each image should have its own unique, descriptive alt text so screen "
                f"reader users can tell them apart and understand what each image shows."
            )
        else:
            description = (
                f"{count} images in this section share the same alt text. Each image should have its own unique "
                f"description so screen reader users can tell them apart."
            )

        suggestion = "Give each image unique alt text describing what's specifically visible in that photo."

        # Collect all image selectors and per-image suggested alts
        all_selectors = [img.element_selector for img, _, _ in group]
        per_image_alts = []
        for img, classification, eval_result in group:
            s = ""
            if eval_result and not eval_result.get("is_effective", True):
                s = eval_result.get("suggested_alt", "")
            if not s:
                s = classification.get("suggested_alt", "")
            per_image_alts.append(s if s and s != alt else "")

        existing_issues.append(Issue(
            issue_type="image",
            category="Duplicate Alt Text",
            description=description,
            suggestion=suggestion,
            section_name=section.name,
            element_selector=all_selectors[0],
            current_alt=alt,
            extra_suggested_alts=per_image_alts,
            extra_screenshots=all_selectors[1:],  # Store selectors; resolved to paths later
        ))

    return existing_issues


def _get_suggested_link_text(current_text: str, section: SmoreSection) -> str:
    """Get a context-aware suggested link text using Claude."""
    try:
        return suggest_link_text(current_text, section.text)
    except Exception:
        return ""


def _check_flyer(img: ImageInfo, section: SmoreSection, classification: dict) -> list[Issue]:
    """Check flyer-specific accessibility issues."""
    issues = []
    section_text = section.text
    key_details = classification.get("key_details", [])
    extracted_text = classification.get("extracted_text", "")

    # Semantic comparison: does the section text convey the same key info as the flyer?
    try:
        comparison = compare_flyer_to_section_text(key_details, extracted_text, section_text)
    except Exception as e:
        print(f"  Warning: Could not compare flyer to section text: {e}")
        comparison = {"has_missing_info": False, "missing_details": []}

    if comparison.get("has_missing_info") and comparison.get("missing_details"):
        issues.append(Issue(
            issue_type="flyer",
            category="Flyer Info Not in Text",
            description="This flyer contains important details that don't appear in the surrounding text. People who can't see the flyer will miss this information:",
            suggestion="Add the missing information to the text of this section so everyone can access it, not just people who can see the image.",
            section_name=section.name,
            element_selector=img.element_selector,
            missing_details=comparison["missing_details"],
        ))

    # Check for QR codes — search ALL links in the section
    if classification.get("has_qr_code"):
        all_section_links = section.links
        if not all_section_links:
            issues.append(Issue(
                issue_type="flyer",
                category="QR Code Without Link",
                description="This flyer contains a QR code, but there's no text link in this section. People who can't scan QR codes (screen reader users, people on desktop) need a clickable text link instead.",
                suggestion="Add a text link in this section that goes to the same destination as the QR code.",
                section_name=section.name,
                element_selector=img.element_selector,
            ))
        else:
            # Check if any link uses raw URL as text
            for link in all_section_links:
                if _is_url_text(link.text):
                    suggested = _get_suggested_link_text(link.text, section)
                    issues.append(Issue(
                        issue_type="link",
                        category="Raw URL as Link Text",
                        description=f"There's a link for the QR code destination, but it uses a raw URL as the link text: \"{link.text}\". This isn't meaningful for screen reader users.",
                        suggestion="Replace the raw URL with descriptive link text that tells people where the link goes.",
                        section_name=section.name,
                        element_selector=link.element_selector,
                        suggested_alt=suggested,
                    ))

    return issues


def check_links(section: SmoreSection) -> list[Issue]:
    """Check all links in a section for accessibility issues."""
    issues = []

    for link in section.links:
        text = link.text.strip()
        text_lower = text.lower()

        # Check: generic/meaningless link text (case-insensitive)
        if text_lower in GENERIC_LINK_TEXT:
            suggested = _get_suggested_link_text(text, section)
            issues.append(Issue(
                issue_type="link",
                category="Generic Link Text",
                description=f"The link text \"{text}\" doesn't describe where the link goes. Screen reader users often navigate by links alone and need descriptive link text.",
                suggestion=f"Replace \"{text}\" with text that describes the destination, like the name of the page or resource it links to.",
                section_name=section.name,
                element_selector=link.element_selector,
                suggested_alt=suggested,
            ))
            continue

        # Check: link text ending with "here" — only flag if preceding text is vague
        if text_lower.endswith(" here") or text_lower.endswith(" aquí"):
            suffix = " here" if text_lower.endswith(" here") else " aquí"
            before_here = text_lower[:-len(suffix)].strip().rstrip(".")
            if before_here in VAGUE_BEFORE_HERE:
                suggested = _get_suggested_link_text(text, section)
                issues.append(Issue(
                    issue_type="link",
                    category="Vague Link Text",
                    description=f"The link text \"{text}\" doesn't clearly describe the destination. Link text should make sense on its own, out of context.",
                    suggestion=f"Rephrase so the link text describes the destination.",
                    section_name=section.name,
                    element_selector=link.element_selector,
                    suggested_alt=suggested,
                ))
            # If preceding text is descriptive (e.g. "Register for back to school night here"), don't flag
            continue

        # Check: "click here to [action]" — only flag if action is vague
        click_here_match = re.match(r"^click here to\s+(.+)$", text_lower, re.IGNORECASE)
        if click_here_match:
            action = click_here_match.group(1).strip().rstrip(".")
            if action.lower() in VAGUE_CLICK_HERE_ACTIONS:
                suggested = _get_suggested_link_text(text, section)
                issues.append(Issue(
                    issue_type="link",
                    category="Vague Link Text",
                    description=f"The link text \"{text}\" doesn't clearly describe the destination. Link text should make sense on its own, out of context.",
                    suggestion=f"Rephrase so the link text describes the destination.",
                    section_name=section.name,
                    element_selector=link.element_selector,
                    suggested_alt=suggested,
                ))
            # If action is specific (e.g. "place your spirit wear order"), don't flag
            continue

        # Check: incomplete link text (other patterns)
        for pattern in INCOMPLETE_LINK_PATTERNS:
            if re.match(pattern, text_lower, re.IGNORECASE):
                suggested = _get_suggested_link_text(text, section)
                issues.append(Issue(
                    issue_type="link",
                    category="Vague Link Text",
                    description=f"The link text \"{text}\" starts with an action phrase but doesn't fully describe the destination. Link text should make sense on its own, out of context.",
                    suggestion=f"Rephrase so the link text describes the destination.",
                    section_name=section.name,
                    element_selector=link.element_selector,
                    suggested_alt=suggested,
                ))
                break

        # Check: raw URL as link text
        if _is_url_text(text):
            suggested = _get_suggested_link_text(text, section)
            issues.append(Issue(
                issue_type="link",
                category="Raw URL as Link Text",
                description=f"The link displays a raw URL as its text: \"{text[:80]}...\". This is hard to read and doesn't describe the destination.",
                suggestion="Replace the URL with descriptive text that tells readers where the link goes.",
                section_name=section.name,
                element_selector=link.element_selector,
                suggested_alt=suggested,
            ))
            continue

        # Resolve the link and check destination type
        resolved = link.original_href
        if link.original_href.startswith("http"):
            resolved = _resolve_url(link.original_href)

        ext = _get_extension(resolved)

        # Check: file download
        if ext in FILE_DOWNLOAD_EXTENSIONS:
            # Skip aptg.co shortlinks — these point to remediated accessible files
            original_domain = urlparse(link.original_href).netloc.lower()
            resolved_domain = urlparse(resolved).netloc.lower()
            if "aptg.co" in original_domain or "aptg.co" in resolved_domain:
                pass  # Already remediated, skip
            elif any(d in resolved_domain for d in ("drive.google.com", "docs.google.com")):
                issues.append(Issue(
                    issue_type="link",
                    category="Links to File Download",
                    description=f"This link points to a Google Drive file ({ext} file). Files hosted on Google Drive have not been remediated for accessibility and may have issues with headings, alt text, reading order, etc.",
                    suggestion=f"Send the file to webdeveloper@eusd.org to have it remediated, or embed the content directly in the Smore post.",
                    section_name=section.name,
                    element_selector=link.element_selector,
                ))
            else:
                issues.append(Issue(
                    issue_type="link",
                    category="Links to File Download",
                    description=f"This link points to a downloadable file ({ext} file). Downloaded files must also be accessible (proper headings, alt text, reading order, etc.).",
                    suggestion=f"Consider embedding the content from this {ext} file directly in the Smore post instead of linking to a download. If the file must be linked, ensure the file itself is accessible.",
                    section_name=section.name,
                    element_selector=link.element_selector,
                ))

        # Check: direct video file
        if ext in VIDEO_FILE_EXTENSIONS:
            issues.append(Issue(
                issue_type="video",
                category="Direct Video File Link",
                description="This link points directly to a video file. Video content must be captioned for accessibility.",
                suggestion="Please send your video file to webdeveloper@eusd.org who will caption it and provide you with a YouTube link to use instead.",
                section_name=section.name,
                element_selector=link.element_selector,
            ))

    return issues


def check_embeds(section: SmoreSection) -> list[Issue]:
    """Check video embeds for accessibility issues."""
    issues = []

    for embed in section.embeds:
        src = embed.src

        # YouTube embeds are fine
        if _is_youtube(src):
            continue

        # Non-YouTube video embeds
        if _is_vimeo(src) or embed.tag in ("VIDEO",):
            issues.append(Issue(
                issue_type="video",
                category="Non-YouTube Video Embed",
                description="This video is embedded from a non-YouTube source. Video content must be captioned for accessibility.",
                suggestion="Please send your video file to webdeveloper@eusd.org who will caption it and provide you with a YouTube link to use instead.",
                section_name=section.name,
                element_selector=embed.element_selector,
            ))

    return issues


def check_headings(page_data: PageData) -> list[Issue]:
    """Check heading hierarchy across the full page."""
    issues = []
    headings = page_data.all_headings

    if not headings:
        return issues

    prev_level = 0
    for heading in headings:
        level = heading.level

        # Check for skipped levels (e.g., H2 -> H4)
        if prev_level > 0 and level > prev_level + 1:
            skipped = ", ".join(f"H{l}" for l in range(prev_level + 1, level))
            issues.append(Issue(
                issue_type="heading",
                category="Skipped Heading Level",
                description=f"The heading \"{heading.text}\" is an H{level}, but the previous heading was an H{prev_level}. This skips {skipped}, which breaks the document outline for screen reader users.",
                suggestion=f"Change this heading to an H{prev_level + 1} to maintain proper heading hierarchy, or add the missing intermediate heading levels.",
                section_name=heading.section_name or "(Page Level)",
                element_selector=f"section#{heading.block_id}" if heading.block_id else "",
            ))

        prev_level = level

    return issues


EMOJI_SUGGESTION = (
    "Emojis are read aloud by screen readers using their full name, which can be disruptive. "
    "Avoid using emojis at the beginning of sentences or paragraphs, avoid multiple emojis in a row, "
    "and use them sparingly overall. One at the end of a sentence is generally acceptable."
)


def check_emojis(section: SmoreSection) -> list[Issue]:
    """Check for problematic emoji usage in a section."""
    issues = []

    for block in section.blocks:
        text = block.text_content
        if not text:
            continue

        selector = f"section#{block.section_id}"

        # Check emojis in headings
        for heading in block.headings:
            if EMOJI_RE.search(heading.text):
                issues.append(Issue(
                    issue_type="emoji",
                    category="Emoji in Heading",
                    description=f"The heading \"{heading.text}\" contains emoji characters. Screen readers read each emoji aloud by its full descriptive name, which can make headings confusing and hard to navigate.",
                    suggestion=EMOJI_SUGGESTION,
                    section_name=section.name,
                    element_selector=selector,
                ))

        # Check for consecutive emojis (2+ in a row)
        if re.search(r"(?:" + EMOJI_RE.pattern + r"[\s\uFE0F]*){2,}", text):
            issues.append(Issue(
                issue_type="emoji",
                category="Consecutive Emojis",
                description="This section has multiple emojis in a row. Screen readers read each emoji by its full descriptive name, so a string of emojis can be very disruptive.",
                suggestion=EMOJI_SUGGESTION,
                section_name=section.name,
                element_selector=selector,
            ))

        # Check for emojis at the beginning of sentences/paragraphs
        # Split into sentences by newlines and sentence-ending punctuation
        sentences = re.split(r"[\n]+", text)
        emoji_start_count = 0
        for sentence in sentences:
            stripped = sentence.strip()
            if stripped and EMOJI_RE.match(stripped):
                emoji_start_count += 1

        if emoji_start_count >= 1:
            # Check if it's being used as bullet points (multiple lines) or just sentence starts
            if emoji_start_count >= 2:
                issues.append(Issue(
                    issue_type="emoji",
                    category="Emojis as Bullet Points",
                    description=f"It looks like emojis are being used as bullet points or list markers ({emoji_start_count} lines start with an emoji). Screen readers will read the emoji name before each item.",
                    suggestion=EMOJI_SUGGESTION,
                    section_name=section.name,
                    element_selector=selector,
                ))
            else:
                issues.append(Issue(
                    issue_type="emoji",
                    category="Emoji at Start of Text",
                    description="This section begins with an emoji. Screen readers will read the emoji's full descriptive name before the actual content, which can be confusing.",
                    suggestion=EMOJI_SUGGESTION,
                    section_name=section.name,
                    element_selector=selector,
                ))

    return issues


def run_all_checks(page_data: PageData, verbose: bool = True) -> list[Issue]:
    """Run all accessibility checks and return a list of issues."""
    all_issues = []

    # Section-level checks
    for section in page_data.sections:
        if verbose:
            print(f"  Checking section: {section.name}")

        # Image checks (includes flyer checks, alt text eval, duplicate alt)
        if section.images:
            if verbose:
                print(f"    Checking {len(section.images)} image(s)...")
            all_issues.extend(check_images(section))

        # Link checks
        if section.links:
            if verbose:
                print(f"    Checking {len(section.links)} link(s)...")
            all_issues.extend(check_links(section))

        # Embed checks
        if section.embeds:
            if verbose:
                print(f"    Checking {len(section.embeds)} embed(s)...")
            all_issues.extend(check_embeds(section))

        # Emoji checks
        all_issues.extend(check_emojis(section))

    # Page-level heading check
    if verbose:
        print("  Checking heading hierarchy...")
    all_issues.extend(check_headings(page_data))

    return all_issues
