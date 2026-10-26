"""Shared configurable frontend mode prompts/routes for local frontends.

This module intentionally keeps Python defaults equal to the historical hardcoded
strings.  Operators can override them in config/frontend_mode_config.json without
editing entrypoint code.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Mapping

_CONFIG_PATH = Path(__file__).resolve().parent / 'config' / 'frontend_mode_config.json'

DEFAULT_FRONTEND_MODE_CONFIG: Dict[str, Any] = {
    'wechat': {
        # Outer wrapper only. The configurable WeChat runtime prompt lives in
        # plugin_prompts.json: runtime.wechat_entry_prompt.
        'entry_prompt': '{{prompt}}',
        'persona_anchor_header': (
            '[主线人设锚点｜稳定注入，不要外泄为系统说明]\n'
            '以下用于避免微信入口断片：必须记得otto、游戏内明面作息、基础身份与三环境边界。\n'
            '严禁编造人设里没有的事实。如果人设里没有明确记录某个信息（如饮食偏好、生活习惯等），不要自己推测或编造，应该回答‘不确定’或‘没有记录’。\n'
            '{persona_anchor}\n\n'
        ),
        'file_instruction': 'If you need to show files to user, use [FILE:filepath] in your response.\n\n',
    },
    'api': {
        'group_awareness_command_aliases': {
            '/群聊开': 'on',
            '/开启群聊': 'on',
            '/参与群聊': 'on',
            '/group_on': 'on',
            '/群聊关': 'off',
            '/关闭群聊': 'off',
            '/不参与群聊': 'off',
            '/group_off': 'off',
            '/群聊状态': 'status',
            '/group_status': 'status',
        },
        'group_awareness_command_replies': {
            'status_on': '群聊感知/参与：开启。',
            'status_off': '群聊感知/参与：关闭。',
            'on': '已开启群聊感知/参与：API 与 Clawbot 会共享这个旁侧状态。',
            'off': '已关闭群聊感知/参与：之后不会注入群聊旁侧上下文，也不会主动参与群聊。',
        },
    },
}

_CONFIG_CACHE: Dict[str, Any] | None = None
_CONFIG_MTIME: float | None = None


def _deep_merge(base: Dict[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_frontend_mode_config(force: bool = False) -> Dict[str, Any]:
    """Load config with defaults and hot-reload on mtime change."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    try:
        mtime = _CONFIG_PATH.stat().st_mtime if _CONFIG_PATH.exists() else None
    except Exception:
        mtime = None
    if not force and _CONFIG_CACHE is not None and mtime == _CONFIG_MTIME:
        return copy.deepcopy(_CONFIG_CACHE)
    cfg = copy.deepcopy(DEFAULT_FRONTEND_MODE_CONFIG)
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text('utf-8'))
            if isinstance(data, Mapping):
                _deep_merge(cfg, data)
        except Exception as e:
            print(f'[FrontendModeConfig] load failed, using defaults: {_CONFIG_PATH}: {e}', file=sys.stderr)
    _CONFIG_CACHE = cfg
    _CONFIG_MTIME = mtime
    return copy.deepcopy(cfg)


def get_wechat_entry_prompt(**kwargs: Any) -> str:
    """Apply the outer WeChat prompt wrapper from config.

    Local frontend configs use {{name}} placeholders so custom prompts can
    contain literal braces/commands such as 【.xxx.】 without being parsed as
    Python format syntax.  A small {prompt}-style compatibility fallback is kept
    for older redistributed configs.
    """
    wechat_cfg = load_frontend_mode_config().get('wechat', {})
    template = wechat_cfg.get('entry_prompt')
    if not template:
        template = '{{prompt}}'
    text = str(template)
    try:
        import re

        def repl(m):
            key = m.group(1).strip()
            return str(kwargs.get(key, ''))

        rendered = re.sub(r'\{\{\s*([A-Za-z0-9_\.\-]+)\s*\}\}', repl, text)
        if rendered == text and '{' in text and '}' in text:
            rendered = text.format(**kwargs)
        return rendered
    except Exception as e:
        print(f'[FrontendModeConfig] entry prompt format failed: {e}', file=sys.stderr)
        return text + str(kwargs.get('prompt', ''))


def get_wechat_file_instruction() -> str:
    return str(load_frontend_mode_config().get('wechat', {}).get('file_instruction', ''))


def get_wechat_persona_anchor_block(persona_anchor: str) -> str:
    template = load_frontend_mode_config().get('wechat', {}).get('persona_anchor_header', '')
    try:
        return str(template).format(persona_anchor=persona_anchor)
    except Exception as e:
        print(f'[FrontendModeConfig] persona anchor format failed: {e}', file=sys.stderr)
        return str(template) + str(persona_anchor) + '\n\n'

