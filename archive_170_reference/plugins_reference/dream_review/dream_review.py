#!/usr/bin/env python3
"""
GA Dream Review plugin (tool-only helper).

The scheduled task at 03:30 asks the agent to perform the reflection itself and
uses this helper to collect materials and persist the final markdown.  The helper
never calls an LLM and never notifies the user.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(os.environ.get('GA_DREAM_REVIEW_BASE', ROOT / 'memory' / 'dream_review'))
LEGACY_BASE = Path(os.environ.get('GA_DREAM_REVIEW_LEGACY_BASE', '/home/aimcafe/ds memory/梦境复盘'))
DREAM_DIR = BASE / 'dreams'
INDEX_PATH = BASE / 'index.json'
LATEST_CONTEXT = BASE / 'latest_context.md'
LOG_DIRS = [ROOT / 'logs', ROOT / 'reflect' / 'done', ROOT / 'temp', ROOT / 'sche_tasks' / 'done']
# 主线聊天/日记随笔已移至 agent 私有做梦系统


def ensure_dirs() -> None:
    DREAM_DIR.mkdir(parents=True, exist_ok=True)
    BASE.mkdir(parents=True, exist_ok=True)


def _run(cmd: list[str], timeout: int = 12) -> str:
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return f'[command failed: {cmd!r}: {exc}]'
    out = (p.stdout or '').strip()
    err = (p.stderr or '').strip()
    if err:
        out = (out + '\n' + err).strip()
    return out[:12000]


def mainline_archive_day(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    if dt.hour < 8:
        dt = dt - timedelta(days=1)
    return dt.strftime('%Y-%m-%d')


def recent_files(paths: Iterable[Path], since: datetime, limit: int = 80) -> list[Path]:
    found: list[tuple[float, Path]] = []
    for base in paths:
        if not base.exists():
            continue
        try:
            it = base.rglob('*') if base.is_dir() else [base]
            for p in it:
                if not p.is_file():
                    continue
                try:
                    mt = datetime.fromtimestamp(p.stat().st_mtime)
                except OSError:
                    continue
                if mt >= since and p.suffix.lower() in {'.md', '.txt', '.log', '.json'}:
                    found.append((p.stat().st_mtime, p))
        except Exception:
            continue
    found.sort(reverse=True)
    return [p for _, p in found[:limit]]


def read_tail(p: Path, max_chars: int = 2200) -> str:
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
    except Exception as exc:
        return f'[read failed: {exc}]'
    text = text.strip()
    if len(text) > max_chars:
        return text[-max_chars:]
    return text


def collect_materials(hours: int = 24) -> str:
    ensure_dirs()
    now = datetime.now()
    since = now - timedelta(hours=hours)
    parts: list[str] = []
    parts.append(f'# 全局工作线梦境复盘素材 · {now:%Y-%m-%d %H:%M}\n')
    parts.append(f'- 时间窗口：{since:%Y-%m-%d %H:%M} → {now:%Y-%m-%d %H:%M}')
    parts.append('- 目标：GA 系统工程复盘，不主动通知言言；最终结果只落盘为 md。')
    parts.append('- 范围：Git/日志/飞书对话/L3 SOP/项目书')
    parts.append('- 注意：主线聊天/日记随笔/关于言言的事实记忆由各 agent 私有做梦系统处理\n')
    if LEGACY_BASE.exists() and LEGACY_BASE != BASE:
        parts.append(f'- 迁移兼容：旧全局梦目录仍可参考但不再写入：{LEGACY_BASE}\n')

    parts.append('## Git/仓库快照')
    parts.append('```text\n' + _run(['git', 'status', '--short']) + '\n```')
    parts.append('```text\n' + _run(['git', 'diff', '--name-only']) + '\n```')

    parts.append('## 近期定时/反射报告')
    for p in recent_files([ROOT / 'reflect' / 'done', ROOT / 'sche_tasks' / 'done'], since, 25):
        parts.append(f'### {p}')
        parts.append('```text\n' + read_tail(p, 1800) + '\n```')

    parts.append('## 近期日志/临时产物尾部')
    for p in recent_files([ROOT / 'logs', ROOT / 'temp'], since, 35):
        rel = str(p)
        if any(secret in rel.lower() for secret in ['key', 'secret', 'token', '.env']):
            continue
        parts.append(f'### {p}')
        parts.append('```text\n' + read_tail(p, 1400) + '\n```')

    parts.append('## L3 SOP 和项目书近期变更')
    sop_paths = [ROOT / 'memory' / 'L3_shared', ROOT / 'memory' / 'projects', ROOT / 'memory' / 'agents']
    for p in recent_files(sop_paths, since, 15):
        parts.append(f'### {p}')
        parts.append('```markdown\n' + read_tail(p, 2000) + '\n```')

    return '\n\n'.join(parts).strip() + '\n'


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith('#'):
            return line.lstrip('#').strip()[:120]
    return '未命名梦境复盘'


def _extract_section(text: str, heading_pat: str, max_chars: int = 2500) -> str:
    pat = re.compile(rf'(^##+\s+.*{heading_pat}.*$)(.*?)(?=^##+\s+|\Z)', re.M | re.S)
    m = pat.search(text)
    if not m:
        return ''
    return (m.group(1) + m.group(2)).strip()[:max_chars]


def save_review(markdown: str, date_str: str | None = None) -> Path:
    ensure_dirs()
    date_str = date_str or datetime.now().strftime('%Y-%m-%d')
    out = DREAM_DIR / f'{date_str}.md'
    if not markdown.lstrip().startswith('#'):
        markdown = f'# 梦境复盘 · {date_str}\n\n' + markdown.strip() + '\n'
    out.write_text(markdown.strip() + '\n', encoding='utf-8')

    try:
        index = json.loads(INDEX_PATH.read_text(encoding='utf-8')) if INDEX_PATH.exists() else []
    except Exception:
        index = []
    entry = {
        'date': date_str,
        'file': str(out),
        'title': _first_heading(markdown),
        'updated_at': datetime.now().isoformat(timespec='seconds'),
    }
    index = [x for x in index if x.get('date') != date_str] + [entry]
    INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    context_parts = [
        f'# 最近梦境复盘上下文 · {date_str}',
        '',
        f'- 来源：{out}',
        '- 用途：供日记/随笔生成前做工程复盘、事实一致性与成长点校准；不是给言言的主动通知。',
        '',
    ]
    wanted = ['工程事实与风险', '关于言言的事实一致性', '我需要调整的地方', '给日记与随笔系统的提示', '行动项']
    added: set[str] = set()
    for heading in wanted:
        sec = _extract_section(markdown, heading)
        if sec and sec not in added:
            context_parts.append(sec)
            context_parts.append('')
            added.add(sec)
    if len(context_parts) <= 5:
        context_parts.append(markdown[:3500])
    LATEST_CONTEXT.write_text('\n'.join(context_parts).strip() + '\n', encoding='utf-8')
    print(out)
    return out


def show_context() -> str:
    ensure_dirs()
    if LATEST_CONTEXT.exists():
        return LATEST_CONTEXT.read_text(encoding='utf-8', errors='replace')
    return '（暂无梦境复盘上下文）'


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--collect-materials', action='store_true')
    ap.add_argument('--save-review')
    ap.add_argument('--date')
    ap.add_argument('--show-context', action='store_true')
    ap.add_argument('--hours', type=int, default=24)
    ns = ap.parse_args(argv)
    if ns.collect_materials:
        print(collect_materials(ns.hours))
        return 0
    if ns.save_review is not None:
        save_review(ns.save_review, ns.date)
        return 0
    if ns.show_context:
        print(show_context())
        return 0
    ap.print_help()
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
