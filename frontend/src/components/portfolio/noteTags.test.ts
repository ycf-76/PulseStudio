import { describe, expect, it } from 'vitest'
import { collectHashtags, extractHashtags, stripHashtags } from './noteTags'

const BODY = [
  '[向右R]上一个单词还记得是什么意思吗',
  '',
  'assess',
  '',
  '📝单词笔记',
  'eliminate v.清除；消除；剔除',
  '',
  '📊方法记忆',
  'eliminate: e=ex- 出 + limin = limit 界限、范围 + -ate 使',
  '',
  '#eliminate是什么意思？# [投票]#',
  '#标签# 根本记不住 [话题]# #标签# 每日一词 [话题]# #标签# 英语单词速记 [话题]#',
  '#标签# 四六级单词 [话题]# #标签# 每日英语 [话题]#',
].join('\n')

describe('extractHashtags 从正文抽话题', () => {
  it('成对井号之间的完整话题都能抽出来，含带空格与标点的话题名', () => {
    expect(extractHashtags('#每日一词# #英语单词速记#')).toEqual(['每日一词', '英语单词速记'])
  })

  it('三段式话题取中间那段真名，不拿「标签」当话题', () => {
    expect(extractHashtags('#标签# 每日一词 [话题]#')).toEqual(['每日一词'])
    expect(extractHashtags('#eliminate是什么意思？# [投票]#')).toEqual(['eliminate是什么意思？'])
  })

  it('没有话题时返回空数组', () => {
    expect(extractHashtags('只有正文没有话题')).toEqual([])
    expect(extractHashtags(null)).toEqual([])
  })
})

describe('stripHashtags 正文与话题分离', () => {
  it('剥掉末尾整串话题后只留正文，不留空行尾巴', () => {
    expect(stripHashtags('正文内容\n\n#每日一词# #英语单词#')).toBe('正文内容')
    expect(stripHashtags('正文内容\n\n#标签# 每日一词 [话题]#')).toBe('正文内容')
  })

  it('正文中间的话题一并剥离，纯话题正文变成空串', () => {
    expect(stripHashtags('#每日一词#')).toBe('')
    expect(stripHashtags('#标签# 每日一词 [话题]#')).toBe('')
    expect(stripHashtags('#甲#中间正文#乙#')).toBe('中间正文')
  })

  it('正文里不带井号的内容原样保留', () => {
    const plain = 'eliminate v.清除；消除；剔除'
    expect(stripHashtags(plain)).toBe(plain)
  })
})

describe('collectHashtags 正文与落库 tags 合并去重', () => {
  it('截图真实形态：话题从正文重建，残缺的「标签|话题|」被丢弃', () => {
    const stored = [
      'eliminate是什么意思？',
      '标签|话题|',
      '根本记不住|话题|',
      '每日一词|话题|',
      '英语单词速记|话题|',
      '四六级单词|话题|',
      '每日英语|话题|',
    ]
    expect(collectHashtags(BODY, stored)).toEqual([
      'eliminate是什么意思？',
      '根本记不住',
      '每日一词',
      '英语单词速记',
      '四六级单词',
      '每日英语',
    ])
  })

  it('正文里拿不到话题时，只有占位词的残缺值被丢弃，真名还能从落库值里救回', () => {
    expect(collectHashtags('纯正文', ['标签|话题|', '每日一词|话题|'])).toEqual(['每日一词'])
  })

  it('正文话题优先于落库同义值，同一话题只出现一次', () => {
    expect(collectHashtags('#每日一词#', ['每日一词|话题|'])).toEqual(['每日一词'])
  })

  it('落库里有正文没提到的话题时作为补全追加在末尾', () => {
    expect(collectHashtags('#甲#', ['乙'])).toEqual(['甲', '乙'])
  })

  it('tags 为字符串时按逗号顿号拆分，非法类型安全忽略', () => {
    expect(collectHashtags('', '甲,乙、丙')).toEqual(['甲', '乙', '丙'])
    expect(collectHashtags('', null)).toEqual([])
    expect(collectHashtags(null, [{ name: 'x' }])).toEqual([])
  })

  it('井号前缀与空白被归一化，重复值不重复输出', () => {
    expect(collectHashtags('# #甲# ', ['##甲', ' 甲 '])).toEqual(['甲'])
  })
})