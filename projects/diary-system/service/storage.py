"""
Obsidian 存储层

负责：
- 路径解析与验证
- Markdown 文件原子写入
- 读取与验证
- Obsidian URI 生成
- 目录组织（按日期）
"""

import os
import json
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from urllib.parse import quote


class ObsidianStorage:
    """Obsidian Vault 存储管理"""
    
    def __init__(self, vault_dir: str, diary_dir: str = "Diary", fragments_dir: str = "Diary/Fragments"):
        """
        初始化存储
        
        Args:
            vault_dir: Obsidian Vault 根目录
            diary_dir: 日记目录（相对于vault_dir）
            fragments_dir: 碎片目录（相对于vault_dir）
        """
        self.vault_path = Path(vault_dir).expanduser().resolve()
        if not self.vault_path.exists():
            raise ValueError(f"Vault directory does not exist: {self.vault_path}")
        
        # 强制防呆：日记与碎片路径必须归属于 03 卧室/，严禁逃逸至 Vault 根目录
        diary_dir_str = str(diary_dir).strip().lstrip("/")
        if not diary_dir_str.startswith("03 卧室/"):
            diary_dir_str = f"03 卧室/{diary_dir_str}"
            
        fragments_dir_str = str(fragments_dir).strip().lstrip("/")
        if not fragments_dir_str.startswith("03 卧室/"):
            fragments_dir_str = f"03 卧室/{fragments_dir_str}"

        self.diary_path = self.vault_path / diary_dir_str
        self.fragments_path = self.vault_path / fragments_dir_str
        
        # 确保目录存在
        self.diary_path.mkdir(parents=True, exist_ok=True)
        self.fragments_path.mkdir(parents=True, exist_ok=True)
    
    def get_diary_path(self, date: datetime) -> Path:
        """
        获取指定日期的日记文件路径
        
        组织结构: Diary/YYYY/MM/YYYY-MM-DD.md
        """
        year = date.strftime("%Y")
        month = date.strftime("%m")
        filename = date.strftime("%Y-%m-%d.md")
        
        date_dir = self.diary_path / year / month
        date_dir.mkdir(parents=True, exist_ok=True)
        
        return date_dir / filename
    
    def get_fragment_path(self, date: datetime, title: str, timestamp: str) -> Path:
        """
        获取碎片日记路径
        
        组织结构: Diary/Fragments/YYYY/MM/YYYY-MM-DD_HHMM_标题.md
        """
        year = date.strftime("%Y")
        month = date.strftime("%m")
        date_str = date.strftime("%Y-%m-%d")
        
        # 清理标题中的非法字符
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '_', '-', '，', '。'))
        safe_title = safe_title.strip()[:50]  # 限制长度
        
        filename = f"{date_str}_{timestamp}_{safe_title}.md"
        
        fragment_dir = self.fragments_path / year / month
        fragment_dir.mkdir(parents=True, exist_ok=True)
        
        return fragment_dir / filename
    
    def write_markdown(self, path: Path, metadata: Dict[str, Any], body: str) -> bool:
        """
        原子写入 Markdown 文件
        
        格式:
        ---
        metadata (YAML)
        ---
        
        body
        
        Returns:
            是否写入成功
        """
        try:
            # 生成 YAML frontmatter
            yaml_lines = ["---"]
            for key, value in metadata.items():
                if isinstance(value, list):
                    yaml_lines.append(f"{key}:")
                    for item in value:
                        yaml_lines.append(f"  - {item}")
                elif isinstance(value, dict):
                    yaml_lines.append(f"{key}:")
                    for k, v in value.items():
                        yaml_lines.append(f"  {k}: {v}")
                elif isinstance(value, bool):
                    yaml_lines.append(f"{key}: {str(value).lower()}")
                elif value is None:
                    yaml_lines.append(f"{key}: null")
                else:
                    yaml_lines.append(f"{key}: {value}")
            yaml_lines.append("---")
            
            frontmatter = "\n".join(yaml_lines)
            content = f"{frontmatter}\n\n{body}\n"
            
            # 原子写入：先写临时文件，再替换
            temp_fd, temp_path = tempfile.mkstemp(
                dir=path.parent,
                prefix=".tmp_diary_",
                suffix=".md",
                text=True
            )
            
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                
                # 原子替换
                os.replace(temp_path, path)
                return True
                
            except Exception as e:
                # 清理临时文件
                try:
                    os.unlink(temp_path)
                except:
                    pass
                raise e
                
        except Exception as e:
            print(f"Failed to write markdown: {e}")
            return False
    
    def append_markdown(self, path: Path, content: str, section_title: Optional[str] = None) -> bool:
        """
        追加内容到现有日记
        
        Args:
            path: 文件路径
            content: 要追加的内容
            section_title: 可选的章节标题
        
        Returns:
            是否追加成功
        """
        try:
            if not path.exists():
                return False
            
            # 读取现有内容
            existing = path.read_text(encoding='utf-8')
            
            # 构建追加内容
            append_parts = ["\n---\n"]
            if section_title:
                append_parts.append(f"\n## {section_title}\n")
            append_parts.append(f"\n{content}\n")
            
            append_text = "".join(append_parts)
            
            # 原子追加
            temp_fd, temp_path = tempfile.mkstemp(
                dir=path.parent,
                prefix=".tmp_diary_",
                suffix=".md",
                text=True
            )
            
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    f.write(existing)
                    f.write(append_text)
                    f.flush()
                    os.fsync(f.fileno())
                
                os.replace(temp_path, path)
                return True
                
            except Exception as e:
                try:
                    os.unlink(temp_path)
                except:
                    pass
                raise e
                
        except Exception as e:
            print(f"Failed to append markdown: {e}")
            return False
    
    def read_markdown(self, path: Path) -> Optional[Tuple[Dict[str, Any], str]]:
        """
        读取 Markdown 文件
        
        Returns:
            (metadata, body) 或 None
        """
        try:
            if not path.exists():
                return None
            
            content = path.read_text(encoding='utf-8')
            
            # 解析 frontmatter
            if not content.startswith('---\n'):
                return None
            
            parts = content.split('---\n', 2)
            if len(parts) < 3:
                return None
            
            yaml_content = parts[1]
            body = parts[2].strip()
            
            # 简单 YAML 解析（足够处理日记的简单结构）
            metadata = {}
            current_key = None
            current_list = None
            
            for line in yaml_content.split('\n'):
                line = line.rstrip()
                if not line:
                    continue
                
                if line.startswith('  - '):
                    # 列表项
                    if current_list is not None:
                        current_list.append(line[4:].strip())
                elif line.startswith('  '):
                    # 嵌套字典（暂不支持）
                    continue
                elif ':' in line:
                    # 键值对
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    if not value:
                        # 列表或字典开始
                        current_list = []
                        metadata[key] = current_list
                        current_key = key
                    else:
                        # 直接值
                        if value.lower() == 'true':
                            metadata[key] = True
                        elif value.lower() == 'false':
                            metadata[key] = False
                        elif value == 'null':
                            metadata[key] = None
                        elif value.isdigit():
                            metadata[key] = int(value)
                        else:
                            metadata[key] = value
                        current_list = None
            
            return (metadata, body)
            
        except Exception as e:
            print(f"Failed to read markdown: {e}")
            return None
    
    def get_obsidian_uri(self, relative_path: str, vault_name: Optional[str] = None) -> str:
        """
        生成 Obsidian URI
        
        格式: obsidian://open?vault=VaultName&file=Diary/2026/09/2026-09-10.md
        
        Args:
            relative_path: 相对于 vault 根目录的路径
            vault_name: Vault 名称（可选，如果不提供则从路径推断）
        """
        if vault_name is None:
            vault_name = self.vault_path.name
        
        encoded_vault = quote(vault_name)
        encoded_file = quote(relative_path.replace('\\', '/'))
        
        return f"obsidian://open?vault={encoded_vault}&file={encoded_file}"
    
    def get_relative_path(self, absolute_path: Path) -> str:
        """获取相对于 vault 根目录的路径"""
        try:
            return str(absolute_path.relative_to(self.vault_path))
        except ValueError:
            return str(absolute_path)
    
    def list_diaries(self, year: Optional[int] = None, month: Optional[int] = None) -> list[Path]:
        """
        列出日记文件
        
        Args:
            year: 指定年份（可选）
            month: 指定月份（可选）
        
        Returns:
            日记文件路径列表（按日期排序）
        """
        if year and month:
            search_dir = self.diary_path / str(year) / f"{month:02d}"
        elif year:
            search_dir = self.diary_path / str(year)
        else:
            search_dir = self.diary_path
        
        if not search_dir.exists():
            return []
        
        diaries = list(search_dir.glob("**/*.md"))
        return sorted(diaries, reverse=True)  # 最新的在前
    
    def list_fragments(self, year: Optional[int] = None, month: Optional[int] = None) -> list[Path]:
        """列出碎片日记"""
        if year and month:
            search_dir = self.fragments_path / str(year) / f"{month:02d}"
        elif year:
            search_dir = self.fragments_path / str(year)
        else:
            search_dir = self.fragments_path
        
        if not search_dir.exists():
            return []
        
        fragments = list(search_dir.glob("**/*.md"))
        return sorted(fragments, reverse=True)
