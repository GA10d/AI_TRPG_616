from __future__ import annotations

from enum import Enum


class OptionLanguage(Enum):
    CHINESE_SIMPLIFIED = (1, "Simplified Chinese", "zh-CN", "简体中文")
    CHINESE_TRADITIONAL = (2, "Traditional Chinese", "zh-TW", "繁體中文")
    ENGLISH = (3, "English", "en", "English")
    JAPANESE = (4, "Japanese", "ja", "日本語")
    KOREAN = (5, "Korean", "ko", "한국어")
    FRENCH = (6, "French", "fr", "Français")
    GERMAN = (7, "German", "de", "Deutsch")
    SPANISH = (8, "Spanish", "es", "Español")
    PORTUGUESE = (9, "Portuguese", "pt", "Português")
    ITALIAN = (10, "Italian", "it", "Italiano")
    RUSSIAN = (11, "Russian", "ru", "Русский")
    ARABIC = (12, "Arabic", "ar", "العربية")
    HINDI = (13, "Hindi", "hi", "हिन्दी")
    DUTCH = (14, "Dutch", "nl", "Nederlands")
    SWEDISH = (15, "Swedish", "sv", "Svenska")
    NORWEGIAN = (16, "Norwegian", "no", "Norsk")
    DANISH = (17, "Danish", "da", "Dansk")
    FINNISH = (18, "Finnish", "fi", "Suomi")
    UKRAINIAN = (19, "Ukrainian", "uk", "Українська")
    POLISH = (20, "Polish", "pl", "Polski")
    CZECH = (21, "Czech", "cs", "Čeština")
    SLOVAK = (22, "Slovak", "sk", "Slovenčina")
    HUNGARIAN = (23, "Hungarian", "hu", "Magyar")
    ROMANIAN = (24, "Romanian", "ro", "Română")
    BULGARIAN = (25, "Bulgarian", "bg", "Български")
    SERBIAN = (26, "Serbian", "sr", "Српски")
    BENGALI = (27, "Bengali", "bn", "বাংলা")
    URDU = (28, "Urdu", "ur", "اردو")
    TAMIL = (29, "Tamil", "ta", "தமிழ்")
    TELUGU = (30, "Telugu", "te", "తెలుగు")
    THAI = (31, "Thai", "th", "ไทย")
    VIETNAMESE = (32, "Vietnamese", "vi", "Tiếng Việt")
    INDONESIAN = (33, "Indonesian", "id", "Bahasa Indonesia")
    MALAY = (34, "Malay", "ms", "Bahasa Melayu")
    FILIPINO = (35, "Filipino", "fil", "Filipino")
    HEBREW = (36, "Hebrew", "he", "עברית")
    PERSIAN = (37, "Persian", "fa", "فارسی")
    TURKISH = (38, "Turkish", "tr", "Türkçe")
    GREEK = (39, "Greek", "el", "Ελληνικά")
    LATIN = (40, "Latin", "la", "Latina")

    def __new__(cls, id: int, label: str, code: str, native_label: str):
        obj = object.__new__(cls)
        obj._value_ = id
        obj.id = id
        obj.label = label
        obj.code = code
        obj.native_label = native_label
        return obj

    @classmethod
    def from_code(cls, code: str) -> "OptionLanguage":
        normalized = (code or "").strip().casefold()
        for item in cls:
            if item.code.casefold() == normalized:
                return item
        return cls.CHINESE_SIMPLIFIED

    @classmethod
    def list_options(cls) -> list["OptionLanguage"]:
        return list(cls)

    def system_prompt(self) -> str:
        return f"Reply strictly in {self.label} ({self.code})."

    def to_payload(self) -> dict[str, str | int]:
        return {
            "id": self.id,
            "code": self.code,
            "label": self.label,
            "native_label": self.native_label,
        }
