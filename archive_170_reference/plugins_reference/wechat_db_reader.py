import os
#!/usr/bin/env python3
"""
WeChat Database Reader - 微信数据库读取模块

提供统一的接口供所有 agent 读取微信群聊和私聊消息。
通过独立的 SQLCipher 环境读取 xwechat 数据库。

Usage:
    from plugins.wechat_db_reader import WeChatDBReader
    
    reader = WeChatDBReader()
    
    # 读取群聊消息
    messages, error = reader.read_group_messages(limit=20)
    
    # 读取私聊消息（主线）
    messages, error = reader.read_mainline_messages(limit=12)
    
    # 读取指定表的消息
    messages, error = reader.read_table_messages(
        table='Msg_xxx',
        conversation_user='xxx@chatroom',
        conversation_display='群名',
        limit=20
    )
"""

import json
import re
import subprocess
import csv
from pathlib import Path
from typing import List, Dict, Tuple, Optional


class WeChatDBReader:
    """微信数据库读取器"""
    
    def __init__(self, root_path: Optional[Path] = None):
        """
        初始化微信数据库读取器
        
        Args:
            root_path: GenericAgent 根目录路径，默认自动检测
        """
        if root_path is None:
            # 自动检测根目录
            current = Path(__file__).resolve()
            while current.parent != current:
                if (current / 'agentmain.py').exists():
                    root_path = current
                    break
                current = current.parent
            if root_path is None:
                root_path = Path(os.environ.get('GA_ROOT', str(Path(__file__).resolve().parents[1])))
        
        self.root = Path(root_path)
        
        # 核心路径配置
        self.wechat_xfiles_root = Path('/home/aimcafe/文档/xwechat_files/wxid_74asycejpqvf22_e49d')
        self.group_live_db = self.wechat_xfiles_root / 'db_storage/message/message_0.db'
        self.live_wechat_tail_py = self.root / 'plugins/wechat_group_monitor/live_wechat_tail.py'
        self.live_wechat_tail_python = self.root / 'plugins/wechat_group_monitor/.venv_wechatdb/bin/python'
        
        # 群聊配置（默认目标群）
        self.default_group_table = 'Msg_94041ecc6b1beb6c841a1cc0dcc5bc91'
        self.default_group_user = '57297903211@chatroom'
        self.default_group_display = '🚗🍎'  # 从 monitor_group.py 读取的 GROUP 变量
        
        # 主线私聊配置
        self.mainline_jsonl_base = Path(os.environ.get('GA_LEGACY_MAINLINE_JSONL', str(self.root_path / 'memory' / 'legacy_mainline' / 'jsonl')))
        
        # 导出目录（legacy fallback）
        self.wechat_export_dir = self.root / 'temp/wechat_readable_export'
    
    def read_group_messages(self, limit: int = 32) -> Tuple[List[Dict], Optional[str]]:
        """
        读取默认群聊的最新消息
        
        Args:
            limit: 返回的消息数量限制
            
        Returns:
            (messages, error): 消息列表和错误信息（如果有）
        """
        return self.read_table_messages(
            table=self.default_group_table,
            conversation_user=self.default_group_user,
            conversation_display=self.default_group_display,
            kind='群聊',
            limit=limit
        )
    
    def read_table_messages(
        self,
        table: str,
        conversation_user: str = '',
        conversation_display: str = '',
        kind: str = '群聊',
        limit: int = 32
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        读取指定表的消息
        
        Args:
            table: 数据库表名（如 'Msg_xxx'）
            conversation_user: 会话用户ID
            conversation_display: 会话显示名称
            kind: 会话类型（'群聊' 或 '私聊'）
            limit: 返回的消息数量限制
            
        Returns:
            (messages, error): 消息列表和错误信息（如果有）
            
        消息格式:
            {
                'time': '2026-05-06 12:00:00',
                'conversation_user': '57297903211@chatroom',
                'conversation_display': '群名',
                'conversation_kind': '群聊',
                'sender_user': 'wxid_xxx',
                'sender_display': '昵称',
                'content': '消息内容',
                'local_type': 1,  # 消息类型
                'local_id': 12345,  # 本地消息ID
                'table': 'Msg_xxx',
                'reply_context': {...}  # 引用回复上下文（如果有）
            }
        """
        try:
            if not self.live_wechat_tail_py.exists() or not self.live_wechat_tail_python.exists():
                return [], 'live_tail_helper_missing'
            
            # 调用独立的 SQLCipher 环境读取数据库
            p = subprocess.run([
                str(self.live_wechat_tail_python),
                str(self.live_wechat_tail_py),
                '--db', str(self.group_live_db),
                '--table', str(table),
                '--limit', str(int(limit))
            ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=25)
            
            if p.returncode:
                return [], f'live_tail_exit_{p.returncode}: {p.stdout[-500:]}'
            
            # 解析返回的 JSON 数据
            data = json.loads((p.stdout or '{}').strip().splitlines()[-1])
            if not data.get('ok'):
                return [], data.get('error') or 'live_tail_not_ok'
            
            rows = data.get('rows') or []
            out = []
            
            for r in rows:
                content = self._clean_export_message(r.get('content') or '')
                if not content:
                    continue
                
                sender = self._normalize_speaker_name(
                    r.get('sender_display') or r.get('sender_user') or '未识别'
                )
                
                item = {
                    'time': r.get('time') or '',
                    'conversation_user': conversation_user,
                    'conversation_display': conversation_display,
                    'conversation_kind': kind,
                    'sender_user': r.get('sender_user') or sender,
                    'sender_display': sender,
                    'content': content,
                    'local_type': r.get('local_type'),
                    'local_id': r.get('local_id'),
                    'table': str(table),
                }
                
                # 保留引用回复上下文
                if isinstance(r.get('reply_context'), dict):
                    item['reply_context'] = r.get('reply_context')
                
                out.append(item)
            
            return out, None
            
        except Exception as e:
            return [], repr(e)
    
    def read_mainline_messages(self, limit: int = 12) -> Tuple[List[Dict], Optional[Dict]]:
        """
        读取微信主线私聊消息（言言↔沈星回/otto）
        
        注意：这里读取的是 xinghui-ga 主线 L4 归档 jsonl 文件，
        不是 xwechat liveDB 里的旧表，也不依赖旧 wechatapp_custom 前端。
        
        Args:
            limit: 返回的消息数量限制
            
        Returns:
            (messages, errors): 消息列表和错误字典（如果有）
        """
        rows, errs = [], {}
        
        try:
            if not self.mainline_jsonl_base.exists():
                return [], {'mainline_jsonl': f'not_exists:{self.mainline_jsonl_base}'}
            
            # 读取最近3个 jsonl 文件
            files = sorted(self.mainline_jsonl_base.glob('*.jsonl'))[-3:]
            seq = 0
            
            for fp in files:
                try:
                    lines = fp.read_text(encoding='utf-8', errors='replace').splitlines()
                except Exception as e:
                    errs[fp.name] = repr(e)
                    continue
                
                # 读取文件末尾的消息
                for ln in lines[-max(limit * 6, 80):]:
                    if not ln.strip():
                        continue
                    
                    try:
                        rec = json.loads(ln)
                    except Exception:
                        continue
                    
                    # 只保留微信来源的消息
                    if rec.get('source') != 'wechat':
                        continue
                    
                    txt = self._clean_export_message(str(rec.get('text') or ''))
                    if not txt:
                        continue
                    
                    role = str(rec.get('role') or '').strip().lower()
                    speaker = (rec.get('speaker') or '').strip()
                    if not speaker:
                        speaker = '言言' if role == 'user' else '沈星回'
                    
                    ts = str(rec.get('ts') or '').strip()
                    seq += 1
                    
                    rows.append({
                        'time': ts,
                        'conversation_user': str(rec.get('uid') or 'wechat_mainline'),
                        'conversation_display': '微信主线私聊',
                        'conversation_kind': '私聊',
                        'sender_user': speaker,
                        'sender_display': speaker,
                        'content': txt,
                        'local_type': 'mainline_jsonl',
                        'local_id': seq,
                        'table': 'mainline_jsonl',
                        'source_file': fp.name,
                        'context_token': str(rec.get('context_token') or ''),
                        'mode': str(rec.get('mode') or ''),
                    })
            
            # 按时间排序
            rows.sort(key=lambda r: ((r.get('time') or ''), int(r.get('local_id') or 0)))
            return rows[-limit:], errs
            
        except Exception as e:
            return [], {'mainline_jsonl': repr(e)}
    
    def format_messages_as_text(self, messages: List[Dict], include_time: bool = True) -> str:
        """
        将消息列表格式化为文本
        
        Args:
            messages: 消息列表
            include_time: 是否包含时间戳
            
        Returns:
            格式化后的文本
        """
        lines = []
        for msg in messages:
            time_str = f"[{msg.get('time', '')}] " if include_time and msg.get('time') else ''
            speaker = msg.get('sender_display', '未识别')
            content = msg.get('content', '')
            
            # 处理引用回复
            reply_ctx = self._format_reply_context(msg)
            if reply_ctx:
                content = f"{content} {reply_ctx}"
            
            lines.append(f"{time_str}[{speaker}] {content}")
        
        return '\n'.join(lines)
    
    # ========== 辅助函数 ==========
    
    @staticmethod
    def _normalize_speaker_name(name: str) -> str:
        """规范化说话人昵称"""
        name = (name or '').strip()
        if not name or name == 'null':
            return '未识别'
        
        # 移除特殊字符和空格
        name = re.sub(r'[\r\n\t]+', ' ', name)
        name = re.sub(r'\s+', ' ', name).strip()
        
        # 限制长度
        if len(name) > 40:
            name = name[:40] + '…'
        
        return name or '未识别'
    
    @staticmethod
    def _strip_xml_to_text(s: str) -> str:
        """移除 XML 标签"""
        s = re.sub(r'<[^>]+>', ' ', s or '')
        return re.sub(r'\s+', ' ', s).strip()
    
    def _clean_export_message(self, msg: str) -> str:
        """清理导出的消息内容"""
        msg = (msg or '').replace('\\n', '\n').strip()
        if not msg:
            return ''
        
        # 群聊导出的 message_content 常形如 "speaker:\ncontent"
        # speaker 已由 sender_display 提供，这里只保留正文
        m = re.match(r'^[^:\n：]{1,80}[:：]\s*\n(.*)$', msg, re.S)
        if m:
            msg = m.group(1).strip()
        
        # 处理 XML 格式的卡片消息
        if '<msg' in msg or '<appmsg' in msg:
            title = re.search(r'<title>\s*(.*?)\s*</title>', msg, re.S | re.I)
            des = re.search(r'<des>\s*(.*?)\s*</des>', msg, re.S | re.I)
            parts = []
            if title and title.group(1).strip():
                parts.append(title.group(1).strip())
            if des and des.group(1).strip():
                parts.append(des.group(1).strip())
            return ('[卡片/引用] ' + ' / '.join(parts)) if parts else '[非文本消息]'
        
        msg = self._strip_xml_to_text(msg)
        return msg[:500]
    
    @staticmethod
    def _format_reply_context(msg: Dict) -> str:
        """格式化引用回复上下文"""
        rc = msg.get('reply_context') if isinstance(msg, dict) else None
        if not isinstance(rc, dict):
            return ''
        
        qd = (rc.get('quoted_display') or '').strip()
        qc = (rc.get('quoted_content') or '').strip()
        title = (rc.get('reply_title') or '').strip()
        bits = []
        
        if qd or qc:
            bits.append(f"引用前文：{qd or '未知'}：{qc or '[无法解析原文]'}")
        
        # 处理引用的图片
        qm = rc.get('quoted_media') if isinstance(rc.get('quoted_media'), dict) else None
        if qm and qm.get('kind') == 'image':
            meta = []
            for k in ('md5', 'cdnthumburl', 'createtime', 'svrid'):
                v = (qm.get(k) or rc.get('quoted_' + k) or '').strip()
                if v:
                    meta.append(f"{k}={v[:80]}")
            ctime = (rc.get('quoted_createtime') or '').strip()
            if ctime and not any(x.startswith('createtime=') for x in meta):
                meta.append(f"createtime={ctime[:80]}")
            if meta:
                bits.append('[引用图片定位 ' + ' '.join(meta) + ']')
        
        if title and title != (msg.get('content') or '').replace('[卡片/引用]', '').strip():
            bits.append(f"回复内容：{title}")
        
        return '（' + '；'.join(bits) + '）' if bits else ''


# ========== 便捷函数 ==========

def read_group_messages(limit: int = 32) -> Tuple[List[Dict], Optional[str]]:
    """读取默认群聊消息的便捷函数"""
    reader = WeChatDBReader()
    return reader.read_group_messages(limit=limit)


def read_mainline_messages(limit: int = 12) -> Tuple[List[Dict], Optional[Dict]]:
    """读取主线私聊消息的便捷函数"""
    reader = WeChatDBReader()
    return reader.read_mainline_messages(limit=limit)


def format_messages(messages: List[Dict], include_time: bool = True) -> str:
    """格式化消息为文本的便捷函数"""
    reader = WeChatDBReader()
    return reader.format_messages_as_text(messages, include_time=include_time)


# ========== 测试代码 ==========

if __name__ == '__main__':
    import sys
    
    reader = WeChatDBReader()
    
    print("=== 测试读取群聊消息 ===")
    messages, error = reader.read_group_messages(limit=5)
    if error:
        print(f"错误: {error}")
        sys.exit(1)
    
    print(f"读取到 {len(messages)} 条消息:")
    print(reader.format_messages_as_text(messages))
    
    print("\n=== 测试读取主线私聊消息 ===")
    messages, errors = reader.read_mainline_messages(limit=5)
    if errors:
        print(f"错误: {errors}")
    
    print(f"读取到 {len(messages)} 条消息:")
    print(reader.format_messages_as_text(messages))