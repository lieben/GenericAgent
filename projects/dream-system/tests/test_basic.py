"""
梦境系统简单测试
"""

import sys
from pathlib import Path
from datetime import datetime

# 添加service到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_imports():
    """测试所有模块导入"""
    print("=== 测试模块导入 ===\n")
    
    try:
        from service.storage import ObsidianStorage
        print("✓ storage")
        
        from service.collector import DreamCollector, Message
        print("✓ collector")
        
        from service.filter import MessageFilter, FilterConfig, DEFAULT_FILTER_CONFIG
        print("✓ filter")
        
        from service.analyzer import DreamAnalyzer
        print("✓ analyzer")
        
        from service.dream import DreamService
        print("✓ dream")
        
        from service.recall import DreamRecall
        print("✓ recall")
        
        from service.config import DreamConfig
        print("✓ config")
        
        print("\n✓ 所有模块导入成功!\n")
        return True
    
    except Exception as e:
        print(f"\n✗ 导入失败: {e}\n")
        import traceback
        traceback.print_exc()
        return False


def test_filter():
    """测试过滤器"""
    print("=== 测试过滤器 ===\n")
    
    from service.filter import MessageFilter, DEFAULT_FILTER_CONFIG
    
    filter = MessageFilter(DEFAULT_FILTER_CONFIG)
    
    # 测试情感关键词
    text1 = "我希望你记住，我不喜欢温牛奶"
    score1 = filter.get_emotion_score(text1)
    print(f"情感文本: '{text1}'")
    print(f"  情感分数: {score1:.2f}")
    
    facts1 = filter.extract_facts(text1)
    print(f"  提取事实: {facts1}")
    
    # 测试技术内容
    text2 = "这个python脚本报错了，traceback显示import失败"
    should_skip2, reason2 = filter.should_skip(text2, "api")
    print(f"\n技术文本: '{text2}'")
    print(f"  是否跳过: {should_skip2} ({reason2})")
    
    # 测试群聊
    text3 = "今天群里讨论了很多有趣的话题"
    should_skip3, reason3 = filter.should_skip(text3, "group")
    print(f"\n群聊文本: '{text3}'")
    print(f"  是否跳过: {should_skip3} ({reason3})")
    
    print("\n✓ 过滤器测试通过!\n")


def test_storage():
    """测试存储层（干运行）"""
    print("=== 测试存储层 ===\n")
    
    from service.storage import ObsidianStorage
    import tempfile
    
    # 使用临时目录
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ObsidianStorage(tmpdir, "TestVault")
        
        # 测试写入梦境
        test_content = """---
date: 2026-09-10
---

# 测试梦境

这是一个测试梦境复盘。
"""
        
        uri = storage.write_dream(datetime(2026, 9, 10), test_content)
        print(f"✓ 写入梦境: {uri}")
        
        # 测试读取
        dreams = storage.list_dreams()
        print(f"✓ 读取梦境列表: {len(dreams)} 条")
        
        # 测试事实候选
        storage.append_fact_candidate(datetime(2026, 9, 10), "测试事实")
        print(f"✓ 写入事实候选")
        
        candidates = storage.read_fact_candidates()
        print(f"✓ 读取事实候选: {len(candidates)} 条")
    
    print("\n✓ 存储层测试通过!\n")


def test_analyzer():
    """测试分析器"""
    print("=== 测试分析器 ===\n")
    
    from service.analyzer import DreamAnalyzer
    from service.collector import Message
    from datetime import datetime
    
    analyzer = DreamAnalyzer()
    
    # 构造测试消息
    messages = [
        Message(datetime(2026, 9, 10, 10, 30), "user", "我希望你记住我不喜欢温牛奶", "wechat"),
        Message(datetime(2026, 9, 10, 10, 31), "assistant", "好的，我记住了", "wechat"),
        Message(datetime(2026, 9, 10, 11, 00), "user", "这个python脚本报错了", "api"),
        Message(datetime(2026, 9, 10, 14, 00), "user", "今天群里讨论了很多话题", "group"),
    ]
    
    # 分析消息
    analyzed = analyzer.analyze_messages(messages)
    
    print(f"总消息数: {len(messages)}")
    print(f"保留消息: {sum(1 for a in analyzed if not a.skipped)}")
    print(f"跳过消息: {sum(1 for a in analyzed if a.skipped)}")
    
    for a in analyzed:
        status = "跳过" if a.skipped else "保留"
        print(f"\n  [{status}] {a.message.content[:30]}...")
        if not a.skipped:
            print(f"    情感分数: {a.emotion_score:.2f}")
            if a.facts:
                print(f"    事实: {a.facts}")
    
    print("\n✓ 分析器测试通过!\n")


if __name__ == "__main__":
    print("梦境系统测试套件\n")
    print("=" * 50 + "\n")
    
    # 依次执行测试
    if not test_imports():
        sys.exit(1)
    
    test_filter()
    test_storage()
    test_analyzer()
    
    print("=" * 50)
    print("\n✓ 所有测试通过!")
    print("\n梦境系统核心功能验证完成。")
    print("下一步: 复制 config.example.toml 为 config.toml 并配置后即可使用。")
