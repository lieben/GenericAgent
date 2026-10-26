"""
召回查询 - 从梦境复盘和事实库中检索记忆
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


@dataclass
class RecallResult:
    """召回结果"""
    dream_uri: str  # Obsidian URI
    date: datetime
    content_snippet: str  # 内容片段
    relevance_score: float  # 相关性分数
    

class DreamRecall:
    """梦境召回查询"""
    
    def __init__(self, storage):
        self.storage = storage
    
    def search_by_keyword(self, 
                         keyword: str, 
                         days: Optional[int] = None,
                         limit: int = 10) -> list[RecallResult]:
        """
        按关键词搜索梦境复盘
        
        Args:
            keyword: 搜索关键词
            days: 只搜索最近N天，None表示全部
            limit: 最多返回几条结果
        """
        # 1. 获取所有梦境
        all_dreams = self.storage.list_dreams()
        
        # 2. 按时间过滤
        filtered_dreams = []
        for dream in all_dreams:
            if isinstance(dream, Path):
                try:
                    dream_date = datetime.strptime(dream.stem, "%Y-%m-%d")
                except ValueError:
                    continue
                if days:
                    cutoff = datetime.now() - timedelta(days=days)
                    if dream_date < cutoff:
                        continue
                filtered_dreams.append({
                    'uri': self.storage.get_obsidian_uri(dream),
                    'date': dream_date,
                    'content': dream.read_text(encoding='utf-8')
                })
            elif isinstance(dream, dict):
                if days:
                    cutoff = datetime.now() - timedelta(days=days)
                    if dream.get('date') and dream['date'] < cutoff:
                        continue
                filtered_dreams.append(dream)

        # 3. 按关键词匹配并打分
        results = []
        for dream in filtered_dreams:
            content = dream['content']
            
            # 简单的关键词匹配打分
            score = self._calculate_relevance(content, keyword)
            
            if score > 0:
                # 提取包含关键词的片段
                snippet = self._extract_snippet(content, keyword)
                
                results.append(RecallResult(
                    dream_uri=dream['uri'],
                    date=dream['date'],
                    content_snippet=snippet,
                    relevance_score=score
                ))
        
        # 4. 按相关性排序并返回top N
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results[:limit]
    
    def search_facts(self, keyword: str) -> list[str]:
        """
        在事实候选和事实记忆中搜索
        
        Returns:
            匹配的事实列表
        """
        facts = []
        
        # 读取事实候选
        candidates = self.storage.read_fact_candidates()
        for fact in candidates:
            if keyword in fact:
                facts.append(f"[候选] {fact}")
        
        # 读取确认的事实
        confirmed = self.storage.read_confirmed_facts()
        for fact in confirmed:
            if keyword in fact:
                facts.append(f"[确认] {fact}")
        
        return facts
    
    def get_context(self, days: int = 7) -> str:
        """
        获取最近N天的情感上下文
        
        Returns:
            上下文文本
        """
        return self.storage.read_context()
    
    def _calculate_relevance(self, content: str, keyword: str) -> float:
        """
        计算内容与关键词的相关性分数
        
        简单实现：关键词出现次数作为基础分数
        """
        # 转小写比较
        content_lower = content.lower()
        keyword_lower = keyword.lower()
        
        # 计算关键词出现次数
        count = content_lower.count(keyword_lower)
        
        if count == 0:
            return 0.0
        
        # 基础分数 = 出现次数 * 0.1，上限1.0
        score = min(count * 0.1, 1.0)
        
        # 如果关键词出现在标题或重要章节，加权
        if '## 核心互动' in content:
            core_section = content.split('## 核心互动')[1].split('##')[0]
            if keyword_lower in core_section.lower():
                score *= 1.5
        
        return min(score, 1.0)
    
    def _extract_snippet(self, content: str, keyword: str, context_chars: int = 100) -> str:
        """
        提取包含关键词的内容片段
        
        Args:
            content: 完整内容
            keyword: 关键词
            context_chars: 前后各保留多少字符
        """
        keyword_lower = keyword.lower()
        content_lower = content.lower()
        
        # 找到第一次出现的位置
        pos = content_lower.find(keyword_lower)
        
        if pos == -1:
            return content[:200] + "..."
        
        # 提取前后文
        start = max(0, pos - context_chars)
        end = min(len(content), pos + len(keyword) + context_chars)
        
        snippet = content[start:end]
        
        # 添加省略号
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."
        
        return snippet
