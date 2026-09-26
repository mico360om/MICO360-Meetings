"""Transcription languages: "auto" plus the languages Whisper knows, and
normalisation of whatever a user typed or an old build stored ("AR", "ar-SA",
"Arabic", "Auto") into a code faster-whisper accepts. Pure logic — no Qt.
"""
from __future__ import annotations

import re  # noqa: F401  (kept for parity with the original helpers)


# ===========================================================================
# Transcription language: "auto" + the languages Whisper knows
# ===========================================================================
# Display names for Whisper's language codes (the code list itself comes from
# faster_whisper when it is installed; this table is the fallback list too).
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "zh": "Chinese", "de": "German", "es": "Spanish", "ru": "Russian",
    "ko": "Korean", "fr": "French", "ja": "Japanese", "pt": "Portuguese", "tr": "Turkish",
    "pl": "Polish", "ca": "Catalan", "nl": "Dutch", "ar": "Arabic", "sv": "Swedish",
    "it": "Italian", "id": "Indonesian", "hi": "Hindi", "fi": "Finnish", "vi": "Vietnamese",
    "he": "Hebrew", "uk": "Ukrainian", "el": "Greek", "ms": "Malay", "cs": "Czech",
    "ro": "Romanian", "da": "Danish", "hu": "Hungarian", "ta": "Tamil", "no": "Norwegian",
    "th": "Thai", "ur": "Urdu", "hr": "Croatian", "bg": "Bulgarian", "lt": "Lithuanian",
    "la": "Latin", "mi": "Maori", "ml": "Malayalam", "cy": "Welsh", "sk": "Slovak",
    "te": "Telugu", "fa": "Persian", "lv": "Latvian", "bn": "Bengali", "sr": "Serbian",
    "az": "Azerbaijani", "sl": "Slovenian", "kn": "Kannada", "et": "Estonian",
    "mk": "Macedonian", "br": "Breton", "eu": "Basque", "is": "Icelandic", "hy": "Armenian",
    "ne": "Nepali", "mn": "Mongolian", "bs": "Bosnian", "kk": "Kazakh", "sq": "Albanian",
    "sw": "Swahili", "gl": "Galician", "mr": "Marathi", "pa": "Punjabi", "si": "Sinhala",
    "km": "Khmer", "sn": "Shona", "yo": "Yoruba", "so": "Somali", "af": "Afrikaans",
    "oc": "Occitan", "ka": "Georgian", "be": "Belarusian", "tg": "Tajik", "sd": "Sindhi",
    "gu": "Gujarati", "am": "Amharic", "yi": "Yiddish", "lo": "Lao", "uz": "Uzbek",
    "fo": "Faroese", "ht": "Haitian Creole", "ps": "Pashto", "tk": "Turkmen",
    "nn": "Norwegian Nynorsk", "mt": "Maltese", "sa": "Sanskrit", "lb": "Luxembourgish",
    "my": "Myanmar (Burmese)", "bo": "Tibetan", "tl": "Tagalog", "mg": "Malagasy",
    "as": "Assamese", "tt": "Tatar", "haw": "Hawaiian", "ln": "Lingala", "ha": "Hausa",
    "ba": "Bashkir", "jw": "Javanese", "su": "Sundanese", "yue": "Cantonese",
}
# Common alternative spellings / ISO-639 codes users type.
_LANG_ALIASES = {
    "iw": "he", "jv": "jw", "fil": "tl", "nb": "no", "in": "id",
    "eng": "en", "ara": "ar", "urd": "ur", "fra": "fr", "fre": "fr", "deu": "de",
    "ger": "de", "spa": "es", "hin": "hi", "zho": "zh", "chi": "zh", "fas": "fa",
    "per": "fa", "tur": "tr", "rus": "ru", "por": "pt", "ita": "it", "jpn": "ja",
    "kor": "ko", "farsi": "fa", "mandarin": "zh", "filipino": "tl", "burmese": "my",
}
_AUTO_WORDS = {"", "auto", "automatic", "auto-detect", "autodetect", "auto detect",
               "detect", "any", "none"}


def language_codes() -> list[str]:
    """Whisper's language codes (from faster_whisper if available)."""
    try:
        from faster_whisper.tokenizer import _LANGUAGE_CODES
        codes = [str(c).lower() for c in _LANGUAGE_CODES]
        if codes:
            return codes
    except Exception:
        pass
    return list(LANGUAGE_NAMES)


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code.upper())


def normalize_language(value) -> str:
    """Turn whatever is stored or typed ("AR", "ar-SA", "Arabic", "Auto", "")
    into a code Whisper accepts ("ar") — or "auto" if it isn't a language."""
    v = str(value or "").strip().lower().replace("_", "-")
    if v in _AUTO_WORDS:
        return "auto"
    codes = language_codes()
    if v in codes:
        return v
    base = v.split("-")[0].split(" ")[0].strip()
    for cand in (v, base):
        cand = _LANG_ALIASES.get(cand, cand)
        if cand in codes:
            return cand
    for code in codes:                                  # a language name ("Arabic")
        name = LANGUAGE_NAMES.get(code, "").lower()
        if name and (v == name or v.split("(")[0].strip() == name):
            return code
    return "auto"
