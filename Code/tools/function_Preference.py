import json
import os
import tempfile
import threading
from copy import deepcopy

import data.data_Path as data_path


_PREFERENCE_LOCKS: dict[str, threading.Lock] = {}
_PREFERENCE_LOCKS_GUARD = threading.Lock()


def deep_merge(base: dict, override: dict) -> dict:
    """
    Deep merge that returns a new dict.
    """
    result = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _get_lock_for_path(path: str) -> threading.Lock:
    normalized = os.path.abspath(path)
    with _PREFERENCE_LOCKS_GUARD:
        lock = _PREFERENCE_LOCKS.get(normalized)
        if lock is None:
            lock = threading.Lock()
            _PREFERENCE_LOCKS[normalized] = lock
        return lock


def load_preferences(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def load_merged_preferences(
    *,
    init_path: str = data_path.PATH_DATA_DEFAULT_PREFERENCE,
    path: str = data_path.PATH_DATA_PREFERENCE,
) -> dict:
    default_prefs = load_preferences(init_path) or {}
    user_prefs = load_preferences(path) or {}
    return deep_merge(default_prefs, user_prefs)


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
        return load_preferences(path)

    def _pref_init(self):
        merged = load_merged_preferences(init_path=self.init_path, path=self.path)
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
        Set a nested config item like set_nested("ui.theme", "light").
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

        path_lock = _get_lock_for_path(self.path)
        with path_lock:
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
