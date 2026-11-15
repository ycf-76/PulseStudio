export interface FileEntryLike {
  name: string
  path: string
  size?: number
  is_dir?: boolean
}

export interface FileTreeNode {
  name: string
  path: string
  isDir: boolean
  size: number
  loaded: boolean
  children: FileTreeNode[]
}

export interface FlatFileRow {
  node: FileTreeNode
  depth: number
  key: string
}

export function normalizeRelativePath(path: string): string {
  return path.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '')
}

export function buildFileNodes(dirs: FileEntryLike[] = [], files: FileEntryLike[] = []): FileTreeNode[] {
  const dirNodes: FileTreeNode[] = dirs.map(dir => ({
    name: dir.name,
    path: normalizeRelativePath(dir.path || dir.name),
    isDir: true,
    size: 0,
    loaded: false,
    children: [],
  }))
  const fileNodes: FileTreeNode[] = files.map(file => ({
    name: file.name,
    path: normalizeRelativePath(file.path || file.name),
    isDir: false,
    size: file.size ?? 0,
    loaded: true,
    children: [],
  }))
  const byName = (a: FileTreeNode, b: FileTreeNode) => a.name.localeCompare(b.name, 'zh-CN')
  return [...dirNodes.sort(byName), ...fileNodes.sort(byName)]
}

export function findTreeNode(nodes: FileTreeNode[], path: string): FileTreeNode | null {
  const target = normalizeRelativePath(path)
  for (const node of nodes) {
    if (node.path === target) return node
    if (node.children.length > 0) {
      const found = findTreeNode(node.children, target)
      if (found) return found
    }
  }
  return null
}

export function flattenFileTree(
  nodes: FileTreeNode[],
  isExpanded: (node: FileTreeNode) => boolean,
  depth = 0,
  keyPrefix = '',
): FlatFileRow[] {
  const rows: FlatFileRow[] = []
  for (const node of nodes) {
    rows.push({ node, depth, key: keyPrefix + node.path })
    if (node.isDir && isExpanded(node)) {
      rows.push(...flattenFileTree(node.children, isExpanded, depth + 1, keyPrefix))
    }
  }
  return rows
}