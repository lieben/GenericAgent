"""碎片日记管理"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import hashlib

from .storage import ObsidianStorage


class Fragment:
    """碎片日记条目"""
    
    def __init__(
        self,
        title: str,
        content: str,
        date: Optional[datetime] = None,
        tags: Optional[List[str]] = None,
        technical_memory: bool = False
    ):
        self.date = date or datetime.now()
        self.title = title.strip()
        self.content = content.strip()
        self.tags = tags or []
        self.technical_memory = technical_memory
        self.timestamp = self.date.strftime("%H%M")
        self.fragment_id = self._generate_id()
    
    def _generate_id(self) -> str:
        """生成碎片ID"""
        date_str = self.date.strftime("%Y%m%d")
        hash_str = hashlib.sha256(f"{self.title}{self.content}".encode()).hexdigest()[:6]
        return f"fragment-{date_str}-{self.timestamp}-{hash_str}"
    
    def to_metadata(self) -> Dict[str, Any]:
        """生成metadata"""
        return {
            "fragment_id": self.fragment_id,
            "date": self.date.strftime("%Y-%m-%d"),
            "time": self.timestamp,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "title": self.title,
            "technical_memory": self.technical_memory,
            "tags": self.tags if self.tags else []
        }
    
    def to_markdown_body(self) -> str:
        """生成正文"""
        time_display = f"{self.timestamp[:2]}:{self.timestamp[2:]}"
        return f"# {time_display} · {self.title}\n\n{self.content}\n"


class FragmentService:
    """碎片日记服务"""
    
    def __init__(self, storage: ObsidianStorage):
        self.storage = storage
    
    def add_fragment(self, fragment: Fragment) -> Optional[Dict[str, Any]]:
        """添加碎片"""
        path = self.storage.get_fragment_path(
            fragment.date,
            fragment.title,
            fragment.timestamp
        )
        
        metadata = fragment.to_metadata()
        body = fragment.to_markdown_body()
        
        success = self.storage.write_markdown(path, metadata, body)
        
        if not success:
            return {"success": False, "error": "write_failed"}
        
        relative_path = self.storage.get_relative_path(path)
        uri = self.storage.get_obsidian_uri(relative_path)
        
        return {
            "success": True,
            "fragment_id": fragment.fragment_id,
            "path": str(path),
            "uri": uri
        }
    
    def list_fragments(self, date: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """列出碎片"""
        if date:
            fragments = self.storage.list_fragments(date.year, date.month)
        else:
            fragments = self.storage.list_fragments()
        
        results = []
        for path in fragments:
            result = self.storage.read_markdown(path)
            if result:
                metadata, _ = result
                relative_path = self.storage.get_relative_path(path)
                results.append({
                    "fragment_id": metadata.get("fragment_id"),
                    "date": metadata.get("date"),
                    "title": metadata.get("title"),
                    "tags": metadata.get("tags", []),
                    "path": str(path),
                    "uri": self.storage.get_obsidian_uri(relative_path)
                })
        
        return results
