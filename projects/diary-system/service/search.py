"""本地搜索功能"""

from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import re


class SimpleSearch:
    """简单的本地全文搜索"""
    
    def __init__(self, storage):
        self.storage = storage
    
    def search_diaries(
        self,
        query: str,
        year: Optional[int] = None,
        month: Optional[int] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        搜索日记
        
        简单实现：遍历所有日记文件，全文匹配
        """
        diaries = self.storage.list_diaries(year, month)
        results = []
        
        query_lower = query.lower()
        
        for path in diaries:
            result = self.storage.read_markdown(path)
            if not result:
                continue
            
            metadata, body = result
            
            # 搜索metadata和正文
            search_text = f"{metadata.get('date', '')} {' '.join(metadata.get('tags', []))} {body}"
            
            if query_lower in search_text.lower():
                # 提取匹配上下文
                match_context = self._extract_context(body, query, 100)
                
                relative_path = self.storage.get_relative_path(path)
                results.append({
                    "date": metadata.get("date"),
                    "importance": metadata.get("importance"),
                    "tags": metadata.get("tags", []),
                    "context": match_context,
                    "path": str(path),
                    "uri": self.storage.get_obsidian_uri(relative_path)
                })
                
                if len(results) >= limit:
                    break
        
        return results
    
    def _extract_context(self, text: str, query: str, context_len: int = 100) -> str:
        """提取匹配上下文"""
        query_lower = query.lower()
        text_lower = text.lower()
        
        idx = text_lower.find(query_lower)
        if idx == -1:
            return text[:context_len] + "..."
        
        start = max(0, idx - context_len // 2)
        end = min(len(text), idx + len(query) + context_len // 2)
        
        context = text[start:end]
        if start > 0:
            context = "..." + context
        if end < len(text):
            context = context + "..."
        
        return context
