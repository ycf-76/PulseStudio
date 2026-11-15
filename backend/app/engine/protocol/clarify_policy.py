"""澄清策略数据（二期从 session.py 外置）：哪些 node_type 触发澄清。

纯数据，被 session._classifier_fallback_check 消费。
"""
CLARIFY_TRIGGER_NODE_TYPES: frozenset[str] = frozenset({
    "produce", "create", "design", "generate", "copywrite", "card_design",
})
