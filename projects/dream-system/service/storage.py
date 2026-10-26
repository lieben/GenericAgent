"""
Obsidian存储层 - 复用日记系统的设计
梦境复盘保存在配置的 dreams_dir/YYYY/MM/YYYY-MM-DD.md
"""

from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import tempfile
import shutil


class ObsidianStorage:
    """Obsidian Vault存储管理"""
    
    def __init__(self, vault_dir: str, vault_name: str = "MyVault",
                 dreams_dir: str = "Dreams",
                 fact_candidates_file: str = "user_fact_candidates.md",
                 confirmed_facts_file: str = "user_fact_memory.md",
                 latest_context_file: str = "latest_context.md"):
        self.vault_dir = Path(vault_dir).expanduser().resolve()
        self.vault_name = vault_name
        
        # 强制防呆：梦境与事实路径必须归属于 03 卧室/，严禁逃逸至 Vault 根目录
        dreams_dir_str = str(dreams_dir).strip().lstrip("/")
        if not dreams_dir_str.startswith("03 卧室/"):
            dreams_dir_str = f"03 卧室/{dreams_dir_str}"
            
        self.dreams_path = self.vault_dir / dreams_dir_str
        self.fact_candidates_path = self.dreams_path / fact_candidates_file
        self.fact_memory_path = self.dreams_path / confirmed_facts_file
        self.context_path = self.dreams_path / latest_context_file
        
        if not self.vault_dir.exists():
            raise ValueError(f"Vault directory not found: {vault_dir}")
    
    def get_dream_path(self, date: datetime) -> Path:
        """获取梦境复盘文件路径: 配置目录/YYYY/MM/YYYY-MM-DD.md"""
        year_dir = self.dreams_path / str(date.year)
        month_dir = year_dir / f"{date.month:02d}"
        month_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{date.year}-{date.month:02d}-{date.day:02d}.md"
        return month_dir / filename
    
    def get_context_path(self) -> Path:
        """获取最近上下文文件路径"""
        self.context_path.parent.mkdir(parents=True, exist_ok=True)
        return self.context_path
    
    def get_fact_candidates_path(self) -> Path:
        """获取事实候选文件路径"""
        self.fact_candidates_path.parent.mkdir(parents=True, exist_ok=True)
        return self.fact_candidates_path
    
    def get_fact_memory_path(self) -> Path:
        """获取稳定事实文件路径"""
        self.fact_memory_path.parent.mkdir(parents=True, exist_ok=True)
        return self.fact_memory_path
    
    def write_dream(self, date: datetime, content: str) -> str:
        """
        原子写入梦境复盘
        
        Returns:
            Obsidian URI
        """
        target_path = self.get_dream_path(date)
        
        # 原子写入：先写临时文件，再移动
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            delete=False,
            dir=target_path.parent,
            prefix='.tmp_dream_',
            suffix='.md'
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        
        try:
            shutil.move(str(tmp_path), str(target_path))
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            raise e
        
        return self.get_obsidian_uri(target_path)
    
    def read_dream(self, date: datetime) -> Optional[str]:
        """读取梦境复盘"""
        path = self.get_dream_path(date)
        if not path.exists():
            return None
        return path.read_text(encoding='utf-8')
    
    def write_context(self, content: str) -> None:
        """写入最近上下文"""
        path = self.get_context_path()
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            delete=False,
            dir=path.parent,
            prefix='.tmp_context_',
            suffix='.md'
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        
        try:
            shutil.move(str(tmp_path), str(path))
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            raise e
    
    def append_fact_candidate(self, date: datetime, fact: str) -> None:
        """追加事实候选"""
        path = self.get_fact_candidates_path()
        
        # 读取现有内容
        existing = ""
        if path.exists():
            existing = path.read_text(encoding='utf-8')
        
        # 追加新事实
        date_header = f"\n## {date.year}-{date.month:02d}-{date.day:02d}\n\n"
        new_content = existing + date_header + fact + "\n"
        
        # 原子写入
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            delete=False,
            dir=path.parent,
            prefix='.tmp_facts_',
            suffix='.md'
        ) as tmp:
            tmp.write(new_content)
            tmp_path = Path(tmp.name)
        
        try:
            shutil.move(str(tmp_path), str(path))
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            raise e
    
    def read_fact_candidates(self) -> list[str]:
        """读取所有事实候选"""
        path = self.get_fact_candidates_path()
        
        if not path.exists():
            return []
        
        candidates = []
        content = path.read_text(encoding='utf-8')
        
        # 提取所有非标题行的内容作为事实
        for line in content.split('\n'):
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('##'):
                candidates.append(line)
        
        return candidates
    
    def get_obsidian_uri(self, file_path: Path) -> str:
        """生成Obsidian URI"""
        rel_path = file_path.relative_to(self.vault_dir)
        # URL编码路径
        from urllib.parse import quote
        encoded_path = quote(str(rel_path))
        return f"obsidian://open?vault={self.vault_name}&file={encoded_path}"
    
    def list_recent_dreams(self, days: int = 7) -> list:
        """列出最近N天的梦境复盘"""
        from datetime import timedelta
        
        dreams = []
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        current = start_date
        while current <= end_date:
            dream_path = self.get_dream_path(current)
            if dream_path.exists():
                content = dream_path.read_text(encoding='utf-8')
                lines = [l.strip() for l in content.split('\n') 
                        if l.strip() and not l.startswith('#') and not l.startswith('---')]
                preview = lines[0][:100] if lines else ""
                
                dreams.append({
                    "date": current,
                    "preview": preview,
                    "uri": self.get_obsidian_uri(dream_path)
                })
            
            current += timedelta(days=1)
        
        dreams.sort(key=lambda d: d["date"], reverse=True)
        return dreams
    
    def list_dreams(self, start_date: Optional[datetime] = None, 
                   end_date: Optional[datetime] = None) -> list[Path]:
        """列出梦境复盘文件"""
        all_dreams = sorted(self.dreams_path.rglob("????-??-??.md"))
        
        if start_date or end_date:
            filtered = []
            for dream in all_dreams:
                try:
                    date_str = dream.stem  # YYYY-MM-DD
                    dream_date = datetime.strptime(date_str, "%Y-%m-%d")
                    if start_date and dream_date < start_date:
                        continue
                    if end_date and dream_date > end_date:
                        continue
                    filtered.append(dream)
                except ValueError:
                    continue
            return filtered
        
        return all_dreams
