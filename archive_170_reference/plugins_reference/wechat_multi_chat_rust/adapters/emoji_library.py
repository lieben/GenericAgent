#!/usr/bin/env python3
"""Agent emoji library inspector and selector.

Directory convention:
  data/agents/<agent_id>/emojis/<category>/<files>
Supported files: png/jpg/jpeg/gif/webp
"""
import argparse
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = ROOT / 'data' / 'agents'
SUPPORTED_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}

ALIASES = {
    'goodmorning': 'goodmoring',  # keep compatibility with current folder typo
    'morning': 'goodmoring',
    'night': 'goodnight',
    'cute': 'actingcute',
    '撒娇': 'actingcute',
    '开心': 'happy',
    '高兴': 'happy',
    '笑': 'happy',
    '生气': 'angry',
    '安慰': 'comfort',
    '困惑': 'confused',
    '吃瓜': 'gossip',
    '抱抱': 'hug',
    '饿': 'hungry',
    '想念': 'missing',
    '伤心': 'sad',
    '害羞': 'shy',
    '惊讶': 'surprised',
    '感谢': 'thanks',
    '谢谢': 'thanks',
    '累': 'tired',
    '等待': 'wait',
    '担心': 'worry',
}

def agent_dir(agent_id: str) -> Path:
    return DATA_ROOT / agent_id / 'emojis'

def norm(s: str) -> str:
    return re.sub(r'\s+', '', (s or '').strip().lower())

def list_categories(agent_id: str):
    root = agent_dir(agent_id)
    if not root.exists():
        return []
    out = []
    for d in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name):
        files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
        if files:
            out.append({'category': d.name, 'count': len(files), 'sample': files[0].name})
    return out

def resolve_category(agent_id: str, category: str) -> str:
    root = agent_dir(agent_id)
    cats = {p.name: p.name for p in root.iterdir() if p.is_dir()} if root.exists() else {}
    raw = (category or '').strip()
    if raw in cats:
        return raw
    n = norm(raw)
    alias = ALIASES.get(n) or ALIASES.get(raw)
    if alias and alias in cats:
        return alias
    for c in cats:
        if norm(c) == n:
            return c
    for c in cats:
        if n and (n in norm(c) or norm(c) in n):
            return c
    raise FileNotFoundError(f'emoji category not found: agent={agent_id} category={category!r} available={sorted(cats)[:80]}')

def select(agent_id: str, category: str, keyword: str = '', seed: str = '') -> Path:
    cat = resolve_category(agent_id, category)
    d = agent_dir(agent_id) / cat
    files = sorted([p for p in d.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS], key=lambda p: p.name)
    if keyword:
        k = norm(keyword)
        filt = [p for p in files if k in norm(p.stem)]
        if filt:
            files = filt
    if not files:
        raise FileNotFoundError(f'emoji files empty: {d}')
    rng = random.Random(seed or None)
    return rng.choice(files).resolve()

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('list'); a.add_argument('--agent', required=True)
    s = sub.add_parser('select'); s.add_argument('--agent', required=True); s.add_argument('--category', required=True); s.add_argument('--keyword', default=''); s.add_argument('--seed', default='')
    args = ap.parse_args()
    try:
        if args.cmd == 'list':
            print(json.dumps({'ok': True, 'agent': args.agent, 'root': str(agent_dir(args.agent)), 'categories': list_categories(args.agent)}, ensure_ascii=False, indent=2))
        else:
            p = select(args.agent, args.category, args.keyword, args.seed)
            print(json.dumps({'ok': True, 'path': str(p), 'category': args.category, 'keyword': args.keyword}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({'ok': False, 'error': repr(e)}, ensure_ascii=False))
        raise SystemExit(1)

if __name__ == '__main__':
    main()
