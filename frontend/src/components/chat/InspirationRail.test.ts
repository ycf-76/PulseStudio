import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import InspirationRail from './InspirationRail.vue'
import { INSPIRATION_CATEGORIES, INSPIRATION_TEMPLATES } from './composables/useInspirationTemplates'

const COLLAPSE_KEY = 'pulse_inspiration_rail_collapsed'

function tabByName(wrapper: ReturnType<typeof mount>, label: string) {
  const tab = wrapper.findAll('.ir-tab').find(t => t.text().includes(label))
  if (!tab) throw new Error(`找不到分类标签：${label}`)
  return tab
}

function mountExpanded() {
  localStorage.setItem(COLLAPSE_KEY, '0')
  return mount(InspirationRail)
}

describe('InspirationRail', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('渲染全部分类标签（含「全部」）', () => {
    const wrapper = mount(InspirationRail)
    expect(wrapper.findAll('.ir-tab').length).toBe(INSPIRATION_CATEGORIES.length)
    expect(tabByName(wrapper, '全部').exists()).toBe(true)
    expect(tabByName(wrapper, INSPIRATION_CATEGORIES[1].label).exists()).toBe(true)
  })

  it('首次进入默认折叠，不渲染模板卡片', () => {
    const wrapper = mount(InspirationRail)
    expect(wrapper.findAll('.ir-card').length).toBe(0)
  })

  it('展开后展示全部模板', () => {
    const wrapper = mountExpanded()
    expect(wrapper.findAll('.ir-card').length).toBe(INSPIRATION_TEMPLATES.length)
  })

  it('点击分类标签只展示该分类的模板', async () => {
    const wrapper = mountExpanded()
    const label = INSPIRATION_CATEGORIES[1].label
    await tabByName(wrapper, label).trigger('click')
    const expected = INSPIRATION_TEMPLATES.filter(t => t.category === 'graphic').length
    expect(wrapper.findAll('.ir-card').length).toBe(expected)
    expect(tabByName(wrapper, label).classes()).toContain('is-active')
  })

  it('切换分类后可回到「全部」', async () => {
    const wrapper = mountExpanded()
    await tabByName(wrapper, INSPIRATION_CATEGORIES[3].label).trigger('click')
    await tabByName(wrapper, '全部').trigger('click')
    expect(wrapper.findAll('.ir-card').length).toBe(INSPIRATION_TEMPLATES.length)
  })

  it('点击卡片向外抛 pick 事件，携带该模板的 prompt', async () => {
    const wrapper = mountExpanded()
    await wrapper.findAll('.ir-card')[0].trigger('click')
    const emitted = wrapper.emitted('pick')
    expect(emitted).toBeTruthy()
    expect(emitted![0][0]).toBe(INSPIRATION_TEMPLATES[0].prompt)
  })

  it('点「全部」按钮进入分组视图，分类标题齐全', async () => {
    const wrapper = mountExpanded()
    await wrapper.find('.ir-actions .ir-act').trigger('click')
    expect(wrapper.find('.ir-groups').exists()).toBe(true)
    const titles = wrapper.findAll('.ir-group-title').map(t => t.text())
    expect(titles.length).toBe(INSPIRATION_CATEGORIES.length - 1)
    expect(wrapper.findAll('.ir-card').length).toBe(INSPIRATION_TEMPLATES.length)
  })

  it('卡片封面用 CSS 绘制，不依赖任何图片文件', () => {
    const wrapper = mountExpanded()
    expect(wrapper.findAll('.ir-card .tc').length).toBe(INSPIRATION_TEMPLATES.length)
    expect(wrapper.findAll('.ir-card img').length).toBe(0)
  })

  it('卡片容器声明为纵向滚动轴，滚轮不被劫持成横向滚动', () => {
    const wrapper = mountExpanded()
    const track = wrapper.find('.ir-track')
    expect(track.attributes('data-axis')).toBe('vertical')

    const el = track.element as HTMLElement
    const ev = new WheelEvent('wheel', { deltaY: 120, cancelable: true, bubbles: true })
    el.dispatchEvent(ev)
    expect(ev.defaultPrevented).toBe(false)
    expect(el.scrollLeft).toBe(0)
  })

  it('入场编排中挂 is-intro，结束后摘掉', () => {
    const on = mount(InspirationRail, { props: { introPlaying: true } })
    expect(on.classes()).toContain('is-intro')
    const off = mount(InspirationRail, { props: { introPlaying: false } })
    expect(off.classes()).not.toContain('is-intro')
  })
})