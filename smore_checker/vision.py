import base64
import json
import httpx
import anthropic

_client = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _encode_image_url(url: str) -> tuple[str, str]:
    """Download image and return (base64_data, media_type)."""
    resp = httpx.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    ct = resp.headers.get("content-type", "image/jpeg")
    if "png" in ct:
        media_type = "image/png"
    elif "webp" in ct:
        media_type = "image/webp"
    elif "gif" in ct:
        media_type = "image/gif"
    else:
        media_type = "image/jpeg"
    return base64.standard_b64encode(resp.content).decode(), media_type


def _parse_json_response(text: str, fallback: dict) -> dict:
    """Extract JSON from a model response, handling markdown code blocks."""
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback


def classify_image(image_url: str, section_text: str = "") -> dict:
    """Classify an image as content vs decorative, and if content, whether it's a flyer.

    Returns dict with keys:
        is_content: bool
        is_flyer: bool
        has_qr_code: bool
        suggested_alt: str (if content image)
        extracted_text: str (if flyer, English only)
        key_details: list[str] (if flyer - English only)
    """
    b64, media_type = _encode_image_url(image_url)
    client = get_client()

    section_context = ""
    if section_text:
        section_context = f"\n\nSURROUNDING SECTION TEXT (for context — do NOT repeat details from this text in suggested alt text):\n{section_text[:500]}"

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                },
                {
                    "type": "text",
                    "text": f"""Analyze this image from a school newsletter. Answer these questions in JSON format:

1. Is this a CONTENT image (photo, flyer, informational graphic uploaded by staff) or a DECORATIVE/UI element (icon, divider, spacer, border, background pattern)?
2. Is this a FLYER or informational document? Classify as a flyer if it contains ANY of: event details, dates, times, locations, registration info, structured lists of information, schedules, announcements with actionable details. This includes event posters, program schedules, sign-up forms, informational graphics with text details — not just traditional paper flyers.
3. If it's a flyer, does it contain a QR code?
4. Suggest appropriate alt text (brief, descriptive, under 125 characters). Do NOT start with "image of" or "photo of" — screen readers already announce it as an image. IMPORTANT RULES for alt text:
   - For newsletter header/title card images: ONLY include the informational content (newsletter name, issue number, date). Do NOT describe decorative elements like borders, holiday themes, colors, or ornamental design.
   - For flyers: Keep it short and simple — just identify what the flyer is about (e.g. "Flyer for the Spring Science Fair"). Do NOT list dates, times, or details in the alt text.
   - For photos: Describe ONLY what is clearly and objectively visible. Do NOT infer, guess, or assume details that cannot be directly seen — such as specific grade levels, names of activities, event names, or other context requiring outside knowledge. Describe visible people, settings, objects, and actions. For example, say "students in holiday costumes participating in indoor activities" NOT "6th and 8th grade students in team building activities."
   - Do NOT repeat specific details (dates, times, event names) that already appear in the surrounding section text.
5. If it's a flyer, extract the ENGLISH text only. Ignore any Spanish or other non-English text on the flyer.
6. If it's a flyer, list the key details IN ENGLISH ONLY: event name, date, time, location, and any other important information. Skip any details that only appear in a non-English portion.
{section_context}
Respond ONLY with valid JSON:
{{
    "is_content": true/false,
    "is_flyer": true/false,
    "has_qr_code": true/false,
    "suggested_alt": "...",
    "extracted_text": "...",
    "key_details": ["Event: ...", "Date: ...", "Time: ...", "Location: ...", ...]
}}""",
                },
            ],
        }],
    )

    return _parse_json_response(response.content[0].text, {
        "is_content": True,
        "is_flyer": False,
        "has_qr_code": False,
        "suggested_alt": "",
        "extracted_text": "",
        "key_details": [],
    })


def evaluate_alt_text(image_url: str, current_alt: str, section_text: str = "") -> dict:
    """Evaluate whether existing alt text meaningfully describes the image.

    Returns dict with:
        is_effective: bool
        reason: str (why it's ineffective, if applicable)
        suggested_alt: str
    """
    b64, media_type = _encode_image_url(image_url)
    client = get_client()

    section_context = ""
    if section_text:
        section_context = f"\n\nSURROUNDING SECTION TEXT (for context — do NOT repeat details from this text in your suggested alt text):\n{section_text[:500]}"

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                },
                {
                    "type": "text",
                    "text": f"""This image has the following alt text: "{current_alt}"

Evaluate whether this alt text would be genuinely helpful to someone who cannot see the image. Only flag it as INEFFECTIVE if it:
- Is a filename or hash (e.g. "IMG_2034.jpg", "a3f8b2c1")
- Is a generic term like "image", "photo", "picture", "graphic" — or just "flyer" with no context about WHAT flyer
- Is just a file type or format
- Starts with "image of" or "photo of" (redundant — screen readers announce images)
- Completely fails to describe or relate to what's in the image
- Is just a single word that doesn't describe the content

IMPORTANT — for flyer/event images specifically:
- Alt text like "Parent to Parent Workshop flyer" or "Science Fair flyer" IS effective — it tells the user what the flyer is about. Do NOT flag this.
- Only flag flyer alt text if it's a filename, completely generic (just "flyer" with no event name), or identifies the wrong event entirely.
- When evaluating flyer alt text, focus ONLY on whether the informational content is correctly identified. Do NOT mention visual design elements like illustrations, decorative borders, layout, or color schemes in your reason.
- A flyer may contain MULTIPLE dates for different things (e.g. a fundraiser date AND a trip date). If the alt text correctly references one of them, do NOT flag it just because other dates also appear in the image. The alt text does not need to include every detail.

DO NOT flag alt text as ineffective if it contains specific accurate details like dates, times, or event names, even if the image contains additional information not mentioned. More specific alt text is BETTER than less specific — never suggest a replacement that is less informative than the original.

DO NOT flag alt text as ineffective if it is informal, casual, or imperfect but still gives a blind reader a reasonable sense of what the image shows. School staff often write alt text in their own words with context a viewer wouldn't have (e.g. names of people, inside references) — this is GOOD alt text, not ineffective. The bar is: would a screen reader user get useful information from this? If yes, it's effective.

For the "reason" field, use a neutral, descriptive tone. Simply describe what the alt text says and what the image shows, without sounding like you're correcting a mistake. Do NOT describe visual design elements (illustrations, borders, colors, layout). Only focus on informational content. For example:
- Good: "The alt text says 'event flyer' but the image is a flyer for the Spring Science Fair competition"
- Bad: "The alt text doesn't mention the colorful illustration of students or the decorative border"

When suggesting replacement alt text:
- For flyers: Keep it short — just identify what the flyer is about (e.g. "Flyer for the Spring Science Fair"). Do NOT list dates, times, or event details.
- For newsletter headers/title cards: Only include informational content (name, issue number, date). Do NOT describe decorative borders, themes, or colors.
- For photos: Describe ONLY what is clearly and objectively visible. Do NOT infer, guess, or assume details that cannot be directly seen (grade levels, activity names, event names, etc.). Stick to visible people, settings, objects, and actions.
- Do NOT repeat specific details that already appear in the surrounding section text.
{section_context}
Respond ONLY with valid JSON:
{{
    "is_effective": true/false,
    "reason": "neutral description of what alt text says vs what image shows, if ineffective",
    "suggested_alt": "suggested replacement (under 125 chars, don't start with 'image of'/'photo of')"
}}""",
                },
            ],
        }],
    )

    return _parse_json_response(response.content[0].text, {
        "is_effective": True,
        "reason": "",
        "suggested_alt": "",
    })


def compare_flyer_to_section_text(key_details: list[str], extracted_text: str, section_text: str) -> dict:
    """Semantically compare flyer content against section text.

    Returns dict with:
        has_missing_info: bool - True only if genuinely important info is missing
        missing_details: list[str] - human-readable descriptions of what's missing
    """
    if not key_details and not extracted_text:
        return {"has_missing_info": False, "missing_details": []}

    details_block = "\n".join(f"- {d}" for d in key_details)
    client = get_client()

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": f"""A school newsletter post has a flyer image embedded in a section. You need to decide: would a person who can ONLY read the post text (cannot see the flyer at all) have enough information to understand and participate in the event?

KEY DETAILS EXTRACTED FROM FLYER (note: OCR extraction may contain errors):
{details_block}

SECTION BODY TEXT:
{section_text}

Only flag CRITICAL actionable information that is completely absent from the section text — things a reader would need in order to participate. Specifically, only flag these categories if they appear on the flyer but NOWHERE in the section text:
- Event date or deadline (if the text mentions ANY date, it's covered)
- Event time
- Event location or address
- Registration link or how to sign up
- Key eligibility requirements (who can participate, age limits, etc.)
- Cost or fees

Do NOT flag:
- Event names, titles, or branding differences
- Motivational text, slogans, or decorative text
- Descriptions of what the event is about (if the section text gives the general idea, it's covered)
- Prizes, incentives, or secondary details
- Any information that can be inferred from context
- Contradictions or differences between flyer and text (your OCR may be wrong)
- Anything where the section text covers the same category even with different wording or values

The standard is: can the reader understand what the event is and how to participate? If yes, pass it.

Respond ONLY with valid JSON:
{{
    "has_missing_info": true/false,
    "missing_details": ["what critical info is missing, e.g. 'Event time (7:00 PM) not mentioned in text'"]
}}

When in doubt, return {{"has_missing_info": false, "missing_details": []}}.""",
        }],
    )

    text = response.content[0].text
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"has_missing_info": False, "missing_details": []}


def suggest_link_text(current_text: str, section_text: str) -> str:
    """Use Claude to suggest better link text based on section context.

    Returns a specific suggested link text string.
    """
    client = get_client()

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=128,
        messages=[{
            "role": "user",
            "content": f"""A school newsletter has a link with vague or generic text. Based on the surrounding section context, suggest a specific, descriptive replacement for the link text.

CURRENT LINK TEXT: "{current_text}"

SECTION CONTEXT:
{section_text[:800]}

Suggest a short, specific link text (under 60 characters) that describes the destination or action. Use the section context to make it specific — for example, if the section is about a "Parent to Parent Workshop" and the link says "Click here to register", suggest "Register for the Parent to Parent Workshop".

Respond with ONLY the suggested link text, nothing else. No quotes, no explanation.""",
        }],
    )

    return response.content[0].text.strip().strip('"').strip("'")
