"""Prompt template loader for custom WeChat/local frontend plugins.

Templates are intentionally externalized so redistributed plugins do not need to
ship private persona/user wording.  Resolution order:
1. config/plugin_prompts.json
2. config/plugin_prompts.example.json
3. caller supplied default string
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

_PLUGIN_DIR = Path(__file__).resolve().parent
_ROOT = _PLUGIN_DIR.parents[1]
_CONFIG_PATHS = [
    _ROOT / 'config' / 'plugin_prompts.json',
    _PLUGIN_DIR / 'config' / 'plugin_prompts.json',
    _ROOT / 'config' / 'plugin_prompts.example.json',
    _PLUGIN_DIR / 'config' / 'plugin_prompts.example.json',
]
_CACHE: Dict[str, Any] | None = None


def _load() -> Dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    data: Dict[str, Any] = {}
    for path in _CONFIG_PATHS:
        try:
            if path.exists():
                obj = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(obj, dict):
                    data = obj
                    break
        except Exception:
            continue
    _CACHE = data
    return data


def get_prompt(key: str, default: str = '') -> str:
    cur: Any = _load()
    for part in str(key or '').split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur if isinstance(cur, str) else default


def format_prompt(key: str, default: str = '', **values: Any) -> str:
    template = get_prompt(key, default)

    def repl(m: re.Match[str]) -> str:
        name = m.group(1).strip()
        return str(values.get(name, ''))

    return re.sub(r'\{\{\s*([A-Za-z0-9_\.\-]+)\s*\}\}', repl, template)


def get_prompt_config(path: str, default: Any = None) -> Any:
    """Read arbitrary value from plugin prompt config by dotted path."""
    cur: Any = _load()
    for part in str(path or '').split('.'):
        if not part:
            continue
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def get_agent_display_name(default: str = 'assistant') -> str:
    v = get_prompt_config('identity.agent_display_name', default)
    return str(v or default)


def get_user_display_name(default: str = 'user') -> str:
    v = get_prompt_config('identity.user_display_name', default)
    return str(v or default)
