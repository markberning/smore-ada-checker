# Unified Vision Provider Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Merge gemini-vision branch into main as a single configurable codebase that supports both Claude Sonnet and Gemini Flash vision providers, switchable via .env.

**Architecture:** A router module (`vision_router.py`) delegates all vision calls to provider-specific modules (`vision_claude.py`, `vision_gemini.py`) based on `VISION_PROVIDER` env var. Shared infrastructure (caching, pre-filtering, batching, verbose logging) lives in the router. Provider modules only contain API-specific call logic.

**Tech Stack:** Python 3.14, anthropic SDK, google-genai SDK, httpx

---

### Task 1: Prepare main branch — commit gemini-vision work and switch to main

**Context:** All gemini-vision work is uncommitted. We need to save the current Gemini vision.py and checks.py before switching branches.

**Step 1: Save copies of Gemini files we'll need**

```bash
cp smore_checker/vision.py smore_checker/vision_gemini_backup.py
cp smore_checker/checks.py smore_checker/checks_gemini_backup.py
cp check.py check_gemini_backup.py
cp .gitignore gitignore_backup
```

**Step 2: Discard working tree changes and switch to main**

```bash
git checkout -- .
git checkout main
```

**Step 3: Restore Gemini backups as reference files**

```bash
cp smore_checker/vision_gemini_backup.py smore_checker/vision_gemini_ref.py
cp smore_checker/checks_gemini_backup.py smore_checker/checks_gemini_ref.py
cp check_gemini_backup.py check_gemini_ref.py
```

**Step 4: Create feature branch from main**

```bash
git checkout -b unified-vision-provider
```

---

### Task 2: Install both SDKs

**Files:**
- Modify: `requirements.txt`

**Step 1: Update requirements.txt**

```
anthropic>=0.84.0
google-genai>=1.0.0
httpx>=0.28.0
playwright>=1.58.0
python-dotenv>=1.0.0
```

**Step 2: Install google-genai into venv**

```bash
source venv/Scripts/activate
pip install google-genai
```

---

### Task 3: Create vision_claude.py — Claude-specific API calls

**Files:**
- Create: `smore_checker/vision_claude.py`
- Reference: `smore_checker/vision.py` (current file on main — this IS the Claude code)

**Context:** The current `vision.py` on main contains all the Claude API call logic. We need to extract just the API call functions into a provider module. The provider module should NOT contain caching, batching, or logging — those live in the router.

**Step 1: Create vision_claude.py**

This module exposes 4 functions that match the provider interface:
- `classify_image(image_bytes, mime_type, prompt) -> str` — raw API call, returns response text
- `evaluate_alt_text(image_bytes, mime_type, prompt) -> str` — raw API call, returns response text
- `compare_flyer(prompt) -> str` — text-only API call, returns response text
- `suggest_link(prompt) -> str` — text-only API call, returns response text
- `classify_images_batch(image_data_list, prompt) -> str` — batch API call, returns response text

Each function takes pre-built prompt text and image data, calls the Claude API, and returns the raw response text. No JSON parsing, no caching, no sleeping — just the API call.

Also expose:
- `PROVIDER_NAME = "Claude Sonnet"`
- `MODEL = "claude-sonnet-4-20250514"`
- `RATE_LIMIT_DELAY = 0` (no delay needed)
- `get_client()` — lazy client initialization

The API call format for Claude (from current vision.py on main):
```python
response = client.messages.create(
    model=MODEL,
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64_data}},
            {"type": "text", "text": prompt},
        ],
    }],
)
return response.content[0].text
```

Note: Claude uses base64-encoded image data, not raw bytes. The provider must handle encoding.

**Step 2: Verify import**

```bash
python -c "from smore_checker.vision_claude import PROVIDER_NAME; print(PROVIDER_NAME)"
```

Expected: `Claude Sonnet`

---

### Task 4: Create vision_gemini.py — Gemini-specific API calls

**Files:**
- Create: `smore_checker/vision_gemini.py`
- Reference: `smore_checker/vision_gemini_ref.py` (backup from gemini-vision branch)

**Context:** Same interface as vision_claude.py but using the google-genai SDK.

**Step 1: Create vision_gemini.py**

Same 4+1 functions with identical signatures. The API call format for Gemini:
```python
from google import genai
from google.genai import types

response = client.models.generate_content(
    model=MODEL,
    contents=[
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        prompt,
    ],
    config=types.GenerateContentConfig(max_output_tokens=1024),
)
return response.text
```

Expose:
- `PROVIDER_NAME = "Gemini Flash"`
- `MODEL = "gemini-2.5-flash"`
- `RATE_LIMIT_DELAY = 2` (2s delay for free tier)
- `get_client()` — lazy client initialization

Note: Gemini uses raw bytes, not base64.

**Step 2: Verify import**

```bash
python -c "from smore_checker.vision_gemini import PROVIDER_NAME; print(PROVIDER_NAME)"
```

Expected: `Gemini Flash`

---

### Task 5: Create vision_router.py — shared infrastructure + routing

**Files:**
- Create: `smore_checker/vision_router.py`
- Reference: `smore_checker/vision_gemini_ref.py` (has caching, batching, pre-filtering, verbose logging)

**Context:** This is the main module. It contains ALL shared infrastructure from the gemini-vision branch (caching, batching, image pre-filtering, verbose logging, rate limiting) and delegates actual API calls to the active provider. Everything else in the codebase imports from this module.

**Step 1: Create vision_router.py with these components:**

1. **Provider selection** — reads `VISION_PROVIDER` from env, defaults to `claude`, imports the matching provider module
2. **Caching** — JSON file cache at `smore_checker/vision_cache.json`, keyed by SHA-256 hash. Functions: `clear_cache()`, `_cache_get()`, `_cache_set()`, `_load_cache()`, `_save_cache()`
3. **Verbose logging** — `set_verbose()`, `_log()`, `reset_stats()`, `get_stats()`
4. **Image utilities** — `_download_image()`, `_get_image_dimensions()`, `is_too_small()` (< 100x100 skip), `_parse_json_response()`
5. **Rate limiting** — `_rate_limited_call()` that uses the provider's `RATE_LIMIT_DELAY` (0 for Claude, 2 for Gemini)
6. **Public API** (same signatures as current vision.py on both branches):
   - `classify_image(image_url, section_text)` — with cache check, download, delegate to provider
   - `classify_images_batch(image_urls, section_text)` — batch up to 5, cache per-image
   - `evaluate_alt_text(image_url, current_alt, section_text)` — with cache check
   - `compare_flyer_to_section_text(key_details, extracted_text, section_text)` — with cache check
   - `suggest_link_text(current_text, section_text)` — with cache check

The router builds the prompts (same prompts as current code — they're identical between branches), downloads images, checks cache, and only calls the provider for the actual API request.

**Step 2: Verify import**

```bash
python -c "from smore_checker.vision_router import classify_image, clear_cache, set_verbose; print('OK')"
```

---

### Task 6: Update checks.py to import from vision_router

**Files:**
- Modify: `smore_checker/checks.py`

**Step 1: Change the import line**

From (current on main):
```python
from .vision import classify_image, compare_flyer_to_section_text, evaluate_alt_text, suggest_link_text
```

To:
```python
from .vision_router import classify_image, classify_images_batch, compare_flyer_to_section_text, evaluate_alt_text, suggest_link_text, is_too_small
```

**Step 2: Port the batching and pre-filtering changes from gemini-vision branch**

Replace `check_images()` to use batch classification and size pre-filtering (from `checks_gemini_ref.py`).

Reorder `run_all_checks()` to run non-vision checks first per section (from `checks_gemini_ref.py`).

**Step 3: Verify import**

```bash
python -c "from smore_checker.checks import run_all_checks; print('OK')"
```

---

### Task 7: Update check.py CLI with --verbose, --clear-cache, and startup message

**Files:**
- Modify: `check.py`

**Step 1: Add flag parsing, startup message, and stats summary**

Port the CLI changes from `check_gemini_ref.py`: `--verbose`, `--clear-cache` flags, `reset_stats()`, `get_stats()`.

Add provider startup message after loading:
```python
from smore_checker.vision_router import provider_name
print(f"Vision provider: {provider_name}")
```

**Step 2: Test flags**

```bash
python check.py --clear-cache
python check.py --help  # should show usage
```

---

### Task 8: Update .env.example and .gitignore

**Files:**
- Modify: `.env.example`
- Modify: `.gitignore`

**Step 1: Update .env.example**

```
# Vision provider: "claude" (default) or "gemini"
VISION_PROVIDER=claude

# Required for Claude vision (default provider)
ANTHROPIC_API_KEY=sk-ant-...

# Required for Gemini vision (alternative provider)
GOOGLE_API_KEY=AIza...
```

**Step 2: Add vision cache to .gitignore**

Add line: `smore_checker/vision_cache.json`

---

### Task 9: Delete old vision.py and reference files, update CLAUDE.md

**Files:**
- Delete: `smore_checker/vision.py` (replaced by vision_router.py + providers)
- Delete: `smore_checker/vision_gemini_ref.py`, `smore_checker/checks_gemini_ref.py`, `check_gemini_ref.py`, `smore_checker/vision_gemini_backup.py`, `smore_checker/checks_gemini_backup.py`, `check_gemini_backup.py`, `gitignore_backup`
- Create: `CLAUDE.md` (project root)

**Step 1: Remove old files**

```bash
rm smore_checker/vision.py
rm smore_checker/vision_gemini_ref.py smore_checker/checks_gemini_ref.py check_gemini_ref.py
rm smore_checker/vision_gemini_backup.py smore_checker/checks_gemini_backup.py check_gemini_backup.py gitignore_backup
```

**Step 2: Create CLAUDE.md**

Document: VISION_PROVIDER config, both API keys, caching + --clear-cache, --verbose, per-provider sleep delay, recommendation to use Claude as primary.

**Step 3: Full test run**

```bash
# Test with Claude (if credits available)
VISION_PROVIDER=claude python check.py --verbose https://app.smore.com/n/6bytj

# Test with Gemini
VISION_PROVIDER=gemini python check.py --verbose --clear-cache https://app.smore.com/n/6bytj
```

---

### Task 10: Commit and create PR to main

**Step 1: Stage and commit**

```bash
git add -A
git commit -m "feat: unified vision provider with Claude/Gemini support

Add configurable VISION_PROVIDER (.env) to switch between Claude Sonnet
and Gemini Flash. Shared infrastructure: result caching, batch
classification, image pre-filtering, verbose logging."
```

**Step 2: Push and create PR**

```bash
git push -u origin unified-vision-provider
gh pr create --title "Unified vision provider: Claude + Gemini" --body "..."
```
