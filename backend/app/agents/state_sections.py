"""技能名 → WorldState 状态区语义映射（能力侧数据）。

WorldState（内核）只认识自己的 section（copywrite/card），
哪个技能贡献哪个 section 由本文件声明——内核零业务名。
注册时机：WorldState.ingest 首次使用时懒加载（延迟 import，同 factory 手法）。
"""

# 技能名 → section（"copywrite" | "card"）
SKILL_SECTIONS: dict[str, str] = {
    # 文案组：产出 title/content → copywrite 区
    "copywrite": "copywrite",
    "copywriting": "copywrite",
    "caption_hashtag": "copywrite",
    # 卡片组：产出 card_draft → card_draft 区
    "card_design": "card",
    "card_xiaohongshu": "card",
    "card_compose": "card",
    "compose_card_deck": "card",
}
