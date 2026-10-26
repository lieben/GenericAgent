"""
情感分析器 - 核心分析逻辑
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .collector import Message
from .filter import MessageFilter, FilterConfig, DEFAULT_FILTER_CONFIG


@dataclass
class AnalyzedMessage:
    """分析后的消息"""
    message: Message
    emotion_score: float  # 情感权重分数
    skipped: bool  # 是否被跳过
    skip_reason: str  # 跳过原因
    facts: list[str]  # 提取的事实候选
    has_sensitive: bool  # 是否包含敏感信息
    

@dataclass
class DayAnalysis:
    """一天的分析结果"""
    date: datetime
    total_messages: int
    kept_messages: int  # 保留的消息数
    skipped_messages: int  # 跳过的消息数
    avg_emotion_score: float  # 平均情感分数
    extracted_facts: list[str]  # 提取的所有事实
    sensitive_count: int  # 敏感信息条数
    

class DreamAnalyzer:
    """梦境分析器"""
    
    def __init__(self, filter_config: FilterConfig = DEFAULT_FILTER_CONFIG):
        self.filter = MessageFilter(filter_config)
    
    def analyze_messages(self, messages: list[Message]) -> list[AnalyzedMessage]:
        """
        分析一批消息
        
        Returns:
            分析后的消息列表
        """
        analyzed = []
        
        for msg in messages:
            # 1. 判断是否跳过
            should_skip, skip_reason = self.filter.should_skip(msg.content, msg.source)
            
            # 2. 计算情感分数
            emotion_score = 0.0 if should_skip else self.filter.get_emotion_score(msg.content)
            
            # 3. 提取事实候选
            facts = [] if should_skip else self.filter.extract_facts(msg.content)
            
            # 4. 标记敏感信息
            has_sensitive, _ = self.filter.mark_sensitive(msg.content)
            
            analyzed.append(AnalyzedMessage(
                message=msg,
                emotion_score=emotion_score,
                skipped=should_skip,
                skip_reason=skip_reason,
                facts=facts,
                has_sensitive=has_sensitive
            ))
        
        return analyzed
    
    def summarize_day(self, analyzed: list[AnalyzedMessage], date: datetime) -> DayAnalysis:
        """
        生成一天的分析摘要
        
        Args:
            analyzed: 分析后的消息列表
            date: 日期
        """
        kept = [a for a in analyzed if not a.skipped]
        skipped = [a for a in analyzed if a.skipped]
        
        # 计算平均情感分数
        avg_score = 0.0
        if kept:
            avg_score = sum(a.emotion_score for a in kept) / len(kept)
        
        # 收集所有事实
        all_facts = []
        for a in kept:
            all_facts.extend(a.facts)
        
        # 统计敏感信息
        sensitive_count = sum(1 for a in analyzed if a.has_sensitive)
        
        return DayAnalysis(
            date=date,
            total_messages=len(analyzed),
            kept_messages=len(kept),
            skipped_messages=len(skipped),
            avg_emotion_score=avg_score,
            extracted_facts=all_facts,
            sensitive_count=sensitive_count
        )
    
    def build_context(self, analyzed: list[AnalyzedMessage], max_chars: int = 4000) -> str:
        """
        构建情感上下文（用于LLM输入）
        
        只保留高情感分数的消息，按时间排序
        """
        # 过滤掉跳过的消息，按情感分数排序
        kept = [a for a in analyzed if not a.skipped and a.emotion_score > 0]
        kept.sort(key=lambda a: a.emotion_score, reverse=True)
        
        # 构建上下文
        context_parts = []
        total_chars = 0
        
        for a in kept:
            msg = a.message
            msg_text = f"[{msg.timestamp.strftime('%H:%M')}] {msg.role}: {msg.content}\n"
            
            if total_chars + len(msg_text) > max_chars:
                break
            
            context_parts.append(msg_text)
            total_chars += len(msg_text)
        
        # 按时间正序返回
        context_parts.sort(key=lambda x: x)
        return '\n'.join(context_parts)
