#!/usr/bin/env python3
"""
梦境系统命令行工具
"""

import sys
import argparse
from datetime import datetime
from pathlib import Path

# 添加service到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from service.config import DreamConfig
from service.storage import ObsidianStorage
from service.collector import DreamCollector
from service.dream import DreamService
from service.recall import DreamRecall
from service.filter import FilterConfig


def cmd_review(args):
    """执行每日复盘"""
    # 加载配置
    config = DreamConfig(args.config)
    
    # 初始化组件
    storage = ObsidianStorage(config.vault_dir, config.vault_name)
    collector = DreamCollector(
        l4_dir=config.l4_sessions_dir,
        diary_dir=config.diary_dir if config.include_diary else None
    )
    
    # 构建过滤配置
    filter_config = FilterConfig(
        emotion_keywords=config.emotion_keywords,
        fact_keywords=config.fact_keywords,
        tech_keywords=config.tech_keywords,
        group_keywords=config.group_keywords,
        sensitive_keywords=config.sensitive_keywords
    )
    
    # 创建服务
    service = DreamService(storage, collector, filter_config)
    
    # 解析日期
    date = None
    if args.date:
        date = datetime.strptime(args.date, "%Y-%m-%d")
    
    # 执行复盘
    try:
        result = service.review_day(date, save_context=not args.no_context)
        
        print(f"✓ 复盘完成: {result.date.date()}")
        print(f"  URI: {result.uri}")
        print(f"  总消息: {result.analysis.total_messages}")
        print(f"  保留: {result.analysis.kept_messages}")
        print(f"  平均情感分数: {result.analysis.avg_emotion_score:.2f}")
        print(f"  提取事实: {result.facts_count} 条")
        
        if args.open:
            import webbrowser
            webbrowser.open(result.uri)
        
        return 0
    
    except Exception as e:
        print(f"✗ 复盘失败: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


def cmd_recall(args):
    """召回查询"""
    # 加载配置
    config = DreamConfig(args.config)
    
    # 初始化组件
    storage = ObsidianStorage(config.vault_dir, config.vault_name)
    recall = DreamRecall(storage)
    
    # 执行查询
    if args.keyword:
        results = recall.search_by_keyword(
            args.keyword,
            days=args.days,
            limit=args.limit
        )
        
        if not results:
            print(f"未找到包含'{args.keyword}'的梦境复盘")
            return 0
        
        print(f"找到 {len(results)} 条相关复盘:\n")
        for i, result in enumerate(results, 1):
            print(f"{i}. {result.date.date()} (相关性: {result.relevance_score:.2f})")
            print(f"   {result.content_snippet}")
            print(f"   {result.dream_uri}\n")
    
    elif args.facts:
        facts = recall.search_facts(args.facts)
        
        if not facts:
            print(f"未找到包含'{args.facts}'的事实")
            return 0
        
        print(f"找到 {len(facts)} 条相关事实:\n")
        for fact in facts:
            print(f"  • {fact}")
    
    else:
        # 显示最近的上下文
        context = recall.get_context(days=args.days or 7)
        print(f"最近 {args.days or 7} 天的情感上下文:\n")
        print(context)
    
    return 0


def cmd_list(args):
    """列出所有梦境复盘"""
    # 加载配置
    config = DreamConfig(args.config)
    storage = ObsidianStorage(config.vault_dir, config.vault_name)
    
    # 获取所有梦境
    dreams = storage.list_dreams(
        start_date=datetime.strptime(args.start, "%Y-%m-%d") if args.start else None,
        end_date=datetime.strptime(args.end, "%Y-%m-%d") if args.end else None
    )
    
    if not dreams:
        print("未找到任何梦境复盘")
        return 0
    
    print(f"共找到 {len(dreams)} 条复盘记录:\n")
    for dream in dreams:
        print(f"  {dream['date'].date()}")
        if args.verbose:
            print(f"    {dream['uri']}")
    
    return 0


def cmd_init(args):
    """初始化配置文件"""
    config_path = Path(args.config or "config.toml")
    example_path = Path(__file__).parent.parent / "config.example.toml"
    
    if config_path.exists() and not args.force:
        print(f"配置文件已存在: {config_path}")
        print("使用 --force 强制覆盖")
        return 1
    
    import shutil
    shutil.copy(example_path, config_path)
    print(f"✓ 配置文件已创建: {config_path}")
    print("\n请编辑配置文件，设置:")
    print("  - Obsidian Vault路径")
    print("  - L4会话目录")
    print("  - 日记系统目录（可选）")
    print("  - 其他个性化配置")
    
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="梦境系统 - 情感复盘与关系事实提取",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "--config", "-c",
        default="config.toml",
        help="配置文件路径 (默认: config.toml)"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细输出"
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # init命令
    p_init = subparsers.add_parser("init", help="初始化配置文件")
    p_init.add_argument("--force", action="store_true", help="强制覆盖已存在的配置")
    p_init.set_defaults(func=cmd_init)
    
    # review命令
    p_review = subparsers.add_parser("review", help="执行每日复盘")
    p_review.add_argument("--date", "-d", help="指定日期 (YYYY-MM-DD)")
    p_review.add_argument("--no-context", action="store_true", help="不更新latest_context")
    p_review.add_argument("--open", "-o", action="store_true", help="完成后打开Obsidian")
    p_review.set_defaults(func=cmd_review)
    
    # recall命令
    p_recall = subparsers.add_parser("recall", help="召回查询")
    p_recall.add_argument("--keyword", "-k", help="搜索关键词")
    p_recall.add_argument("--facts", "-f", help="搜索事实")
    p_recall.add_argument("--days", "-d", type=int, help="搜索范围（天数）")
    p_recall.add_argument("--limit", "-l", type=int, default=10, help="最大结果数")
    p_recall.set_defaults(func=cmd_recall)
    
    # list命令
    p_list = subparsers.add_parser("list", help="列出所有复盘记录")
    p_list.add_argument("--start", help="开始日期 (YYYY-MM-DD)")
    p_list.add_argument("--end", help="结束日期 (YYYY-MM-DD)")
    p_list.set_defaults(func=cmd_list)
    
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
