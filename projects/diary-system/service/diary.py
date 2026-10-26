"""
日记核心逻辑

负责：
- 日记元数据生成
- 日记写入控制
- 格式验证
- 日记读取
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path
import hashlib

from .storage import ObsidianStorage


class DiaryEntry:
    """日记条目"""
    
    def __init__(
        self,
        date: datetime,
        content: str,
        tips: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        importance: int = 3,
        technical_memory: bool = False,
        source_refs: Optional[List[str]] = None,
        persona_name: Optional[str] = None,
        user_name: Optional[str] = None,
        weather: Optional[Dict[str, Any]] = None,
        mood: Optional[str] = None
    ):
        self.date = date
        self.content = content.strip()
        self.tips = tips or []
        self.tags = tags or []
        self.importance = max(1, min(5, importance))  # 限制在 1-5
        self.technical_memory = technical_memory
        self.source_refs = source_refs or []
        self.persona_name = persona_name
        self.user_name = user_name
        self.weather = weather
        self.mood = mood
        
        # 生成唯一ID
        self.entry_id = self._generate_entry_id()
    
    def _generate_entry_id(self) -> str:
        """生成唯一ID"""
        date_str = self.date.strftime("%Y%m%d")
        content_hash = hashlib.sha256(self.content.encode()).hexdigest()[:8]
        return f"diary-{date_str}-{content_hash}"
    
    def to_metadata(self) -> Dict[str, Any]:
        """生成 YAML metadata"""
        metadata = {
            "entry_id": self.entry_id,
            "date": self.date.strftime("%Y-%m-%d"),
            "weekday": self._get_weekday_zh(),
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "importance": self.importance,
            "technical_memory": self.technical_memory,
        }
        
        if self.persona_name:
            metadata["persona_name"] = self.persona_name
        
        if self.user_name:
            metadata["user_name"] = self.user_name
        
        if self.tags:
            metadata["tags"] = self.tags
        
        if self.source_refs:
            metadata["source_refs"] = self.source_refs
        
        if self.weather:
            metadata["weather"] = self.weather
        
        if self.mood:
            metadata["mood"] = self.mood
        
        return metadata
    
    def _get_weekday_zh(self) -> str:
        """获取中文星期"""
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return weekdays[self.date.weekday()]
    
    def to_markdown_body(self) -> str:
        """生成 Markdown 正文"""
        parts = []
        
        # 标题
        title = self.date.strftime("# %Y-%m-%d")
        if self.mood:
            title += f" · {self.mood}"
        parts.append(title)
        parts.append("")
        
        # 正文
        parts.append(self.content)
        parts.append("")
        
        # Tips（如果有）
        if self.tips:
            parts.append("---")
            parts.append("")
            parts.append("## tips｜给以后回忆用，不属于正文")
            parts.append("")
            for tip in self.tips:
                parts.append(f"- {tip}")
            parts.append("")
        
        return "\n".join(parts)


class DiaryService:
    """日记服务"""
    
    def __init__(self, storage: ObsidianStorage):
        self.storage = storage
    
    def write_diary(self, entry: DiaryEntry, overwrite: bool = False) -> Optional[Dict[str, Any]]:
        """
        写入日记
        
        Args:
            entry: 日记条目
            overwrite: 是否覆盖已存在的日记
        
        Returns:
            写入结果 {success, path, uri} 或 None
        """
        path = self.storage.get_diary_path(entry.date)
        
        # 检查是否已存在
        if path.exists() and not overwrite:
            return {
                "success": False,
                "error": "diary_exists",
                "message": f"Diary already exists for {entry.date.strftime('%Y-%m-%d')}. Use append or overwrite=True.",
                "path": str(path)
            }
        
        # 写入
        metadata = entry.to_metadata()
        body = entry.to_markdown_body()
        
        success = self.storage.write_markdown(path, metadata, body)
        
        if not success:
            return {
                "success": False,
                "error": "write_failed",
                "message": "Failed to write diary file"
            }
        
        # 重新读取验证
        verify = self.storage.read_markdown(path)
        if verify is None:
            return {
                "success": False,
                "error": "verify_failed",
                "message": "Diary written but verification failed"
            }
        
        # 生成 Obsidian URI
        relative_path = self.storage.get_relative_path(path)
        uri = self.storage.get_obsidian_uri(relative_path)
        
        return {
            "success": True,
            "entry_id": entry.entry_id,
            "path": str(path),
            "relative_path": relative_path,
            "uri": uri,
            "date": entry.date.strftime("%Y-%m-%d")
        }
    
    def append_diary(
        self,
        date: datetime,
        content: str,
        section_title: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        追加内容到已存在的日记
        
        Args:
            date: 日期
            content: 要追加的内容
            section_title: 可选的章节标题
        
        Returns:
            追加结果
        """
        path = self.storage.get_diary_path(date)
        
        if not path.exists():
            return {
                "success": False,
                "error": "diary_not_found",
                "message": f"No diary found for {date.strftime('%Y-%m-%d')}"
            }
        
        success = self.storage.append_markdown(path, content, section_title)
        
        if not success:
            return {
                "success": False,
                "error": "append_failed",
                "message": "Failed to append to diary"
            }
        
        relative_path = self.storage.get_relative_path(path)
        uri = self.storage.get_obsidian_uri(relative_path)
        
        return {
            "success": True,
            "path": str(path),
            "uri": uri,
            "date": date.strftime("%Y-%m-%d")
        }
    
    def read_diary(self, date: datetime) -> Optional[Dict[str, Any]]:
        """
        读取指定日期的日记
        
        Returns:
            {metadata, content, path, uri} 或 None
        """
        path = self.storage.get_diary_path(date)
        
        if not path.exists():
            return None
        
        result = self.storage.read_markdown(path)
        if result is None:
            return None
        
        metadata, body = result
        
        relative_path = self.storage.get_relative_path(path)
        uri = self.storage.get_obsidian_uri(relative_path)
        
        return {
            "metadata": metadata,
            "content": body,
            "path": str(path),
            "relative_path": relative_path,
            "uri": uri
        }
    
    def list_recent_diaries(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        列出最近N天的日记
        
        Returns:
            日记列表（按日期倒序）
        """
        diaries = self.storage.list_diaries()
        results = []
        
        for path in diaries[:days]:
            result = self.storage.read_markdown(path)
            if result:
                metadata, _ = result
                relative_path = self.storage.get_relative_path(path)
                uri = self.storage.get_obsidian_uri(relative_path)
                
                results.append({
                    "date": metadata.get("date"),
                    "weekday": metadata.get("weekday"),
                    "importance": metadata.get("importance"),
                    "tags": metadata.get("tags", []),
                    "path": str(path),
                    "uri": uri
                })
        
        return results
