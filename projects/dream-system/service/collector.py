"""
素材收集器 - 从L4会话和日记系统收集当天互动内容
"""

from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass


@dataclass
class Message:
    """单条消息"""
    timestamp: datetime
    role: str  # user/assistant
    content: str
    source: str  # wechat/api/group/diary


class DreamCollector:
    """梦境系统素材收集器"""
    
    def __init__(self, 
                 l4_session_dirs,
                 journal_dirs,
                 include_group: bool = False):
        if isinstance(l4_session_dirs, (str, Path)):
            l4_session_dirs = [l4_session_dirs]
        if isinstance(journal_dirs, (str, Path)):
            journal_dirs = [journal_dirs]
        self.l4_dirs = [Path(d) for d in l4_session_dirs]
        self.journal_dirs = [Path(d) for d in journal_dirs]
        self.include_group = include_group
    
    def collect_day(self, date: datetime) -> list[Message]:
        """
        收集指定日期的所有素材
        
        优先级:
        1. 日记系统（已经过滤和整理）
        2. L4会话（原始互动）
        3. 群聊（可选）
        """
        messages = []
        
        # 1. 收集日记系统内容
        messages.extend(self._collect_from_journal(date))
        
        # 2. 收集L4会话
        messages.extend(self._collect_from_l4(date))
        
        # 按时间排序
        messages.sort(key=lambda m: m.timestamp)
        
        return messages
    
    def _collect_from_journal(self, date: datetime) -> list[Message]:
        """从日记系统收集"""
        messages = []
        
        for journal_dir in self.journal_dirs:
            if not journal_dir.exists():
                continue
            
            # 日记文件支持两种可能结构：
            # 1. diary/YYYY/MM/YYYY-MM-DD.md
            # 2. 直接在 diary/YYYY-MM-DD.md
            year_dir = journal_dir / str(date.year)
            month_dir = year_dir / f"{date.month:02d}"
            diary_file = month_dir / f"{date.year}-{date.month:02d}-{date.day:02d}.md"
            if not diary_file.exists():
                diary_file = journal_dir / f"{date.year}-{date.month:02d}-{date.day:02d}.md"
            
            if diary_file.exists():
                content = diary_file.read_text(encoding='utf-8')
                # 移除YAML frontmatter
                if content.startswith('---'):
                    parts = content.split('---', 2)
                    if len(parts) >= 3:
                        content = parts[2].strip()
                
                messages.append(Message(
                    timestamp=datetime.combine(date, datetime.min.time()),
                    role='user',  # 日记是用户写的
                    content=content,
                    source='diary'
                ))
        
        return messages
    
    def _collect_from_l4(self, date: datetime) -> list[Message]:
        """从L4原始会话收集"""
        messages = []
        
        for l4_dir in self.l4_dirs:
            if not l4_dir.exists():
                continue
            
            # L4会话文件: L4_sessions/wechat/mainline/YYYY-MM-DD.txt
            session_file = l4_dir / f"{date.year}-{date.month:02d}-{date.day:02d}.txt"
            
            if not session_file.exists():
                # 尝试.md后缀
                session_file = l4_dir.parent / l4_dir.name / f"{date.year}-{date.month:02d}-{date.day:02d}.md"
            
            if session_file.exists():
                messages.extend(self._parse_l4_session(session_file, date))
            
            # 检查group目录（如果允许）
            if self.include_group:
                group_dir = l4_dir.parent / "group"
                if group_dir.exists():
                    group_file = group_dir / f"{date.year}-{date.month:02d}-{date.day:02d}.txt"
                    if group_file.exists():
                        messages.extend(self._parse_l4_session(group_file, date, is_group=True))
        
        return messages
    
    def _parse_l4_session(self, file_path: Path, date: datetime, is_group: bool = False) -> list[Message]:
        """解析L4会话文件"""
        messages = []
        
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
            lines = content.split('\n')
            
            current_role = None
            current_content = []
            current_time = datetime.combine(date, datetime.min.time())
            
            for line in lines:
                line = line.strip()
                
                # 识别角色标记
                if line.startswith('[USER'):
                    if current_role and current_content:
                        messages.append(Message(
                            timestamp=current_time,
                            role=current_role,
                            content='\n'.join(current_content),
                            source='group' if is_group else 'wechat'
                        ))
                    current_role = 'user'
                    current_content = []
                    # 尝试提取时间戳
                    if '发送时间:' in line:
                        try:
                            time_str = line.split('发送时间:')[1].split('|')[0].strip()
                            current_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                        except:
                            pass
                
                elif line.startswith('[ASSISTANT') or line.startswith('[A]'):
                    if current_role and current_content:
                        messages.append(Message(
                            timestamp=current_time,
                            role=current_role,
                            content='\n'.join(current_content),
                            source='group' if is_group else 'wechat'
                        ))
                    current_role = 'assistant'
                    current_content = []
                
                elif line and not line.startswith('---'):
                    current_content.append(line)
            
            # 添加最后一条消息
            if current_role and current_content:
                messages.append(Message(
                    timestamp=current_time,
                    role=current_role,
                    content='\n'.join(current_content),
                    source='group' if is_group else 'wechat'
                ))
        
        except Exception as e:
            print(f"Warning: Failed to parse {file_path}: {e}")
        
        return messages
    
    def get_recent_context(self, days: int = 7, max_chars: int = 4000) -> str:
        """
        获取最近N天的上下文（用于召回）
        
        Args:
            days: 回溯天数
            max_chars: 最大字符数
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        all_messages = []
        current = start_date
        while current <= end_date:
            all_messages.extend(self.collect_day(current))
            current += timedelta(days=1)
        
        # 构建上下文（最近的优先）
        all_messages.reverse()
        
        context_parts = []
        total_chars = 0
        
        for msg in all_messages:
            msg_text = f"[{msg.timestamp.strftime('%Y-%m-%d %H:%M')}] {msg.role}: {msg.content}\n"
            if total_chars + len(msg_text) > max_chars:
                break
            context_parts.insert(0, msg_text)  # 保持时间正序
            total_chars += len(msg_text)
        
        return '\n'.join(context_parts)
