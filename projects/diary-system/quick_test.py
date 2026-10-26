#!/usr/bin/env python3
"""
快速测试脚本 - 无需安装tomli
"""

import sys
from pathlib import Path
from datetime import datetime
import tempfile

# 添加路径
sys.path.insert(0, str(Path(__file__).parent))

from service.storage import ObsidianStorage
from service.diary import DiaryEntry, DiaryService
from service.fragment import Fragment, FragmentService
from service.recall import generate_recall_card

def test_write_diary():
    """测试写入日记（使用临时目录，不污染项目）"""
    storage = ObsidianStorage(vault_dir=tempfile.mkdtemp(prefix="diary-test-"), diary_dir="Diary")
    service = DiaryService(storage)
    
    entry = DiaryEntry(
        date=datetime.now(),
        content="今天是安静的一天。",
        tips=["情感锚点：平静"],
        tags=["日常"],
        importance=3
    )
    
    result = service.write_diary(entry)
    if result.get('success'):
        print(f"✅ 写入: {result.get('uri', result.get('relative_path'))}")
    else:
        print(f"❌ 写入失败: {result.get('message')}")
    return result

def test_add_fragment():
    """测试添加碎片"""
    storage = ObsidianStorage(vault_dir=tempfile.mkdtemp(prefix="diary-test-"), fragments_dir="Diary/Fragments")
    service = FragmentService(storage)
    
    fragment = Fragment(
        title="测试碎片",
        content="这是一个测试碎片。",
        tags=["测试"]
    )
    
    result = service.add_fragment(fragment)
    if result.get('success'):
        print(f"✅ 碎片: {result.get('uri', result.get('path'))}")
    else:
        print(f"❌ 碎片失败: {result.get('message')}")
    return result

def test_read_diary():
    """测试读取日记"""
    storage = ObsidianStorage(vault_dir=tempfile.mkdtemp(prefix="diary-test-"), diary_dir="Diary")
    service = DiaryService(storage)
    
    result = service.read_diary(datetime.now())
    if result:
        print(f"✅ 读取成功: {result['metadata']['date']}")
        print(f"   内容: {result['content'][:50]}...")
    else:
        print("❌ 未找到今天的日记")

if __name__ == '__main__':
    print("=== Diary System 快速测试 ===\n")
    
    print("1. 写入日记")
    test_write_diary()
    
    print("\n2. 添加碎片")
    test_add_fragment()
    
    print("\n3. 读取日记")
    test_read_diary()
    
    print("\n✅ 测试完成")
