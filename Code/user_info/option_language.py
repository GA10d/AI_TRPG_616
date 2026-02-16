from enum import Enum

class OptionLanguage(Enum):
    '''
    语言选项枚举类，包含常用语言及其代码。
     每个枚举成员包含一个唯一的 ID、语言名称和语言代码。
     语言代码遵循 ISO 639-1 标准，部分语言使用了区域代码以区分不同的变体（如中文简体和繁体）。
     该枚举类还提供了一个 classmethod 用于根据语言代码获取对应的枚举成员，以及一个实例方法用于生成系统提示语，要求回复严格使用指定的语言。
    '''
    CHINESE_SIMPLIFIED = (1, "中文（简体）", "zh-CN")
    CHINESE_TRADITIONAL = (2, "中文（繁體）", "zh-TW")
    ENGLISH = (3, "English", "en")
    JAPANESE = (4, "日本語", "ja")
    KOREAN = (5, "한국어", "ko")
    FRENCH = (6, "Français", "fr")
    GERMAN = (7, "Deutsch", "de")
    SPANISH = (8, "Español", "es")
    PORTUGUESE = (9, "Português", "pt")
    ITALIAN = (10, "Italiano", "it")

    # —— 常用扩展 ——
    RUSSIAN = (11, "Русский", "ru")
    ARABIC = (12, "العربية", "ar")
    HINDI = (13, "हिन्दी", "hi")
    DUTCH = (14, "Nederlands", "nl")
    SWEDISH = (15, "Svenska", "sv")
    NORWEGIAN = (16, "Norsk", "no")
    DANISH = (17, "Dansk", "da")
    FINNISH = (18, "Suomi", "fi")
    UKRAINIAN = (19, "Українська", "uk")
    POLISH = (20, "Polski", "pl")

    # —— 东欧 / 巴尔干 ——
    CZECH = (21, "Čeština", "cs")
    SLOVAK = (22, "Slovenčina", "sk")
    HUNGARIAN = (23, "Magyar", "hu")
    ROMANIAN = (24, "Română", "ro")
    BULGARIAN = (25, "Български", "bg")
    SERBIAN = (26, "Српски", "sr")

    # —— 南亚 ——
    BENGALI = (27, "বাংলা", "bn")
    URDU = (28, "اردو", "ur")
    TAMIL = (29, "தமிழ்", "ta")
    TELUGU = (30, "తెలుగు", "te")

    # —— 东南亚 ——
    THAI = (31, "ไทย", "th")
    VIETNAMESE = (32, "Tiếng Việt", "vi")
    INDONESIAN = (33, "Bahasa Indonesia", "id")
    MALAY = (34, "Bahasa Melayu", "ms")
    FILIPINO = (35, "Filipino", "fil")

    # —— 中东 / 其他 ——
    HEBREW = (36, "עברית", "he")
    PERSIAN = (37, "فارسی", "fa")
    TURKISH = (38, "Türkçe", "tr")
    GREEK = (39, "Ελληνικά", "el")
    LATIN = (40, "Latina", "la")


    def __new__(cls, id: int, label: str, code: str):
        '''
        自定义 __new__ 方法以支持枚举成员的多个属性。
        '''
        obj = object.__new__(cls)
        obj._value_ = id        
        obj.id = id
        obj.label = label
        obj.code = code
        return obj

    @classmethod
    def from_code(cls, code: str) -> "OptionLanguage":
        '''
         根据语言代码获取对应的枚举成员，如果未找到则返回默认值（中文简体）。
        '''
        for item in cls:
            if item.code == code:
                return item
        return cls.CHINESE_SIMPLIFIED

    def system_prompt(self) -> str:
        '''
         生成系统提示语，要求回复严格使用指定的语言。
        '''
        return f"Reply strictly in {self.label} ({self.code})."
