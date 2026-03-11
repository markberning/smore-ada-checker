"""Vision router — shared infrastructure + provider delegation.

All vision calls go through this module. It handles caching, batching,
image pre-filtering, verbose logging, and rate limiting. Actual API calls
are delegated to the active provider (vision_claude or vision_gemini).
"""
import hashlib
import json
import os
import time
import httpx

_cache = None
_cache_path = os.path.join(os.path.dirname(__file__), "vision_cache.json")

# --- Provider selection ---

_provider_name = os.environ.get("VISION_PROVIDER", "claude").lower()

if _provider_name == "gemini":
    from . import vision_gemini as _provider
else:
    from . import vision_claude as _provider

provider_name = _provider.PROVIDER_NAME
provider_label = _provider_name  # "claude" or "gemini" — used in filenames
_rate_limit_delay = _provider.RATE_LIMIT_DELAY

# --- Stats for verbose logging ---

_stats = {"api_calls": 0, "cache_hits": 0, "start_time": None}


def reset_stats():
    _stats["api_calls"] = 0
    _stats["cache_hits"] = 0
    _stats["start_time"] = time.time()


def get_stats() -> dict:
    elapsed = time.time() - _stats["start_time"] if _stats["start_time"] else 0
    return {**_stats, "elapsed": elapsed}


# --- Verbose logging ---

_verbose = False


def set_verbose(enabled: bool):
    global _verbose
    _verbose = enabled


def _log(msg: str):
    if _verbose:
        elapsed = time.time() - _stats["start_time"] if _stats["start_time"] else 0
        print(f"  [vision {elapsed:6.1f}s] {msg}")


# --- Cache ---

def _load_cache() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    if os.path.exists(_cache_path):
        try:
            with open(_cache_path, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            _cache = {}
    else:
        _cache = {}
    return _cache


def _save_cache():
    if _cache is not None:
        with open(_cache_path, "w", encoding="utf-8") as f:
            json.dump(_cache, f, indent=2, ensure_ascii=False)


def clear_cache():
    global _cache
    _cache = {}
    if os.path.exists(_cache_path):
        os.remove(_cache_path)


def _cache_key(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_get(key: str):
    cache = _load_cache()
    result = cache.get(key)
    if result is not None:
        _stats["cache_hits"] += 1
        _log(f"CACHE HIT ({_stats['cache_hits']} hits, {_stats['api_calls']} calls)")
    return result


def _cache_set(key: str, value):
    cache = _load_cache()
    cache[key] = value
    _save_cache()


# --- Image utilities ---

def _download_image(url: str) -> tuple[bytes, str]:
    resp = httpx.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    ct = resp.headers.get("content-type", "image/jpeg")
    if "png" in ct:
        mime_type = "image/png"
    elif "webp" in ct:
        mime_type = "image/webp"
    elif "gif" in ct:
        mime_type = "image/gif"
    else:
        mime_type = "image/jpeg"
    return resp.content, mime_type


def _get_image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    try:
        from struct import unpack
        data = image_bytes
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            w, h = unpack('>II', data[16:24])
            return (w, h)
        if data[:2] == b'\xff\xd8':
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    h, w = unpack('>HH', data[i + 5:i + 9])
                    return (w, h)
                length = unpack('>H', data[i + 2:i + 4])[0]
                i += 2 + length
            return (0, 0)
        if data[:6] in (b'GIF87a', b'GIF89a'):
            w, h = unpack('<HH', data[6:10])
            return (w, h)
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            if data[12:16] == b'VP8 ':
                w = unpack('<H', data[26:28])[0] & 0x3FFF
                h = unpack('<H', data[28:30])[0] & 0x3FFF
                return (w, h)
            elif data[12:16] == b'VP8L':
                bits = unpack('<I', data[21:25])[0]
                w = (bits & 0x3FFF) + 1
                h = ((bits >> 14) & 0x3FFF) + 1
                return (w, h)
            elif data[12:16] == b'VP8X':
                w = (data[24] | data[25] << 8 | data[26] << 16) + 1
                h = (data[27] | data[28] << 8 | data[29] << 16) + 1
                return (w, h)
        return (0, 0)
    except Exception:
        return (0, 0)


def is_too_small(image_url: str) -> bool:
    try:
        image_bytes, _ = _download_image(image_url)
        w, h = _get_image_dimensions(image_bytes)
        if w > 0 and h > 0 and w < 100 and h < 100:
            _log(f"SKIP small image ({w}x{h}): {image_url[-50:]}")
            return True
        return False
    except Exception:
        return False


def _parse_json_response(text: str, fallback: dict) -> dict:
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback


# --- Rate-limited API call wrapper ---

def _rate_limited_call(call_fn, description: str):
    delay = _rate_limit_delay
    if delay > 0:
        _log(f"API CALL: {description} (sleeping {delay}s...)")
        time.sleep(delay)
    else:
        _log(f"API CALL: {description}")
    _stats["api_calls"] += 1
    try:
        result = call_fn()
        _log(f"SUCCESS ({_stats['cache_hits']} hits, {_stats['api_calls']} calls)")
        return result
    except Exception as e:
        err_str = str(e)
        if "429" in err_str:
            _log(f"RATE LIMITED: {err_str[:100]}")
        else:
            _log(f"ERROR: {err_str[:100]}")
        raise


# --- Prompts (identical for both providers) ---

def _classify_prompt(n: int, section_text: str = "") -> str:
    section_context = ""
    if section_text:
        section_context = f"\n\nSURROUNDING SECTION TEXT (for context — do NOT repeat details from this text in suggested alt text):\n{section_text[:500]}"

    if n == 1:
        preamble = "Analyze this image from a school newsletter. Answer these questions in JSON format:"
    else:
        preamble = f"Analyze these {n} images from a school newsletter. For EACH image (numbered 1 through {n} in the order they appear above), answer these questions."

    prompt = f"""{preamble}

{"For each image:" if n > 1 else ""}
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
{section_context}"""

    obj = """{{
    "is_content": true/false,
    "is_flyer": true/false,
    "has_qr_code": true/false,
    "suggested_alt": "...",
    "extracted_text": "...",
    "key_details": ["Event: ...", "Date: ...", "Time: ...", "Location: ...", ...]
}}"""

    if n == 1:
        prompt += f"\nRespond ONLY with valid JSON:\n{obj}"
    else:
        prompt += f"\nRespond ONLY with a valid JSON array of {n} objects, one per image in order:\n[\n    {obj},\n    ...\n]"

    return prompt


def _eval_alt_prompt(current_alt: str, section_text: str = "") -> str:
    section_context = ""
    if section_text:
        section_context = f"\n\nSURROUNDING SECTION TEXT (for context — do NOT repeat details from this text in your suggested alt text):\n{section_text[:500]}"

    return f"""This image has the following alt text: "{current_alt}"

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
}}"""


def _compare_flyer_prompt(key_details: list[str], extracted_text: str, section_text: str) -> str:
    details_block = "\n".join(f"- {d}" for d in key_details)
    return f"""A school newsletter post has a flyer image embedded in a section. You need to decide: would a person who can ONLY read the post text (cannot see the flyer at all) have enough information to understand and participate in the event?

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

When in doubt, return {{"has_missing_info": false, "missing_details": []}}."""


def _suggest_link_prompt(current_text: str, section_text: str) -> str:
    return f"""A school newsletter has a link with vague or generic text. Based on the surrounding section context, suggest a specific, descriptive replacement for the link text.

CURRENT LINK TEXT: "{current_text}"

SECTION CONTEXT:
{section_text[:800]}

Suggest a short, specific link text (under 60 characters) that describes the destination or action. Use the section context to make it specific — for example, if the section is about a "Parent to Parent Workshop" and the link says "Click here to register", suggest "Register for the Parent to Parent Workshop".

Respond with ONLY the suggested link text, nothing else. No quotes, no explanation."""


# --- Public API ---

def classify_images_batch(image_urls: list[str], section_text: str = "") -> list[dict]:
    """Classify up to 5 images per API call. Returns list of classification dicts."""
    fallback = {
        "is_content": True,
        "is_flyer": False,
        "has_qr_code": False,
        "suggested_alt": "",
        "extracted_text": "",
        "key_details": [],
    }

    results = [None] * len(image_urls)
    uncached_indices = []
    uncached_urls = []
    image_data_list = []

    for i, url in enumerate(image_urls):
        key = _cache_key("classify", url, section_text[:200])
        cached = _cache_get(key)
        if cached is not None:
            results[i] = cached
        else:
            uncached_indices.append(i)
            uncached_urls.append(url)

    if not uncached_urls:
        return results

    for url in uncached_urls:
        try:
            image_bytes, mime_type = _download_image(url)
            image_data_list.append((image_bytes, mime_type))
        except Exception as e:
            _log(f"Download failed: {url[-50:]}: {e}")
            image_data_list.append(None)

    valid_batch = []
    for idx, data in enumerate(image_data_list):
        if data is not None:
            valid_batch.append((idx, data[0], data[1]))

    if not valid_batch:
        for i in uncached_indices:
            if results[i] is None:
                results[i] = fallback.copy()
        return results

    batch_size = 5
    for batch_start in range(0, len(valid_batch), batch_size):
        batch = valid_batch[batch_start:batch_start + batch_size]
        n = len(batch)
        prompt = _classify_prompt(n, section_text)
        img_data = [(img_bytes, mime) for _, img_bytes, mime in batch]
        max_tokens = 1024 * n

        try:
            if n == 1:
                text = _rate_limited_call(
                    lambda: _provider.call_vision(img_data[0][0], img_data[0][1], prompt, max_tokens),
                    f"classify 1 image",
                )
            else:
                text = _rate_limited_call(
                    lambda: _provider.call_vision_batch(img_data, prompt, max_tokens),
                    f"batch classify {n} images",
                )

            if n == 1:
                batch_results = [_parse_json_response(text, fallback)]
            else:
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip()
                try:
                    batch_results = json.loads(text)
                    if not isinstance(batch_results, list):
                        batch_results = [batch_results]
                except json.JSONDecodeError:
                    batch_results = []

            for j, (uncached_idx, _, _) in enumerate(batch):
                original_idx = uncached_indices[uncached_idx]
                if j < len(batch_results) and isinstance(batch_results[j], dict):
                    result = batch_results[j]
                else:
                    result = fallback.copy()
                results[original_idx] = result
                url = uncached_urls[uncached_idx]
                key = _cache_key("classify", url, section_text[:200])
                _cache_set(key, result)

        except Exception as e:
            _log(f"Batch classify failed: {e}")
            for uncached_idx, _, _ in batch:
                original_idx = uncached_indices[uncached_idx]
                if results[original_idx] is None:
                    results[original_idx] = fallback.copy()

    for i in range(len(results)):
        if results[i] is None:
            results[i] = fallback.copy()

    return results


def classify_image(image_url: str, section_text: str = "") -> dict:
    return classify_images_batch([image_url], section_text)[0]


def evaluate_alt_text(image_url: str, current_alt: str, section_text: str = "") -> dict:
    fallback = {"is_effective": True, "reason": "", "suggested_alt": ""}

    key = _cache_key("eval_alt", image_url, current_alt, section_text[:200])
    cached = _cache_get(key)
    if cached is not None:
        return cached

    image_bytes, mime_type = _download_image(image_url)
    prompt = _eval_alt_prompt(current_alt, section_text)

    text = _rate_limited_call(
        lambda: _provider.call_vision(image_bytes, mime_type, prompt, 512),
        f"evaluate alt text: {image_url[-50:]}",
    )

    result = _parse_json_response(text, fallback)
    _cache_set(key, result)
    return result


def compare_flyer_to_section_text(key_details: list[str], extracted_text: str, section_text: str) -> dict:
    fallback = {"has_missing_info": False, "missing_details": []}

    if not key_details and not extracted_text:
        return fallback

    details_str = "|".join(key_details)
    key = _cache_key("compare_flyer", details_str, extracted_text[:200], section_text[:200])
    cached = _cache_get(key)
    if cached is not None:
        return cached

    prompt = _compare_flyer_prompt(key_details, extracted_text, section_text)

    text = _rate_limited_call(
        lambda: _provider.call_text(prompt, 512),
        f"compare flyer to section text",
    )

    result = _parse_json_response(text, fallback)
    _cache_set(key, result)
    return result


def suggest_link_text(current_text: str, section_text: str) -> str:
    key = _cache_key("suggest_link", current_text, section_text[:200])
    cached = _cache_get(key)
    if cached is not None:
        return cached

    prompt = _suggest_link_prompt(current_text, section_text)

    text = _rate_limited_call(
        lambda: _provider.call_text(prompt, 128),
        f"suggest link text for: {current_text}",
    )

    result = text.strip().strip('"').strip("'")
    _cache_set(key, result)
    return result
