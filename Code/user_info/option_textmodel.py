from enum import Enum

class OptionTextModel(Enum):
    CHINESE_SIMPLIFIED = (1, "中文（简体）", "zh-CN")




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
    def from_code(cls, code: str) -> "Option_Language":
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
