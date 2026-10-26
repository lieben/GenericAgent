"""
配置管理
"""

from pathlib import Path
from typing import Optional
import os

try:
    import tomli
except ImportError:
    # 简化的TOML解析器（仅支持基础键值对）
    class tomli:
        @staticmethod
        def load(f):
            config = {}
            current_section = None
            # 统一处理为文本模式
            if hasattr(f, 'mode') and 'b' in f.mode:
                content = f.read().decode('utf-8')
            else:
                content = f.read()
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('[') and line.endswith(']'):
                    current_section = line[1:-1]
                    config[current_section] = {}
                elif '=' in line and current_section:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    # 简单类型转换
                    if value.lower() == 'true':
                        value = True
                    elif value.lower() == 'false':
                        value = False
                    elif value.isdigit():
                        value = int(value)
                    elif value.startswith('[') and value.endswith(']'):
                        value = [v.strip().strip('"').strip("'") for v in value[1:-1].split(',') if v.strip()]
                    config[current_section][key] = value
            return config


class DreamConfig:
    """梦境系统配置"""
    
    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            # 默认配置文件位置
            config_path = Path(__file__).parent.parent / "config.toml"
        
        self.config_path = Path(config_path)
        self.config = self._load_config()
    
    def _load_config(self) -> dict:
        """加载配置文件"""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"配置文件不存在: {self.config_path}\n"
                f"请复制 config.example.toml 为 config.toml 并修改配置"
            )
        
        with open(self.config_path, 'rb') as f:
            return tomli.load(f)
    
    # Storage配置
    @property
    def vault_dir(self) -> str:
        return self._expand_path(self.config['storage']['vault_dir'])
    
    @property
    def vault_name(self) -> str:
        return os.path.expandvars(self.config['storage']['vault_name'])
    
    @property
    def dreams_dir(self) -> str:
        return self.config['storage']['dreams_dir']
    
    @property
    def fact_candidates_file(self) -> str:
        return self.config['storage']['fact_candidates_file']
    
    @property
    def confirmed_facts_file(self) -> str:
        return self.config['storage']['confirmed_facts_file']
    
    @property
    def latest_context_file(self) -> str:
        return self.config['storage']['latest_context_file']
    
    # Collector配置
    @property
    def l4_sessions_dir(self) -> str:
        return self._expand_path(self.config['collector']['l4_sessions_dir'])
    
    @property
    def diary_dir(self) -> str:
        return self._expand_path(self.config['collector']['diary_dir'])
    
    @property
    def include_wechat(self) -> bool:
        return self.config['collector']['include_wechat']
    
    @property
    def include_api(self) -> bool:
        return self.config['collector']['include_api']
    
    @property
    def include_group(self) -> bool:
        return self.config['collector']['include_group']
    
    @property
    def include_diary(self) -> bool:
        return self.config['collector']['include_diary']
    
    # Filter配置
    @property
    def emotion_keywords(self) -> list[str]:
        return self.config['filter']['emotion_keywords']
    
    @property
    def fact_keywords(self) -> list[str]:
        return self.config['filter']['fact_keywords']
    
    @property
    def tech_keywords(self) -> list[str]:
        return self.config['filter']['tech_keywords']
    
    @property
    def group_keywords(self) -> list[str]:
        return self.config['filter']['group_keywords']
    
    @property
    def sensitive_keywords(self) -> list[str]:
        return self.config['filter']['sensitive_keywords']
    
    # Analyzer配置
    @property
    def min_emotion_score(self) -> float:
        return self.config['analyzer']['min_emotion_score']
    
    @property
    def max_context_chars(self) -> int:
        return self.config['analyzer']['max_context_chars']
    
    # Recall配置
    @property
    def default_search_days(self) -> int:
        return self.config['recall']['default_search_days']
    
    @property
    def max_results(self) -> int:
        return self.config['recall']['max_results']
    
    # MemPalace配置
    @property
    def mempalace_auto_sync(self) -> bool:
        return self.config['mempalace']['auto_sync']
    
    @property
    def mempalace_api_url(self) -> str:
        return self.config['mempalace']['api_url']
    
    @property
    def dream_drawer(self) -> str:
        return self.config['mempalace']['dream_drawer']
    
    @property
    def fact_drawer(self) -> str:
        return self.config['mempalace']['fact_drawer']
    
    def _expand_path(self, path: str) -> str:
        """展开环境变量和~，支持可分发模板。"""
        return str(Path(os.path.expandvars(path)).expanduser())
