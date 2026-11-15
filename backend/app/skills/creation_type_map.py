"""创作类型 → 技能清单映射（纯数据，二期从 agents/registry 搬出）。

消费方：agents/registry._resolve_creation_type_skills——
按 creation_type 决定注入哪些技能。此处只放数据，不放逻辑；
未来技能元数据化（.md frontmatter 声明）后此文件可退役。
"""
CREATION_TYPE_SKILLS: dict[str, dict[str, list[str]]] = {

    "image_text": {
        "produce":   ["compose_card_deck", "xhs_note_creator", "copywriting", "social_content",
                      "card_xiaohongshu", "card_quote", "card_design", "infographic",
                      "poster_hero", "comparison_card", "style_transfer", "generate_card_image"],
        "plan":      ["content_matrix", "topic_evaluator", "hook_generator", "carousel_planner",
                      "voice_builder", "positioning_analysis"],
        "publish":   ["publish_checklist", "quality_gate", "risk_scanner", "content_repurposing"],
        "discover":  ["trending_topics", "competitor_analysis", "content_gap_analysis"],
        "attribute": ["data_tracker", "comment_insights", "strategy_advisor", "content_postmortem"],
        "refine":    ["text_polisher", "text_condenser", "caption_hashtag", "post_formatter",
                      "persona_check"],
        "copywrite": ["casual", "elegant", "lively_girl", "professional"],
        "search":    ["trending_search", "xhs_search"],
        "messaging": ["email_send"],
    },
    "voiceover": {
        "produce":   ["copywriting", "social_content", "style_transfer"],
        "plan":      ["content_matrix", "hook_generator", "voice_builder"],
        "publish":   ["publish_checklist", "quality_gate"],
        "discover":  ["trending_topics"],
        "attribute": ["data_tracker", "comment_insights"],
        "refine":    ["text_polisher", "caption_hashtag"],
        "search":    ["trending_search", "xhs_search"],
        "messaging": ["email_send"],
    },
    "short_video": {
        "produce":   ["multi_image_video", "compose_video", "talking_head_overlay",
                      "copywriting", "style_transfer"],
        "plan":      ["content_matrix", "hook_generator", "carousel_planner"],
        "publish":   ["publish_checklist", "quality_gate"],
        "discover":  ["trending_topics"],
        "attribute": ["data_tracker", "comment_insights"],
        "refine":    ["caption_hashtag"],
        "search":    ["trending_search", "xhs_search"],
        "messaging": ["email_send"],
    },
    "ai_edit": {
        "produce":   ["style_transfer", "text_polisher", "text_condenser"],
        "plan":      ["voice_builder", "positioning_analysis"],
        "publish":   ["publish_checklist", "quality_gate", "risk_scanner"],
        "attribute": ["data_tracker", "comment_insights", "strategy_advisor"],
        "refine":    ["persona_check"],
        "messaging": ["email_send"],
    },
    "long_article": {
        "produce":   ["copywriting", "social_content", "style_transfer"],
        "plan":      ["content_matrix", "topic_evaluator", "hook_generator", "voice_builder"],
        "publish":   ["publish_checklist", "quality_gate"],
        "discover":  ["trending_topics", "competitor_analysis"],
        "attribute": ["data_tracker", "comment_insights", "strategy_advisor"],
        "refine":    ["text_polisher", "caption_hashtag", "post_formatter"],
        "search":    ["trending_search", "xhs_search"],
        "messaging": ["email_send"],
    },
    "live_clip": {
        "produce":   ["copywriting", "card_xiaohongshu", "caption_hashtag"],
        "plan":      ["hook_generator"],
        "publish":   ["publish_checklist"],
        "attribute": ["data_tracker"],
        "search":    ["trending_search", "xhs_search"],
        "messaging": ["email_send"],
    },
}
