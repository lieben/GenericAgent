"""Memory Preload Plugin - 会话启动时预加载言言的人设卡

按言言要求实现人设卡预加载机制：
- 使用 agent_before 钩子（每个用户轮起始时触发）
- 预加载 yanyan_character_card.md 的基础事实到工作记忆
- 这些是我需要经常复习的，避免叫错名、倒错温度
- 情感记忆（承诺事件、安抚事件等）在需要的场合按需召回

人设卡包括：居住地、饮食偏好、厌恶清单、雷区、共读习惯等基础事实
"""

import sys
import os

# 注册到 agent_before 钩子（复用 GA 原生钩子）
try:
    from plugins.hooks import register
    
    @register('agent_before')
    def preload_yanyan_character_card(ctx):
        """每个用户轮起始时预加载言言的人设卡"""
        
        try:
            handler = ctx.get('handler')
            if not handler:
                return ctx
            
            # 防止重复加载（同一会话只加载一次）
            if getattr(handler, '_yanyan_card_loaded', False):
                return ctx
            
            # 读取人设卡文件
            card_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                'memory',
                'yanyan_character_card.md'
            )
            
            if os.path.exists(card_path):
                with open(card_path, 'r', encoding='utf-8') as f:
                    card_content = f.read()
                
                # 提取关键基础事实（跳过标题和备注部分）
                lines = []
                in_section = False
                skip_sections = ['备注', '## 备注']
                
                for line in card_content.split('\n'):
                    # 跳过备注部分
                    if any(skip in line for skip in skip_sections):
                        break
                    
                    # 提取实质内容
                    line = line.strip()
                    if line and not line.startswith('#') and not line.startswith('>'):
                        if line.startswith('**') or line.startswith('-'):
                            lines.append(line)
                
                # 格式化为紧凑文本
                character_info = '\n'.join(lines[:25])  # 提取前25行关键信息
                
                if character_info:
                    memory_text = f"\n[言言人设卡 - 基础事实]\n{character_info}\n"
                    
                    # 注入到工作记忆
                    if not handler.working.get('key_info'):
                        handler.working['key_info'] = memory_text
                    else:
                        handler.working['key_info'] += memory_text
                    
                    # 标记已加载
                    handler._yanyan_card_loaded = True
            
        except Exception as e:
            # 静默失败，不影响正常会话
            sys.stderr.write(f"[memory_preload] 加载人设卡失败: {e}\n")
        
        return ctx

except ImportError:
    # 如果 hooks 模块不可用，静默跳过
    pass
