"""应用设置与 Windows 启动项管理。"""
import json
import os
import sys
from pathlib import Path

import winreg

from interaction_rhythm.runtime_paths import resolve_data_dir


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "扣舷-数字节律"
LEGACY_RUN_VALUE_NAMES = ("交互节律", "扣舷", "叩舷")
DEFAULT_SETTINGS = {
    "auto_start": False,
    "background_start": False,
    "silent_start": False,
    "theme": "light",
    "accent": "green",
    "language": "zh",
    "intro_seen": False,
    "ai_provider": "deepseek",
    "ai_cloud_enabled": False,
    "ai_base_url": "",
    "ai_model": "",
    "ai_api_key": "",
    "ai_thinking_enabled": False,
    "ai_agent_prompt": "",
    # Optional external page for one-time support/tips. Empty until the owner configures it.
    "support_url": "",
    "hidden_apps": [],
}
BOOL_SETTING_KEYS = (
    "auto_start",
    "background_start",
    "silent_start",
    "intro_seen",
    "ai_cloud_enabled",
    "ai_thinking_enabled",
)
CHOICE_SETTINGS = {
    "theme": {"light", "dark"},
    "accent": {"green", "rose", "indigo", "ink"},
    "language": {"zh", "en"},
    "ai_provider": {"mimo", "deepseek", "openai"},
}
STRING_SETTING_KEYS = ("ai_base_url", "ai_model", "ai_api_key", "support_url")
PROMPT_SETTING_KEYS = ("ai_agent_prompt",)
LIST_SETTING_KEYS = ("hidden_apps",)
MAX_STRING_SETTING_LENGTH = 500
MAX_PROMPT_SETTING_LENGTH = 12000
MAX_LIST_ITEMS = 50
MAX_LIST_ITEM_LENGTH = 120


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _data_dir() -> Path:
    return resolve_data_dir(_app_root())


def _settings_path() -> Path:
    return _data_dir() / "settings.json"


def _load_file_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    path = _settings_path()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key in BOOL_SETTING_KEYS:
                    if key in raw:
                        data[key] = bool(raw[key])
                for key, allowed in CHOICE_SETTINGS.items():
                    value = str(raw.get(key, "")).strip()
                    if value in allowed:
                        data[key] = value
                for key in STRING_SETTING_KEYS:
                    value = str(raw.get(key, "")).strip()
                    if len(value) <= MAX_STRING_SETTING_LENGTH:
                        data[key] = value
                for key in PROMPT_SETTING_KEYS:
                    value = str(raw.get(key, "")).strip()
                    if len(value) <= MAX_PROMPT_SETTING_LENGTH:
                        data[key] = value
                for key in LIST_SETTING_KEYS:
                    raw_list = raw.get(key)
                    if isinstance(raw_list, (list, tuple)):
                        cleaned = []
                        for item in raw_list:
                            value = str(item or "").strip()[:MAX_LIST_ITEM_LENGTH]
                            if value and value not in cleaned:
                                cleaned.append(value)
                            if len(cleaned) >= MAX_LIST_ITEMS:
                                break
                        data[key] = cleaned
        except Exception:
            pass
    return data


def _save_file_settings(data: dict) -> None:
    _settings_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _launcher_command(background: bool = False) -> str:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        args = ["--background"] if background else []
        return " ".join([f'"{exe}"', *args])

    packaged = Path(__file__).parent / "dist" / "current" / "InteractionRhythm" / "InteractionRhythm.exe"
    if packaged.exists():
        args = ["--background"] if background else []
        return " ".join([f'"{packaged}"', *args])

    main_py = Path(__file__).parent / "main.py"
    args = [f'"{main_py}"']
    if background:
        args.append("--background")
    return " ".join([f'"{Path(sys.executable)}"', *args])


def _read_run_value() -> str:
    if os.name != "nt":
        return ""
    names = (RUN_VALUE_NAME, *LEGACY_RUN_VALUE_NAMES)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            for name in names:
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                    if value:
                        return str(value)
                except FileNotFoundError:
                    continue
                except OSError:
                    continue
    except FileNotFoundError:
        pass
    except OSError:
        pass
    return ""


def _write_run_value(command: str) -> None:
    if os.name != "nt":
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        for legacy_name in LEGACY_RUN_VALUE_NAMES:
            try:
                winreg.DeleteValue(key, legacy_name)
            except OSError:
                pass
        winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, command)


def _delete_run_value() -> None:
    if os.name != "nt":
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            for name in (RUN_VALUE_NAME, *LEGACY_RUN_VALUE_NAMES):
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    pass
    except FileNotFoundError:
        pass
    except OSError:
        pass


def get_settings() -> dict:
    data = _load_file_settings()
    data["silent_start"] = bool(data.get("silent_start") or data.get("background_start"))
    data["background_start"] = data["silent_start"]
    run_command = _read_run_value()
    if run_command:
        data["auto_start"] = True
        silent = "--background" in run_command or "--hidden" in run_command or "--tray" in run_command
        data["background_start"] = silent
        data["silent_start"] = silent
    return data


def save_settings(patch: dict) -> dict:
    data = get_settings()
    if isinstance(patch, dict):
        for key in BOOL_SETTING_KEYS:
            if key in patch:
                data[key] = bool(patch[key])
        for key, allowed in CHOICE_SETTINGS.items():
            value = str(patch.get(key, "")).strip()
            if value in allowed:
                data[key] = value
        for key in STRING_SETTING_KEYS:
            if key in patch:
                value = str(patch.get(key, "")).strip()
                if len(value) <= MAX_STRING_SETTING_LENGTH:
                    data[key] = value
        for key in PROMPT_SETTING_KEYS:
            if key in patch:
                value = str(patch.get(key, "")).strip()
                if len(value) <= MAX_PROMPT_SETTING_LENGTH:
                    data[key] = value
        for key in LIST_SETTING_KEYS:
            if key in patch:
                raw_list = patch.get(key)
                if not isinstance(raw_list, (list, tuple)):
                    raw_list = [raw_list] if raw_list else []
                cleaned = []
                for item in raw_list:
                    value = str(item or "").strip()[:MAX_LIST_ITEM_LENGTH]
                    if value and value not in cleaned:
                        cleaned.append(value)
                    if len(cleaned) >= MAX_LIST_ITEMS:
                        break
                data[key] = cleaned
        if "silent_start" in patch:
            data["background_start"] = bool(patch["silent_start"])
        elif "background_start" in patch:
            data["silent_start"] = bool(patch["background_start"])

    _save_file_settings(data)
    if data["auto_start"]:
        _write_run_value(_launcher_command(data["silent_start"]))
    else:
        _delete_run_value()
    return get_settings()
