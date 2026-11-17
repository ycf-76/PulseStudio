# Creator Platform Plugin System Core
# 一切皆插件 - 核心模块

from .base_interfaces import (
    BaseDatasourcePlugin,
    BasePlatformPlugin,
    BasePlugin,
    BaseWorkflowNodePlugin,
    PluginEventBus,
)
from .plugin_manager import (
    ExecuteRequest,
    PluginLoadResult,
    PluginManager,
)
from .plugin_types import (
    ExecutionResult,
    PluginCategory,
    PluginContext,
    PluginInstanceInfo,
    PluginManifest,
    PluginPermission,
    PluginStatus,
    PublishResult,
    TrendingContent,
)

__all__ = [
    # Types
    "PluginCategory",
    "PluginStatus",
    "PluginPermission",
    "ExecutionResult",
    "PublishResult",
    "TrendingContent",
    "PluginContext",
    "PluginManifest",
    "PluginInstanceInfo",
    # Interfaces
    "BasePlugin",
    "BasePlatformPlugin",
    "BaseDatasourcePlugin",
    "BaseWorkflowNodePlugin",
    "PluginEventBus",
    # Manager
    "PluginManager",
    "PluginLoadResult",
    "ExecuteRequest",
]
