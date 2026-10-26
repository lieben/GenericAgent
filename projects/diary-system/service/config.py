"""
日记系统配置管理
"""
from pathlib import Path
import os

try:
    import tomli
except ImportError:
    # 简化TOML解析器
    class tomli:
        @staticmethod
        def load(f):
            config = {}
            current_section = None
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
                    if value.lower() == 'true':
                        value = True
                    elif value.lower() == 'false':
                        value = False
                    elif value.isdigit():
                        value = int(value)
                    config[current_section][key] = value
            return config


class DiaryConfig:
    """日记系统配置"""
    
    def __init__(self, config_path: str = "config.toml"):
        self.config_path = Path(config_path)
        self.config = self._load_config()
    
    def _load_config(self) -> dict:
        """加载配置文件"""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"配置文件不存在: {self.config_path}\n"
                f"请复制 config.example.toml 为 config.toml 并修改配置"
            )
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return tomli.load(f)
    
    def _expand_path(self, path: str) -> str:
        """展开环境变量和~，支持可分发模板。"""
        return os.path.expanduser(os.path.expandvars(path))
    
    # Storage配置
    @property
    def vault_dir(self) -> str:
        return self._expand_path(self.config['storage']['vault_dir'])
    
    @property
    def vault_name(self) -> str:
        return self.config['storage']['vault_name']
    
    @property
    def diary_folder(self) -> str:
        return self.config['storage']['diary_folder']
    
    # MemPalace配置
    @property
    def mempalace_enabled(self) -> bool:
        return self.config.get('mempalace', {}).get('enabled', False)
    
    @property
    def mempalace_url(self) -> str:
        return self.config.get('mempalace', {}).get('sync_url', '')
    
    # Format配置
    @property
    def title_prefix(self) -> str:
        return self.config.get('format', {}).get('title_prefix', '📔 ')
    
    @property
    def time_format(self) -> str:
        return self.config.get('format', {}).get('time_format', '%Y-%m-%d %H:%M')
