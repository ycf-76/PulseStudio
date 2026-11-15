import { describe, expect, it } from 'vitest'
import { buildFileNodes, findTreeNode, flattenFileTree, normalizeRelativePath } from './file-tree'

describe('normalizeRelativePath', () => {
  it('把 Windows 反斜杠统一成正斜杠', () => {
    expect(normalizeRelativePath('assets\\images\\cover.png')).toBe('assets/images/cover.png')
  })

  it('去掉首尾斜杠', () => {
    expect(normalizeRelativePath('/assets/')).toBe('assets')
  })
})

describe('buildFileNodes', () => {
  it('文件夹排在文件前面，并按名称排序', () => {
    const nodes = buildFileNodes(
      [
        { name: 'zeta', path: 'zeta' },
        { name: 'alpha', path: 'alpha' },
      ],
      [
        { name: 'b.txt', path: 'b.txt', size: 10 },
        { name: 'a.txt', path: 'a.txt', size: 20 },
      ],
    )
    expect(nodes.map(n => n.name)).toEqual(['alpha', 'zeta', 'a.txt', 'b.txt'])
    expect(nodes[0].isDir).toBe(true)
    expect(nodes[2].isDir).toBe(false)
    expect(nodes[2].size).toBe(20)
  })

  it('规范化 Windows 路径分隔符', () => {
    const nodes = buildFileNodes([{ name: 'images', path: 'assets\\images' }], [])
    expect(nodes[0].path).toBe('assets/images')
  })
})

describe('findTreeNode', () => {
  it('可以递归查找到子节点', () => {
    const root = buildFileNodes([{ name: 'assets', path: 'assets' }], [])
    root[0].children = buildFileNodes([], [{ name: 'cover.png', path: 'assets\\cover.png', size: 1024 }])
    expect(findTreeNode(root, 'assets/cover.png')?.name).toBe('cover.png')
    expect(findTreeNode(root, 'missing.png')).toBeNull()
  })
})

describe('flattenFileTree', () => {
  it('只展开已展开目录的子项', () => {
    const root = buildFileNodes([{ name: 'assets', path: 'assets' }], [{ name: 'readme.md', path: 'readme.md' }])
    root[0].children = buildFileNodes([], [{ name: 'cover.png', path: 'assets/cover.png' }])

    const collapsed = flattenFileTree(root, () => false)
    expect(collapsed.map(row => row.node.name)).toEqual(['assets', 'readme.md'])

    const expanded = flattenFileTree(root, node => node.path === 'assets')
    expect(expanded.map(row => row.node.name)).toEqual(['assets', 'cover.png', 'readme.md'])
    expect(expanded[1].depth).toBe(1)
  })

  it('keyPrefix 会加在每行 key 前面', () => {
    const root = buildFileNodes([], [{ name: 'a.txt', path: 'a.txt' }])
    expect(flattenFileTree(root, () => false, 0, 'ws-1:')[0].key).toBe('ws-1:a.txt')
  })
})