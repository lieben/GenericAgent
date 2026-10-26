#!/usr/bin/env python3
"""Probe the live X11 display/auth where WeChat GUI is actually visible.

GNOME on Xwayland rotates the mutter cookie path and may move WeChat between
:0 and :1 after a session restart. A hardcoded DISPLAY/XAUTHORITY in the
systemd unit therefore goes stale: LLM generate still succeeds, then wmctrl
dies with "Authorization required" / "Cannot open display".

WECHAT_DISPLAY / DISPLAY / XAUTHORITY are hints, not locks.
"""
from __future__ import annotations

import glob
import os
import subprocess

_CACHE = None  # (display, xauthority) | None


def xauthority_candidates():
    uid = os.getuid()
    cands = []
    env_xa = os.environ.get('XAUTHORITY')
    if env_xa:
        cands.append(env_xa)
    cands.extend(sorted(glob.glob(f'/run/user/{uid}/.mutter-Xwaylandauth.*')))
    cands.append(f'/run/user/{uid}/gdm/Xauthority')
    cands.append(os.path.expanduser('~/.Xauthority'))
    seen, out = [], []
    for xa in cands:
        if xa and xa not in seen and os.path.exists(xa):
            seen.append(xa)
            out.append(xa)
    return out or [os.path.expanduser('~/.Xauthority')]


def display_candidates():
    seen, out = [], []
    # Prefer the local GNOME/Xwayland screens first. WECHAT_DISPLAY/DISPLAY are
    # hints only; a stale :1 lock is what broke sending after session restart.
    for d in (':0', ':1', os.environ.get('WECHAT_DISPLAY'), os.environ.get('DISPLAY')):
        if d and d != ':10' and d not in seen:
            seen.append(d)
            out.append(d)
    return out or [':0']


def _wmctrl_list(display, xauth, timeout=3):
    env = {**os.environ, 'DISPLAY': display, 'XAUTHORITY': xauth}
    return subprocess.run(
        ['wmctrl', '-lx'],
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def _has_wechat(stdout):
    low = (stdout or '').lower()
    return 'wechat.wechat' in low or '微信' in (stdout or '')


def usable_display(force_refresh=False):
    """Return (DISPLAY, XAUTHORITY) that can enumerate WeChat windows."""
    global _CACHE
    if _CACHE and not force_refresh:
        return _CACHE
    xauths = xauthority_candidates()
    fallback = None
    for display in display_candidates():
        for xauth in xauths:
            try:
                r = _wmctrl_list(display, xauth)
            except Exception:
                continue
            if r.returncode != 0:
                continue
            pair = (display, xauth)
            if _has_wechat(r.stdout):
                _CACHE = pair
                return _CACHE
            if fallback is None:
                fallback = pair
    _CACHE = fallback or (
        os.environ.get('WECHAT_DISPLAY') or os.environ.get('DISPLAY') or ':0',
        xauths[0],
    )
    return _CACHE


def x_env(extra=None):
    display, xauth = usable_display()
    env = {**os.environ, 'DISPLAY': display, 'XAUTHORITY': xauth}
    if extra:
        env.update(extra)
    return env


def invalidate():
    global _CACHE
    _CACHE = None
