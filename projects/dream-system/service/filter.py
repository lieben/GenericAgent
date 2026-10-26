"""
情感分析与过滤器 - 从170迁移的核心过滤逻辑
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class FilterConfig:
    """过滤配置"""
    # 情感关键词（命中则高权重保留）
    emotion_keywords: list[str]
    
    # 事实标记词（提取为候选事实）
    fact_keywords: list[str]
    
    # 技术关键词（命中则跳过）
    tech_keywords: list[str]
    
    # 群聊标记（默认跳过）
    group_keywords: list[str]
    
    # 敏感信息标记
    sensitive_keywords: list[str]


# 默认过滤配置（从170迁移）
DEFAULT_FILTER_CONFIG = FilterConfig(
    emotion_keywords=[
        "记住", "别忘", "忘了", "后悔", "重要",
        "在乎", "喜欢", "不喜欢", "讨厌",
        "害怕", "难过", "委屈", "安心", "安全感",
        "陪伴", "边界", "雷区", "不要", "希望",
        "以后", "承诺", "关系", "日记", "梦"
    ],
    
    fact_keywords=[
        "我喜欢", "我不喜欢", "我希望", "我讨厌",
        "不要", "以后", "你要记住", "必须记住",
        "雷区", "偏好", "习惯"
    ],
    
    tech_keywords=[
        "python", "脚本", "报错", "traceback", "debug",
        "git", "commit", "接口", "api", "进程", "端口",
        "webdriver", "配置", "systemd", "日志",
        "json", "yaml", "patch", "代码", "文件",
        "函数", "变量", "import", "module"
    ],
    
    group_keywords=[
        "group", "群聊", "正常人类聚集地",
        "L2_group", "group_members", "roomid"
    ],
    
    sensitive_keywords=[
        "住址", "身份证", "银行卡", "密码", "密钥",
        "病", "医院", "药", "收入", "工资", "财务",
        "家人", "真实姓名"
    ]
)


class MessageFilter:
    """消息过滤器"""
    
    def __init__(self, config: FilterConfig = DEFAULT_FILTER_CONFIG):
        self.config = config
    
    def should_skip(self, content: str, source: str) -> tuple[bool, str]:
        """
        判断是否应该跳过这条消息
        
        Returns:
            (should_skip, reason)
        """
        # 群聊默认跳过
        if source == 'group' or any(kw in content for kw in self.config.group_keywords):
            return True, "群聊内容"
        
        # 技术内容跳过
        tech_count = sum(1 for kw in self.config.tech_keywords if kw in content.lower())
        if tech_count >= 3:  # 3个以上技术关键词
            return True, "技术讨论"
        
        return False, ""
    
    def get_emotion_score(self, content: str) -> float:
        """
        计算情感权重分数
        
        Returns:
            0.0-1.0，越高表示情感内容越重要
        """
        emotion_count = sum(1 for kw in self.config.emotion_keywords if kw in content)
        # 每个情感关键词 +0.2 分，最高1.0
        return min(1.0, emotion_count * 0.2)
    
    def extract_facts(self, content: str) -> list[str]:
        """
        提取可能的事实候选
        
        Returns:
            包含事实标记的句子列表
        """
        facts = []
        sentences = content.split('。')
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            
            # 检查是否包含事实标记
            for kw in self.config.fact_keywords:
                if kw in sentence:
                    facts.append(sentence + '。')
                    break
        
        return facts
    
    def mark_sensitive(self, content: str) -> tuple[bool, list[str]]:
        """
        标记敏感信息
        
        Returns:
            (has_sensitive, sensitive_keywords_found)
        """
        found = [kw for kw in self.config.sensitive_keywords if kw in content]
        return len(found) > 0, found
