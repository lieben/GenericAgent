"""Recall card 生成与管理"""

from datetime import datetime
from typing import Dict, Any, Optional, List
import json


class RecallCard:
    """召回卡片"""
    
    def __init__(
        self,
        entry_id: str,
        date: str,
        content: str,
        importance: int,
        source_ref: str,
        technical_memory: bool = False,
        tags: Optional[List[str]] = None,
        trigger_keywords: Optional[List[str]] = None
    ):
        self.entry_id = entry_id
        self.date = date
        self.content = content
        self.importance = importance
        self.source_ref = source_ref
        self.technical_memory = technical_memory
        self.tags = tags or []
        self.trigger_keywords = trigger_keywords or []
        self.created_at = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """转为字典"""
        return {
            "entry_id": self.entry_id,
            "date": self.date,
            "content": self.content,
            "importance": self.importance,
            "source_ref": self.source_ref,
            "technical_memory": self.technical_memory,
            "tags": self.tags,
            "trigger_keywords": self.trigger_keywords,
            "created_at": self.created_at
        }


def generate_recall_card(
    entry_id: str,
    date: str,
    diary_content: str,
    tips: List[str],
    importance: int,
    source_uri: str,
    tags: List[str]
) -> RecallCard:
    """
    从日记生成recall card
    
    策略：
    - 提取tips作为触发词
    - 生成简短摘要（不超过200字）
    - 保留source_ref指向完整日记
    """
    # 生成简短摘要
    content_lines = diary_content.split('\n')
    first_para = []
    for line in content_lines:
        line = line.strip()
        if line and not line.startswith('#'):
            first_para.append(line)
            if len(''.join(first_para)) > 150:
                break
    
    summary = ''.join(first_para)[:200]
    if len(diary_content) > 200:
        summary += "..."
    
    # 提取触发词
    trigger_keywords = []
    for tip in tips:
        if '：' in tip:
            trigger_keywords.append(tip.split('：')[0])
        elif ':' in tip:
            trigger_keywords.append(tip.split(':')[0])
    
    trigger_keywords.extend(tags)
    
    # 构建recall内容
    recall_content = f"{date}：{summary}\n\n[source: {source_uri}]"
    
    return RecallCard(
        entry_id=entry_id,
        date=date,
        content=recall_content,
        importance=importance,
        source_ref=source_uri,
        technical_memory=False,
        tags=tags,
        trigger_keywords=trigger_keywords
    )
