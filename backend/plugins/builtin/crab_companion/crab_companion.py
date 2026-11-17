"""
Crab Companion Plugin
螃蟹伴侣插件 - 前端交互组件，后端仅提供配置管理
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class CrabCompanionPlugin:
    """螃蟹伴侣插件入口类"""

    PLUGIN_ID = "crab-companion"

    def __init__(self):
        self.config = {
            "crab_size": 48,
            "speech_enabled": True,
            # 便签（用户自填的提醒）真身存在 plugin_configs.config_json.crab_notes，
            # 这里只提供空默认值；故意不写进 config_schema，
            # 否则插件设置页的 DynamicForm 会把整个对象渲染成只读 JSON 预览。
            "crab_notes": {
                "settings": {
                    "notes_enabled": True,
                    "quiet_hours": "23-8",
                    "global_cooldown_sec": 5,
                    "max_per_session": 2,
                },
                "notes": [],
            },
        }
        logger.info(f"[{self.PLUGIN_ID}] plugin loaded")

    def get_config(self) -> Dict[str, Any]:
        return self.config.copy()

    def update_config(self, new_config: Dict[str, Any]) -> None:
        self.config.update(new_config)
        logger.info(f"[{self.PLUGIN_ID}] config updated: {new_config}")

    def on_enable(self) -> None:
        logger.info(f"[{self.PLUGIN_ID}] enabled")

    def on_disable(self) -> None:
        logger.info(f"[{self.PLUGIN_ID}] disabled")
