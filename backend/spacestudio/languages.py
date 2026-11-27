"""Language support: what Claude writes, which voice reads it, which font draws it.

Codes used in VideoConfig.language / narration_language: "en", "hi" (Hindi, Devanagari) and "hinglish"
(Hindi-English code-mix). Any other code is passed through as-is (Claude writes in that language).

Voices are the free ones the engines already ship:
- lemo-opuscar core/tts/tts_zh.py: Microsoft edge-tts neural voices (online, no key, free; "reads other languages
  too" per its docstring). Hindi voices: hi-IN-SwaraNeural (female), hi-IN-MadhurNeural (male).
- lemo-opuscar core/tts/tts.py: Kokoro (offline). Kokoro v1.0 has Hindi voices (hf_alpha, hf_beta, hm_omega,
  hm_psi) with lang "hi"; plainer than edge-tts. Used when the user picks the offline voice.
"""
from __future__ import annotations

from typing import Optional

LANGUAGES: dict[str, dict] = {
    "en": {"name": "English", "script": "Latin", "asr": "en", "tts_lang": "en-us"},
    "hi": {
        "name": "Hindi (हिन्दी)", "script": "Devanagari", "asr": "hi", "tts_lang": "hi",
        "voices": [
            {"id": "hi-IN-SwaraNeural", "name": "Swara (female, natural, online)", "tool": "edge"},
            {"id": "hi-IN-MadhurNeural", "name": "Madhur (male, natural, online)", "tool": "edge"},
            {"id": "hf_alpha", "name": "Kokoro female (offline, plainer)", "tool": "kokoro"},
            {"id": "hm_omega", "name": "Kokoro male (offline, plainer)", "tool": "kokoro"},
        ],
    },
    "hinglish": {
        "name": "Hinglish (Hindi + English)", "script": "Latin captions, Devanagari narration", "asr": "hi",
        "tts_lang": "hi",
        "voices": [
            {"id": "hi-IN-SwaraNeural", "name": "Swara (female, natural, online)", "tool": "edge"},
            {"id": "hi-IN-MadhurNeural", "name": "Madhur (male, natural, online)", "tool": "edge"},
            {"id": "hf_alpha", "name": "Kokoro female (offline, plainer)", "tool": "kokoro"},
            {"id": "hm_omega", "name": "Kokoro male (offline, plainer)", "tool": "kokoro"},
        ],
    },
}

DEVANAGARI_FONTS = ("Nirmala UI", "Noto Sans Devanagari", "Mangal")


def info(code: Optional[str]) -> dict:
    return LANGUAGES.get((code or "en").lower(), {"name": code or "en", "script": "", "asr": (code or "en")[:2],
                                                    "tts_lang": code or "en"})


def is_indic(code: Optional[str]) -> bool:
    return (code or "").lower() in ("hi", "hinglish")


def needs_devanagari_on_screen(code: Optional[str]) -> bool:
    """On-screen text and captions in Devanagari (Hinglish captions are Roman)."""
    return (code or "").lower() == "hi"


def default_voice(code: Optional[str]) -> Optional[str]:
    v = info(code).get("voices")
    return v[0]["id"] if v else None


def voice_tool(voice_id: Optional[str]) -> str:
    """'edge' for Microsoft neural voices (xx-XX-NameNeural), else 'kokoro'."""
    return "edge" if voice_id and voice_id.endswith("Neural") else "kokoro"


def writing_rules(language: Optional[str], narration_language: Optional[str] = None) -> str:
    """Instructions every script-writing / on-screen-text prompt gets."""
    lang = (language or "en").lower()
    narr = (narration_language or lang).lower()
    if lang == "hi" or narr == "hi":
        return (
            "LANGUAGE: Hindi. Write all narration, on-screen text and captions in natural, conversational Hindi in "
            "Devanagari script (हिन्दी), the way a good Hindi documentary narrator speaks, not a word-for-word "
            "translation. Keep widely used English technical terms where Hindi speakers actually use them. In "
            "script.json put the Devanagari line in both `text` (captions) and `spoken` (what the voice reads), with "
            "numbers written out as Hindi words in `spoken`. Research may use English sources; the output is Hindi.")
    if lang == "hinglish" or narr == "hinglish":
        return (
            "LANGUAGE: Hinglish (Hindi-English code-mix, as on Indian YouTube/Instagram). Sentences are Hindi in "
            "structure with natural English words and phrases mixed in. In script.json: `text` (captions and "
            "on-screen text) is Roman-script Hinglish, e.g. \"Aaj hum dekhenge ki Mars laal kyun hai\"; `spoken` "
            "(what the Hindi voice reads) is the SAME sentence with Hindi words in Devanagari and English words left "
            "in English, e.g. \"आज हम देखेंगे कि Mars लाल क्यों है\", numbers as words. A Hindi neural voice reads "
            "that naturally; never feed it Roman-script Hindi.")
    if lang != "en":
        return f"LANGUAGE: write all narration, on-screen text and captions in {info(lang)['name']} ({lang})."
    return ""


def font_rules(language: Optional[str]) -> str:
    """Instructions for anything that draws text (engine pages, Skia boards, title cards)."""
    if not needs_devanagari_on_screen(language):
        return ""
    return ("FONTS: on-screen text is Devanagari. Latin-only fonts (Bebas Neue, Inter, most display fonts) draw empty "
            "boxes for it. Use a font with Devanagari glyphs: download Noto Sans Devanagari or Hind (SIL Open Font "
            "License, Google Fonts) into the project and credit it, or use the system font Nirmala UI on Windows. "
            "Check every still for boxes or broken conjuncts (e.g. क्ष, त्र, श्र) before rendering.")


def caption_font(language: Optional[str], windows: bool) -> Optional[str]:
    """Font for burned-in captions (libass), or None for its default."""
    if needs_devanagari_on_screen(language):
        return "Nirmala UI" if windows else "Noto Sans Devanagari"
    return None


def presets() -> list[dict]:
    return [{"id": k, "name": v["name"], "voices": v.get("voices", [])} for k, v in LANGUAGES.items()]
