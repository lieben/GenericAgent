#!/usr/bin/env python3
"""
Agent Dream Review - 情感线做梦系统

每个 agent 的私有做梦系统，专注于情感对话和故事线复盘。
收集主线聊天、日记随笔、关于言言的事实记忆。
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
AGENT_BASE = ROOT / 'memory' / 'agents'
LEGACY_AGENT_BASE = Path(os.environ.get('AGENT_DREAM_LEGACY_BASE', '')) if os.environ.get('AGENT_DREAM_LEGACY_BASE') else None


def get_agent_paths(agent: str) -> dict[str, Path]:
    """获取 agent 的私有路径。

    以当前 GA 实例目录为权威根目录，便于复制模板后只改实例目录即可运行。
    可通过 AGENT_DREAM_LEGACY_BASE 指定旧外部归档作迁移期可选读取来源。
    """
    base = AGENT_BASE / agent
    legacy_base = (LEGACY_AGENT_BASE / agent) if LEGACY_AGENT_BASE else None
    return {
        'base': base,
        'dream_dir': base / '梦境复盘' / 'dreams',
        'index': base / '梦境复盘' / 'index.json',
        'latest_context': base / '梦境复盘' / 'latest_context.md',
        'mainline': base / 'L4_sessions' / 'wechat' / 'mainline',
        'legacy_mainline': (legacy_base / '主线聊天记录') if legacy_base else base / '__missing_legacy_mainline__',
        'journal': base / '日记',
        'legacy_journal': (legacy_base / '日记') if legacy_base else base / '__missing_legacy_journal__',
        'essay': base / '随笔',
        'legacy_essay': (legacy_base / '随笔') if legacy_base else base / '__missing_legacy_essay__',
        'l4_sessions': base / 'L4_sessions',
        'legacy_base': legacy_base if legacy_base else base / '__missing_legacy__',
    }


def ensure_dirs(paths: dict[str, Path]) -> None:
    """确保目录存在"""
    paths['dream_dir'].mkdir(parents=True, exist_ok=True)
    paths['dream_dir'].parent.mkdir(parents=True, exist_ok=True)


def recent_files(paths: Iterable[Path], since: datetime, limit: int = 80) -> list[Path]:
    """获取最近修改的文件"""
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
    """读取文件尾部"""
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
    except Exception as exc:
        return f'[read failed: {exc}]'
    text = text.strip()
    if len(text) > max_chars:
        return text[-max_chars:]
    return text


def mainline_archive_day(dt: datetime | None = None) -> str:
    """获取主线日期（08:00 为分界）"""
    dt = dt or datetime.now()
    if dt.hour < 8:
        dt = dt - timedelta(days=1)
    return dt.strftime('%Y-%m-%d')


def collect_materials(agent: str, hours: int = 24) -> str:
    """收集情感线做梦素材"""
    paths = get_agent_paths(agent)
    ensure_dirs(paths)
    
    now = datetime.now()
    since = now - timedelta(hours=hours)
    parts: list[str] = []
    
    parts.append(f'# {agent} 情感线梦境复盘素材 · {now:%Y-%m-%d %H:%M}\n')
    parts.append(f'- Agent: {agent}')
    parts.append(f'- 时间窗口：{since:%Y-%m-%d %H:%M} → {now:%Y-%m-%d %H:%M}')
    parts.append('- 目标：复盘情感对话，增补 agent 眼里的言言，记住必须记住的事。')
    parts.append('- 强制要求：除提取生活习惯、约定外，必须提取一个【重大情节锚点】（记录昨天发生的最重要的一件事，压缩在15字以内，如：[情节锚点] 2026-04-25：一起在下雨天撑伞散步）。')
    parts.append('- 输出：梦境复盘与 L4 记忆；持久生活事实仅经 Memory Palace Guard 写入。\n')
    
    # 1. 主线聊天记录
    parts.append('## 完整主线日文件（源头按08:00→次日08:00落盘）')
    main_day = mainline_archive_day(now)
    main_path = paths['mainline'] / f'{main_day}.md'
    parts.append(f'- 当前主线日：{main_day} 08:00 → {(datetime.strptime(main_day, "%Y-%m-%d") + timedelta(days=1)):%Y-%m-%d} 08:00')
    parts.append(f'- 文件：{main_path}')
    if main_path.exists():
        parts.append('```markdown\n' + read_tail(main_path, 8000) + '\n```')
    else:
        parts.append('（未找到当前完整主线日文件）')
    
    # 2. 日记和随笔
    parts.append('## 日记系统近期产物')
    for p in recent_files([paths['journal'], paths['legacy_journal']], since + timedelta(hours=-48), 15):
        parts.append(f'### {p}')
        parts.append('```markdown\n' + read_tail(p, 2500) + '\n```')
    
    parts.append('## 随笔系统近期产物')
    for p in recent_files([paths['essay']], since + timedelta(hours=-48), 15):
        parts.append(f'### {p}')
        parts.append('```markdown\n' + read_tail(p, 2500) + '\n```')
    
    # 3. 持久生活事实由 Memory Palace 主动召回；旧 L2 仅保留为迁移档案，不在做梦素材中读取。
    parts.append('## 持久生活事实')
    parts.append('（按需使用 Memory Palace search→read；本素材收集器不读取旧 L2 档案。）')
    
    # 4. 最近的 L4 原始会话（如果有）
    parts.append('## 最近的原始会话记录（L4）')
    if paths['l4_sessions'].exists():
        for p in recent_files([paths['l4_sessions']], since, 10):
            parts.append(f'### {p}')
            parts.append('```text\n' + read_tail(p, 1500) + '\n```')
    else:
        parts.append('（未找到 L4 会话记录）')
    
    return '\n\n'.join(parts).strip() + '\n'


def _first_heading(text: str) -> str:
    """提取第一个标题"""
    for line in text.splitlines():
        if line.startswith('#'):
            return line.lstrip('#').strip()[:120]
    return '未命名梦境复盘'


def _extract_section(text: str, heading_pat: str, max_chars: int = 2500) -> str:
    """提取指定章节"""
    pat = re.compile(rf'(^##+\s+.*{heading_pat}.*$)(.*?)(?=^##+\s+|\Z)', re.M | re.S)
    m = pat.search(text)
    if not m:
        return ''
    return (m.group(1) + m.group(2)).strip()[:max_chars]


def save_review(agent: str, markdown: str, date_str: str | None = None) -> Path:
    """保存情感线梦境复盘"""
    paths = get_agent_paths(agent)
    ensure_dirs(paths)
    
    date_str = date_str or datetime.now().strftime('%Y-%m-%d')
    out = paths['dream_dir'] / f'{date_str}.md'
    
    if not markdown.lstrip().startswith('#'):
        markdown = f'# {agent} 情感线梦境复盘 · {date_str}\n\n' + markdown.strip() + '\n'
    
    out.write_text(markdown.strip() + '\n', encoding='utf-8')
    
    # 更新索引
    try:
        index = json.loads(paths['index'].read_text(encoding='utf-8')) if paths['index'].exists() else []
    except Exception:
        index = []
    
    entry = {
        'date': date_str,
        'file': str(out),
        'title': _first_heading(markdown),
        'updated_at': datetime.now().isoformat(timespec='seconds'),
    }
    index = [x for x in index if x.get('date') != date_str] + [entry]
    paths['index'].write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    
    # 生成最新上下文
    context_parts = [
        f'# {agent} 最近情感线梦境复盘上下文 · {date_str}',
        '',
        f'- 来源：{out}',
        '- 用途：供日记/随笔生成前做情感复盘、关于言言的事实一致性校准',
        '',
    ]
    
    wanted = ['关于言言的新认知', '必须记住的事', '情感状态变化', '需要调整的地方', '给日记与随笔系统的提示']
    added: set[str] = set()
    for heading in wanted:
        sec = _extract_section(markdown, heading)
        if sec and sec not in added:
            context_parts.append(sec)
            context_parts.append('')
            added.add(sec)
    
    if len(context_parts) <= 5:
        context_parts.append(markdown[:3500])
    
    paths['latest_context'].write_text('\n'.join(context_parts).strip() + '\n', encoding='utf-8')

    # 生活事实持久化已迁移到 Memory Palace。旧 L2 仅保留为迁移档案，
    # save_review 不得再直接写入或回退到 L2_facts.txt。
    print(out)
    return out


def show_context(agent: str) -> str:
    """显示最新上下文"""
    paths = get_agent_paths(agent)
    ensure_dirs(paths)
    if paths['latest_context'].exists():
        return paths['latest_context'].read_text(encoding='utf-8', errors='replace')
    return f'（{agent} 暂无情感线梦境复盘上下文）'


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Agent Dream Review - 情感线做梦系统')
    ap.add_argument('--agent', required=True, help='Agent 名称（如 xinghui）')
    ap.add_argument('--collect-materials', action='store_true', help='收集素材')
    ap.add_argument('--save-review', help='保存梦境复盘（markdown 内容）')
    ap.add_argument('--date', help='日期（YYYY-MM-DD）')
    ap.add_argument('--show-context', action='store_true', help='显示最新上下文')
    ap.add_argument('--hours', type=int, default=24, help='收集时间窗口（小时）')
    
    ns = ap.parse_args(argv)
    
    if ns.collect_materials:
        print(collect_materials(ns.agent, ns.hours))
        return 0
    
    if ns.save_review is not None:
        save_review(ns.agent, ns.save_review, ns.date)
        return 0
    
    if ns.show_context:
        print(show_context(ns.agent))
        return 0
    
    ap.print_help()
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
