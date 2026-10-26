#!/usr/bin/env python3
"""
Diary System CLI

使用方法：
  python diary_cli.py write --content "今天..." --tips "情感锚点：..."
  python diary_cli.py add-fragment --title "..." --content "..."
  python diary_cli.py read --date 2026-09-10
  python diary_cli.py search "关键词"
  python diary_cli.py recent --days 7
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
import tomli

# 添加service路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from service.storage import ObsidianStorage
from service.diary import DiaryEntry, DiaryService
from service.fragment import Fragment, FragmentService
from service.recall import generate_recall_card
from service.sync import SyncManager
from service.search import SimpleSearch


def load_config(config_path: str = "config.toml") -> dict:
    """加载配置文件"""
    config_file = Path(config_path)
    if not config_file.exists():
        print(f"❌ 配置文件不存在: {config_path}")
        print("请复制 config.example.toml 为 config.toml 并配置")
        sys.exit(1)
    
    with open(config_file, 'rb') as f:
        return tomli.load(f)


def cmd_write(args, config):
    """写入日记"""
    storage = ObsidianStorage(
        vault_dir=config['storage']['vault_dir'],
        diary_dir=config['storage']['diary_dir']
    )
    service = DiaryService(storage)
    
    # 解析日期
    if args.date:
        date = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        date = datetime.now()
    
    # 解析tips
    tips = []
    if args.tips:
        if isinstance(args.tips, list):
            tips = args.tips
        else:
            tips = [t.strip() for t in args.tips.split(',') if t.strip()]
    
    # 解析tags
    tags = []
    if args.tags:
        if isinstance(args.tags, list):
            tags = args.tags
        else:
            tags = [t.strip() for t in args.tags.split(',') if t.strip()]
    
    # 创建日记条目
    entry = DiaryEntry(
        date=date,
        content=args.content,
        tips=tips,
        tags=tags,
        importance=args.importance,
        technical_memory=args.technical,
        persona_name=config['agent'].get('persona_name'),
        user_name=config['agent'].get('user_name'),
        mood=args.mood
    )
    
    # 写入
    result = service.write_diary(entry, overwrite=args.overwrite)
    
    if not result['success']:
        print(f"❌ 写入失败: {result.get('message', result.get('error'))}")
        sys.exit(1)
    
    print(f"✅ 日记已写入")
    print(f"   日期: {result['date']}")
    print(f"   路径: {result['relative_path']}")
    print(f"   URI: {result['uri']}")
    
    # 生成并同步recall card（如果配置启用）
    if config['diary'].get('auto_generate_recall', True):
        card = generate_recall_card(
            entry_id=entry.entry_id,
            date=result['date'],
            diary_content=entry.content,
            tips=entry.tips,
            importance=entry.importance,
            source_uri=result['uri'],
            tags=entry.tags
        )
        print(f"\n📇 Recall card已生成")
        print(f"   触发词: {', '.join(card.trigger_keywords[:5])}")
        
        # TODO: 同步到MemPalace
        if config['mempalace'].get('enabled', False):
            print("   MemPalace同步: 需要MCP实现")


def cmd_append(args, config):
    """追加内容到日记"""
    storage = ObsidianStorage(
        vault_dir=config['storage']['vault_dir'],
        diary_dir=config['storage']['diary_dir']
    )
    service = DiaryService(storage)
    
    date = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    
    result = service.append_diary(date, args.content, args.section)
    
    if not result['success']:
        print(f"❌ 追加失败: {result.get('message')}")
        sys.exit(1)
    
    print(f"✅ 内容已追加到 {result['date']}")
    print(f"   URI: {result['uri']}")


def cmd_fragment(args, config):
    """添加碎片日记"""
    storage = ObsidianStorage(
        vault_dir=config['storage']['vault_dir'],
        fragments_dir=config['storage']['fragments_dir']
    )
    service = FragmentService(storage)
    
    date = datetime.now()
    tags = [t.strip() for t in args.tags.split(',') if t.strip()] if args.tags else []
    
    fragment = Fragment(
        title=args.title,
        content=args.content,
        date=date,
        tags=tags,
        technical_memory=args.technical
    )
    
    result = service.add_fragment(fragment)
    
    if not result['success']:
        print(f"❌ 写入失败")
        sys.exit(1)
    
    print(f"✅ 碎片已保存")
    print(f"   ID: {result['fragment_id']}")
    print(f"   URI: {result['uri']}")


def cmd_read(args, config):
    """读取日记"""
    storage = ObsidianStorage(
        vault_dir=config['storage']['vault_dir'],
        diary_dir=config['storage']['diary_dir']
    )
    service = DiaryService(storage)
    
    date = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    
    result = service.read_diary(date)
    
    if not result:
        print(f"❌ 未找到 {date.strftime('%Y-%m-%d')} 的日记")
        sys.exit(1)
    
    print(f"\n{'='*60}")
    print(f"日期: {result['metadata']['date']} {result['metadata'].get('weekday', '')}")
    print(f"{'='*60}\n")
    print(result['content'])
    print(f"\n{'='*60}")
    print(f"URI: {result['uri']}")


def cmd_recent(args, config):
    """列出最近日记"""
    storage = ObsidianStorage(
        vault_dir=config['storage']['vault_dir'],
        diary_dir=config['storage']['diary_dir']
    )
    service = DiaryService(storage)
    
    diaries = service.list_recent_diaries(args.days)
    
    if not diaries:
        print("暂无日记")
        return
    
    print(f"\n最近 {args.days} 天的日记：\n")
    for diary in diaries:
        importance = '⭐' * diary['importance']
        tags = ', '.join(diary['tags']) if diary['tags'] else ''
        print(f"  {diary['date']} {diary.get('weekday', '')}  {importance}")
        if tags:
            print(f"    标签: {tags}")
        print(f"    {diary['uri']}\n")


def cmd_search(args, config):
    """搜索日记"""
    storage = ObsidianStorage(
        vault_dir=config['storage']['vault_dir'],
        diary_dir=config['storage']['diary_dir']
    )
    search = SimpleSearch(storage)
    
    results = search.search_diaries(args.query, limit=args.limit)
    
    if not results:
        print(f"未找到包含 '{args.query}' 的日记")
        return
    
    print(f"\n找到 {len(results)} 篇日记：\n")
    for r in results:
        print(f"  {r['date']}  ⭐{r['importance']}")
        print(f"    {r['context']}")
        print(f"    {r['uri']}\n")


def main():
    parser = argparse.ArgumentParser(description="Diary System CLI")
    parser.add_argument('--config', default='config.toml', help='配置文件路径')
    
    subparsers = parser.add_subparsers(dest='command', help='命令')
    
    # write命令
    write_parser = subparsers.add_parser('write', help='写入日记')
    write_parser.add_argument('--content', required=True, help='日记正文')
    write_parser.add_argument('--date', help='日期 (YYYY-MM-DD)，默认今天')
    write_parser.add_argument('--tips', help='Tips，逗号分隔')
    write_parser.add_argument('--tags', help='标签，逗号分隔')
    write_parser.add_argument('--importance', type=int, default=3, help='重要性 (1-5)')
    write_parser.add_argument('--mood', help='心情/状态')
    write_parser.add_argument('--technical', action='store_true', help='技术记忆')
    write_parser.add_argument('--overwrite', action='store_true', help='覆盖已存在的日记')
    
    # append命令
    append_parser = subparsers.add_parser('append', help='追加内容到日记')
    append_parser.add_argument('--content', required=True, help='要追加的内容')
    append_parser.add_argument('--date', help='日期，默认今天')
    append_parser.add_argument('--section', help='章节标题')
    
    # add-fragment命令
    fragment_parser = subparsers.add_parser('add-fragment', help='添加碎片日记')
    fragment_parser.add_argument('--title', required=True, help='标题')
    fragment_parser.add_argument('--content', required=True, help='内容')
    fragment_parser.add_argument('--tags', help='标签，逗号分隔')
    fragment_parser.add_argument('--technical', action='store_true', help='技术记忆')
    
    # read命令
    read_parser = subparsers.add_parser('read', help='读取日记')
    read_parser.add_argument('--date', help='日期，默认今天')
    
    # recent命令
    recent_parser = subparsers.add_parser('recent', help='列出最近日记')
    recent_parser.add_argument('--days', type=int, default=7, help='天数')
    
    # search命令
    search_parser = subparsers.add_parser('search', help='搜索日记')
    search_parser.add_argument('query', help='搜索关键词')
    search_parser.add_argument('--limit', type=int, default=10, help='最多返回结果数')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # 加载配置
    config = load_config(args.config)
    
    # 执行命令
    commands = {
        'write': cmd_write,
        'append': cmd_append,
        'add-fragment': cmd_fragment,
        'read': cmd_read,
        'recent': cmd_recent,
        'search': cmd_search
    }
    
    commands[args.command](args, config)


if __name__ == '__main__':
    main()
