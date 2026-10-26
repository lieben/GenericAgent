"""
梦境系统核心服务类
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from .storage import ObsidianStorage
from .collector import DreamCollector
from .analyzer import DreamAnalyzer, DayAnalysis
from .filter import FilterConfig, DEFAULT_FILTER_CONFIG


@dataclass
class DreamResult:
    """梦境复盘结果"""
    date: datetime
    uri: str  # Obsidian URI
    analysis: DayAnalysis
    facts_count: int
    

class DreamService:
    """梦境系统核心服务"""
    
    def __init__(self,
                 storage: ObsidianStorage,
                 collector: DreamCollector,
                 filter_config: FilterConfig = DEFAULT_FILTER_CONFIG):
        self.storage = storage
        self.collector = collector
        self.analyzer = DreamAnalyzer(filter_config)
    
    def review_day(self, 
                   date: Optional[datetime] = None,
                   save_context: bool = True) -> DreamResult:
        """
        复盘指定日期的情感互动
        
        Args:
            date: 日期，默认为今天
            save_context: 是否同时保存latest_context
        
        Returns:
            DreamResult 包含URI和分析结果
        """
        if date is None:
            date = datetime.now()
        
        # 1. 收集素材
        messages = self.collector.collect_day(date)
        
        if not messages:
            raise ValueError(f"No messages found for {date.date()}")
        
        # 2. 分析消息
        analyzed = self.analyzer.analyze_messages(messages)
        analysis = self.analyzer.summarize_day(analyzed, date)
        
        # 3. 生成复盘内容
        content = self._build_dream_content(date, analysis, analyzed)
        
        # 4. 保存梦境复盘
        uri = self.storage.write_dream(date, content)
        
        # 5. 保存事实候选
        facts_count = 0
        if analysis.extracted_facts:
            for fact in analysis.extracted_facts:
                self.storage.append_fact_candidate(date, fact)
                facts_count += 1
        
        # 6. 更新latest_context（可选）
        if save_context:
            context = self.collector.get_recent_context(days=7, max_chars=4000)
            self.storage.write_context(context)
        
        return DreamResult(
            date=date,
            uri=uri,
            analysis=analysis,
            facts_count=facts_count
        )
    
    def list_recent(self, days: int = 7) -> list:
        """
        列出最近的梦境复盘
        
        Args:
            days: 最近多少天
            
        Returns:
            列表，每项包含 date, preview, uri
        """
        dreams = self.storage.list_recent_dreams(days)
        return [
            {
                "date": dream["date"],
                "preview": dream.get("preview", ""),
                "uri": dream["uri"]
            }
            for dream in dreams
        ]
    
    def _build_dream_content(self, 
                            date: datetime, 
                            analysis: DayAnalysis,
                            analyzed: list) -> str:
        """构建梦境复盘Markdown内容"""
        
        # YAML frontmatter
        frontmatter = f"""---
date: {date.date()}
type: dream_review
total_messages: {analysis.total_messages}
kept_messages: {analysis.kept_messages}
avg_emotion_score: {analysis.avg_emotion_score:.2f}
fact_candidates: {len(analysis.extracted_facts)}
sensitive_count: {analysis.sensitive_count}
---

"""
        
        # 标题
        content = f"# {date.date()} 情感复盘\n\n"
        
        # 统计摘要
        content += "## 数据概览\n\n"
        content += f"- 总消息数: {analysis.total_messages}\n"
        content += f"- 保留消息: {analysis.kept_messages}\n"
        content += f"- 跳过消息: {analysis.skipped_messages}\n"
        content += f"- 平均情感分数: {analysis.avg_emotion_score:.2f}\n"
        content += f"- 提取事实: {len(analysis.extracted_facts)} 条\n\n"
        
        # 核心互动（高情感分数）
        content += "## 核心互动\n\n"
        high_emotion = [a for a in analyzed if not a.skipped and a.emotion_score >= 0.4]
        high_emotion.sort(key=lambda a: a.message.timestamp)
        
        if high_emotion:
            for a in high_emotion[:10]:  # 最多显示10条
                msg = a.message
                content += f"**[{msg.timestamp.strftime('%H:%M')}]** {msg.role}:\n"
                content += f"> {msg.content[:200]}{'...' if len(msg.content) > 200 else ''}\n\n"
                content += f"*情感分数: {a.emotion_score:.2f}*\n\n"
        else:
            content += "（今天的互动主要是技术讨论或日常对话）\n\n"
        
        # 事实候选
        if analysis.extracted_facts:
            content += "## 事实候选\n\n"
            for i, fact in enumerate(analysis.extracted_facts, 1):
                content += f"{i}. {fact}\n"
            content += "\n"
        
        # 敏感信息提示
        if analysis.sensitive_count > 0:
            content += f"## ⚠️ 敏感信息\n\n"
            content += f"本次复盘包含 {analysis.sensitive_count} 条敏感信息，请注意保护隐私。\n\n"
        
        return frontmatter + content
