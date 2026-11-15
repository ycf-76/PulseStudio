/**
 * 话题（hashtag）解析与清洗。
 *
 * 采集回来的数据里话题有两路来源，而且两路都不干净：
 * 1. 正文 content_text 末尾常常原样保留一整串话题；
 * 2. 落库的 tags 由正则 `#(\S+?)#` 抓取，只会吃到成对井号之间的片段，
 *    `#标签|话题#` 这种小红书新格式因此被切成「标签|话题|」这类残缺值。
 *
 * 两路直接拼在一起，就会出现截断标签和正文里重复话题混排。
 * 这里把两路来源收敛成一条链：正文重建 → 存盘补全 → 去重 → 去残缺，
 * 所有展示入口共用同一份结果，保证同一个作品在任何地方看到的话题都一致。
 */

/**
 * 话题候选：成对 `#…#`，后面允许紧跟一段文字与 `[话题]#` 类型后缀。
 *
 * 三段式 `#标签# 真名 [话题]#` 里 `#…#` 只是占位，真名在后面的文字段；
 * 因此先试「文字段 + 类型后缀」，匹配不上才是普通的 `#话题#` 写法，
 * 这样 `#每日一词# #英语单词#`、`#甲#中间正文#乙#` 都不会被误吞。
 */
const HASHTAG_TOKEN_RE = /#([^#\s]+)#(?:[ \t\u3000]*([^#\n]{1,40}?)[ \t\u3000]*\[\s*(?:话题|投票)\s*\]#)?/g;
/** 落库时被正则切碎的痕迹：`标签|话题|`、`|话题|`。 */
const TYPE_SUFFIX_RE = /\|?\s*(?:话题|投票)\s*\|?\s*$/;
/** 三段式里的占位标签，正文和后缀都拿不到真名时它的名字不成立。 */
const PLACEHOLDER_LABELS = new Set(['标签', '话题', '#']);

function normalizeTag(raw: string): string {
  const stripped = raw.trim().replace(/^#+/, '')
  const beforeSuffix = stripped.replace(TYPE_SUFFIX_RE, '').trim()
  // 残缺值是「占位词 + 类型后缀」，剩下的前缀本身不成立，不能当成话题
  if (TYPE_SUFFIX_RE.test(stripped) && PLACEHOLDER_LABELS.has(beforeSuffix)) return ''
  return beforeSuffix
}

/** 竖线只出现在被正则切碎的话题名里，留下任何一侧都只是半截垃圾。 */
function isUsableTag(tag: string): boolean {
  return Boolean(tag) && !tag.includes('|') && !PLACEHOLDER_LABELS.has(tag);
}

type HashtagHit = { tag: string; start: number; end: number }

/**
 * 扫描正文里的全部话题及其覆盖区间。
 *
 * 三段式 `#标签# 真名 [话题]#` 的真名取中间文字段，取不到才退回 `#…#` 里的名字；
 * 两者都是占位词时整段丢弃，避免把「标签」当成一个话题展示出来。
 */
function scanHashtags(text: string): HashtagHit[] {
  const hits: HashtagHit[] = []
  HASHTAG_TOKEN_RE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = HASHTAG_TOKEN_RE.exec(text)) !== null) {
    const head = normalizeTag(match[1] || '')
    const middle = normalizeTag(match[2] || '')
    const tag = isUsableTag(middle) ? middle : head
    const start = match.index
    const end = start + match[0].length
    if (isUsableTag(tag)) hits.push({ tag, start, end })
    else hits.push({ tag: '', start, end })
  }
  return hits
}

/** 从一段文本里解析出全部话题名。 */
export function extractHashtags(text: string | null | undefined): string[] {
  if (!text) return []
  return scanHashtags(String(text))
    .map((hit) => hit.tag)
    .filter(Boolean)
}

/** 去掉话题片段，只留正文，避免正文与「标签」区重复展示同一串话题。 */
export function stripHashtags(text: string | null | undefined): string {
  if (!text) return ''
  const source = String(text)
  let result = ''
  let cursor = 0
  for (const hit of scanHashtags(source)) {
    result += source.slice(cursor, hit.start)
    cursor = hit.end
  }
  result += source.slice(cursor)
  return result
    .replace(/[\s\u3000]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/**
 * 合并两路来源并去重。
 *
 * 正文里写的是原作者的完整话题，优先级最高；
 * 落库 tags 只在正文没覆盖到时做补全，其中带竖线的残缺值一律丢弃。
 */
export function collectHashtags(
  text: string | null | undefined,
  stored: unknown
): string[] {
  const result: string[] = []
  const seen = new Set<string>()
  const push = (raw: unknown) => {
    if (typeof raw !== 'string') return
    const tag = normalizeTag(raw)
    if (!isUsableTag(tag) || seen.has(tag)) return
    seen.add(tag)
    result.push(tag)
  }

  for (const tag of extractHashtags(text)) push(tag)
  if (Array.isArray(stored)) for (const tag of stored) push(tag)
  else if (typeof stored === 'string') for (const tag of stored.split(/[,\u3001]/)) push(tag)

  return result
}