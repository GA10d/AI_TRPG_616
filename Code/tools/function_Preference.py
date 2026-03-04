import json
import os
import tempfile
from copy import deepcopy

import data.data_Path as data_path


def deep_merge(base: dict, override: dict) -> dict:
    """
    深合并：返回一个新 dict
    - base: 默认配置
    - override: 用户配置（覆盖 base）
    规则：
    - 两边都是 dict -> 递归合并
    - 否则 -> 用 override 覆盖
    """
    result = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class PreferenceManager:
    def __init__(
        self,
        init_path=data_path.PATH_DATA_DEFAULT_PREFERENCE,
        path=data_path.PATH_DATA_PREFERENCE,
    ):
        self.init_path = init_path
        self.path = path
        self.prefs = self._pref_init()

    def _load(self, path=None):
        if path is None:
            path = self.path
        if not os.path.exists(path):
            return None  # 用 None 区分“文件不存在/读失败” vs “合法空字典”
        try:
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            return obj if isinstance(obj, dict) else None
        except (json.JSONDecodeError, OSError):
            return None

    def _pref_init(self):
        default_prefs = self._load(self.init_path) or {}
        user_prefs = self._load(self.path) or {}

        merged = deep_merge(default_prefs, user_prefs)
        self._save_atomic(merged)
        return merged

    def save(self):
        self._save_atomic(self.prefs)

    def get(self, key, default=None):
        return self.prefs.get(key, default)

    def set(self, key, value):
        self.prefs[key] = value
        self.save()

    def set_nested(self, dotted_key: str, value):
        """
        设置嵌套的配置项
        例如 set_nested("ui.theme", "light") 会在 prefs 中设置 {"ui": {"theme": "light"}}
        """
        cur = self.prefs
        parts = dotted_key.split(".")
        for p in parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = value
        self.save()

    def _save_atomic(self, prefs: dict):
        dir_name = os.path.dirname(self.path) or "."
        os.makedirs(dir_name, exist_ok=True)

        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                delete=False,
                dir=dir_name,
                encoding="utf-8",
            ) as tmp:
                json.dump(prefs, tmp, ensure_ascii=False, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_name = tmp.name

            os.replace(tmp_name, self.path)
        finally:
            if tmp_name and os.path.exists(tmp_name):
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass