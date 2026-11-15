import { describe, expect, it } from 'vitest'
import {
  INSPIRATION_ACCENT,
  INSPIRATION_CATEGORIES,
  INSPIRATION_TEMPLATES,
  INSPIRATION_TONE,
  useInspirationTemplates,
} from './useInspirationTemplates'

describe('灵感模板分类数据', () => {
  it('每个模板的 category 都能在分类表中找到（除「全部」）', () => {
    const known = new Set(INSPIRATION_CATEGORIES.filter(c => c.key !== 'all').map(c => c.key))
    for (const tpl of INSPIRATION_TEMPLATES) {
      expect(known.has(tpl.category)).toBe(true)
    }
  })

  it('模板 id 唯一，prompt 非空', () => {
    const ids = INSPIRATION_TEMPLATES.map(t => t.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const tpl of INSPIRATION_TEMPLATES) {
      expect(tpl.prompt.trim().length).toBeGreaterThan(0)
      expect(tpl.title.trim().length).toBeGreaterThan(0)
    }
  })

  it('每个分类至少有一个模板，避免出现空标签页', () => {
    for (const cat of INSPIRATION_CATEGORIES) {
      if (cat.key === 'all') continue
      const count = INSPIRATION_TEMPLATES.filter(t => t.category === cat.key).length
      expect(count).toBeGreaterThan(0)
    }
  })

  it('每个分类都有封面底色与主色，避免卡片出现空白封面', () => {
    for (const cat of INSPIRATION_CATEGORIES) {
      if (cat.key === 'all') continue
      expect(INSPIRATION_TONE[cat.key]).toBeTruthy()
      expect(INSPIRATION_ACCENT[cat.key]).toBeTruthy()
    }
  })

  it('「全部」计数等于模板总数，各分类计数之和也等于总数', () => {
    const { counts } = useInspirationTemplates()
    expect(counts.value.all).toBe(INSPIRATION_TEMPLATES.length)
    const sum = INSPIRATION_CATEGORIES
      .filter(c => c.key !== 'all')
      .reduce((acc, c) => acc + counts.value[c.key], 0)
    expect(sum).toBe(INSPIRATION_TEMPLATES.length)
  })
})

describe('useInspirationTemplates 过滤', () => {
  it('默认展示全部模板', () => {
    const { visibleTemplates } = useInspirationTemplates()
    expect(visibleTemplates.value.length).toBe(INSPIRATION_TEMPLATES.length)
  })

  it('切换分类后只展示该分类的模板', () => {
    const { activeCategory, visibleTemplates, selectCategory } = useInspirationTemplates()
    selectCategory('graphic')
    expect(activeCategory.value).toBe('graphic')
    expect(visibleTemplates.value.every(t => t.category === 'graphic')).toBe(true)

    selectCategory('video')
    expect(visibleTemplates.value.every(t => t.category === 'video')).toBe(true)
  })

  it('切回「全部」恢复全量', () => {
    const { visibleTemplates, selectCategory } = useInspirationTemplates()
    selectCategory('copy')
    selectCategory('all')
    expect(visibleTemplates.value.length).toBe(INSPIRATION_TEMPLATES.length)
  })

  it('分组视图按分类顺序排列且不含空组', () => {
    const { groupedTemplates } = useInspirationTemplates()
    const order = INSPIRATION_CATEGORIES.filter(c => c.key !== 'all').map(c => c.key)
    const keys = groupedTemplates.value.map(g => g.category.key)
    expect(keys).toEqual(order.filter(k => INSPIRATION_TEMPLATES.some(t => t.category === k)))
    for (const g of groupedTemplates.value) {
      expect(g.items.length).toBeGreaterThan(0)
      expect(g.items.every(t => t.category === g.category.key)).toBe(true)
    }
  })
})