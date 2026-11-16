/**
 * 第 4 项：任务卡 = 一个单独的 DSH 会话。
 * - 会话名 task-<项目指纹8位>-<卡号>：不同项目的同号卡不会撞；反查时指纹必须是当前项目的（老写法 task-<卡号> 也认）。
 * - 卡号 → 会话名 记在项目的 .collab/dsh会话.json：先到先得，绑定后不覆盖；一个会话只属于一张卡。
 * - 记录文件坏了不覆盖（保住其他卡的记录），读写用目录锁防两个窗口同时改。
 */
import { createHash, randomBytes } from 'node:crypto'
import { mkdirSync, readFileSync, renameSync, rmdirSync, statSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const CARD = '[A-Za-z0-9][A-Za-z0-9-]{0,63}'
const SESSION = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/

export function projectTag(project: string): string {
  return createHash('sha1').update(project).digest('hex').slice(0, 8)
}

export function taskSessionIdFor(project: string, card: string): string {
  return `task-${projectTag(project)}-${card}`
}

/** 从会话名里取卡号和项目指纹；不是任务卡会话名时返回 null。 */
export function parseTaskSession(sessionId: string): { card: string; tag: string } | null {
  const tagged = new RegExp(`^task-([0-9a-f]{8})-(${CARD})$`).exec(sessionId)
  if (tagged) return { tag: tagged[1] ?? '', card: tagged[2] ?? '' }
  const plain = new RegExp(`^task-(${CARD})$`).exec(sessionId)
  return plain ? { tag: '', card: plain[1] ?? '' } : null
}

/** 从会话名里取卡号（不管属于哪个项目）；不是任务卡对话时返回空。 */
export function cardOfSession(sessionId: string): string {
  return parseTaskSession(sessionId)?.card ?? ''
}

/** 记录文件坏了（不是“还没有”）。 */
export class TaskSessionMapCorrupt extends Error {}

function mapFile(project: string): string {
  return join(project, '.collab', 'dsh会话.json')
}

/**
 * 读卡号→会话名。还没有记录返回 {}；记录坏了抛 TaskSessionMapCorrupt（别覆盖它）。
 * 记录文件里写明属于哪个项目（项目指纹）：整个项目文件夹被复制到别处时，这份记录整份不认
 * （DSH 起的 session-UUID 这类会话名不带指纹，不能靠名字判断；复查第 4 项第 3 轮严重）。
 * 老格式（没写项目）只认名字里带当前项目指纹的条目。
 */
export function readTaskSessions(project: string): Record<string, string> {
  let text: string
  try {
    text = readFileSync(mapFile(project), 'utf8')
  } catch (error: unknown) {
    if ((error as { code?: string }).code === 'ENOENT') return {}
    throw new TaskSessionMapCorrupt(`读不了任务对话记录：${String((error as Error).message ?? error)}`)
  }
  let value: unknown
  try { value = JSON.parse(text) } catch (_error) { throw new TaskSessionMapCorrupt('任务对话记录坏了（不是 JSON）') }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new TaskSessionMapCorrupt('任务对话记录坏了（格式不对）')
  const record = value as Record<string, unknown>
  const tagged = 'sessions' in record
  const entries = tagged ? record.sessions : record
  if (entries === null || typeof entries !== 'object' || Array.isArray(entries)) throw new TaskSessionMapCorrupt('任务对话记录坏了（格式不对）')
  if (tagged && typeof record.project !== 'string') throw new TaskSessionMapCorrupt('任务对话记录坏了（没写项目）')
  const out: Record<string, string> = {}
  for (const [card, sid] of Object.entries(entries as Record<string, unknown>)) {
    if (typeof sid !== 'string' || !SESSION.test(sid)) throw new TaskSessionMapCorrupt(`任务对话记录坏了（${card}）`)
    out[card] = sid
  }
  if (tagged) return record.project === projectTag(project) ? out : {}
  return Object.fromEntries(Object.entries(out).filter(([, sid]) => parseTaskSession(sid)?.tag === projectTag(project)))
}

/** 会话名写明属于别的项目（指纹不是当前项目的）。 */
function foreign(project: string, sessionId: string): boolean {
  const named = parseTaskSession(sessionId)
  return named !== null && named.tag !== '' && named.tag !== projectTag(project)
}

/** 只读场景用：坏了就当没有（不写）；记录里属于别的项目的条目一律不认（复查第 4 项第 2 轮严重）。 */
function readLoose(project: string): Record<string, string> {
  let map: Record<string, string>
  try { map = readTaskSessions(project) } catch (_error) { return {} }
  return Object.fromEntries(Object.entries(map).filter(([, sid]) => !foreign(project, sid)))
}

/** 目录锁：mkdir 是原子的；超过 10 秒的旧锁视为上次没收尾，清掉。 */
function withLock<T>(project: string, run: () => T): T {
  const dir = join(project, '.collab')
  mkdirSync(dir, { recursive: true })
  const lock = join(dir, '.dsh会话.lock')
  const deadline = Date.now() + 3000
  for (;;) {
    try {
      mkdirSync(lock)
      break
    } catch (error: unknown) {
      if ((error as { code?: string }).code !== 'EEXIST') throw error
      try { if (Date.now() - statSync(lock).mtimeMs > 10000) rmdirSync(lock) } catch (_gone) { /* 别人刚放掉 */ }
      if (Date.now() > deadline) throw new Error('任务对话记录正被占用，请稍后再试')
      const until = Date.now() + 20
      while (Date.now() < until) { /* 短等 */ }
    }
  }
  try {
    return run()
  } finally {
    try { rmdirSync(lock) } catch (_gone) { /* 已清 */ }
  }
}

export type BindResult =
  | { readonly ok: true; readonly sessionId: string; readonly existed: boolean }
  | { readonly ok: false; readonly reason: string }

/**
 * 记下“这张卡的任务对话是哪个会话”。先到先得：这张卡已经绑过就返回原来那个（不覆盖）。
 * 拒绝：会话名不合规、会话名写明属于别的卡、这个会话已属于别的卡、记录文件坏了。
 */
export function bindTaskSession(project: string, card: string, sessionId: string): BindResult {
  if (!SESSION.test(sessionId)) return { ok: false, reason: '会话名不对' }
  const named = parseTaskSession(sessionId)
  if (named && (named.card !== card || (named.tag && named.tag !== projectTag(project)))) {
    return { ok: false, reason: '这个会话属于别的卡' }
  }
  return withLock(project, () => {
    let map: Record<string, string>
    try { map = readTaskSessions(project) } catch (error: unknown) { return { ok: false, reason: (error as Error).message } }
    // 记录里这张卡指向别的项目的对话（比如复制过来的项目）：不算数，可以重新绑。
    const existing = map[card]
    if (existing && !foreign(project, existing)) return { ok: true, sessionId: existing, existed: true }
    const owner = Object.entries(map).find(([, sid]) => sid === sessionId)
    if (owner) return { ok: false, reason: `这个会话已属于 ${owner[0]}` }
    const next = { project: projectTag(project), sessions: { ...map, [card]: sessionId } }
    const tmp = `${mapFile(project)}.${process.pid}.${randomBytes(4).toString('hex')}.tmp`
    writeFileSync(tmp, JSON.stringify(next, null, 1), 'utf8')
    renameSync(tmp, mapFile(project))
    return { ok: true, sessionId, existed: false }
  })
}

/** 反查：这个会话是当前项目哪张卡的（先查记录，再看会话名；别的项目的会话名不认）。 */
export function cardOfTaskSession(project: string, sessionId: string): string {
  const hit = Object.entries(readLoose(project)).find(([, sid]) => sid === sessionId)
  if (hit) return hit[0]
  const named = parseTaskSession(sessionId)
  if (!named) return ''
  if (named.tag && named.tag !== projectTag(project)) return ''
  return named.card
}

/** 读某张卡记下的会话名（坏了当没有）。 */
export function taskSessionOf(project: string, card: string): string {
  return readLoose(project)[card] ?? ''
}

/** 这个聊天会话里建出的卡（看每张卡的 来源.json 的 conversation）。 */
export function cardsFromConversation(project: string, sessionId: string, cardIds: readonly string[]): string[] {
  return cardIds.filter((card) => {
    try {
      const source = JSON.parse(readFileSync(join(project, '.collab', 'cards', card, '来源.json'), 'utf8')) as { conversation?: string }
      return source.conversation === sessionId
    } catch (_error) {
      return false
    }
  })
}
