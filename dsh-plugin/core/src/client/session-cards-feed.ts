/**
 * 同会话共享的任务卡读取与格式化模块（供 TaskReceipt 与兼容组件 TaskStrip 共用）。
 * - 同会话所有订阅者共享一个 3 秒轮询定时器与同一份快照，最后一个订阅离开立即停表；
 * - 严格保留乱序 seq 守卫、代际隔离、cardOfSession/sessionCards 双失败独立记账、确认建卡立即刷新；
 * - 绝不使用模块级全局单例，由插件 apply 内创建 registry 并随 ui.effect 统一销毁。
 */
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import { splitAcceptanceCriteria } from '../revision.ts'
import type { CardRow, DispatcherState } from '../types.ts'
import { parseStrictDateParts } from '../work-duration.ts'
import { subscribeSessionRefresh } from './session-dock-sync.ts'

export type TaskCardSummaryRow = Pick<CardRow, 'id' | 'title' | 'phaseCode' | 'phaseLabel' | 'needsYou'>
  & Partial<Pick<CardRow, 'flow' | 'createdAt' | 'requirement' | 'kind' | 'state' | 'dispatcher' | 'isRunningNow'>>

export interface SessionCardsFeedPorts {
  readonly sessionCards: (sessionId: string) => Promise<RemoteResult<readonly TaskCardSummaryRow[]>>
  readonly cardOfSession: (sessionId: string) => Promise<RemoteResult<TaskCardSummaryRow | null>>
  readonly dispatcherState?: () => Promise<RemoteResult<DispatcherState>>
  readonly status?: () => Promise<RemoteResult<{ readonly project: string }>>
}

export interface SessionCardsFeedSnapshot {
  readonly rows: readonly TaskCardSummaryRow[]
  readonly self: TaskCardSummaryRow | null
  readonly dispatcherLabel: string
  readonly project: string
  readonly selfFailed: boolean
  readonly rowsFailed: boolean
}

export interface SessionCardsFeed {
  getSnapshot(): SessionCardsFeedSnapshot
  subscribe(listener: () => void): () => void
  refresh(): void
  dispose(): void
}

export interface SessionCardsFeedRegistry {
  forSession(sessionId: string): SessionCardsFeed
  dispose(): void
}

const INITIAL_FEED_SNAPSHOT: SessionCardsFeedSnapshot = {
  rows: [],
  self: null,
  dispatcherLabel: '',
  project: '',
  selfFailed: false,
  rowsFailed: false,
}

export function displayTaskNumber(id: string): string {
  const match = /^CARD-0*(\d+)$/i.exec(id.trim())
  return match?.[1] ?? id
}

export function summarizeRequirementDo(rawDo: string): string {
  const clean = rawDo.trim()
  if (!clean) return ''
  const marker = '\n\n拍板（问号没答的按建议）：'
  const idx = clean.indexOf(marker)
  const mainBody = (idx === -1 ? clean : clean.slice(0, idx)).trim()
  const firstLine = mainBody.split(/\r?\n/).map(line => line.trim()).find(Boolean) ?? mainBody
  return firstLine.length > 72 ? `${firstLine.slice(0, 71)}…` : firstLine
}

export function splitCriteriaItems(doneWhen: string): string[] {
  const items = splitAcceptanceCriteria(doneWhen)
  if (items.length > 0) return items
  const trimmed = doneWhen.trim()
  return trimmed && trimmed !== '（待填）' ? [trimmed] : []
}

export function formatCreatedClock(raw?: string | null): string {
  if (typeof raw !== 'string') return ''
  const trimmed = raw.trim()
  if (!trimmed || parseStrictDateParts(trimmed) === null) return ''
  if (/(?:Z|[+-]\d{2}:?\d{2})$/i.test(trimmed)) {
    const parsed = new Date(trimmed)
    if (Number.isNaN(parsed.getTime())) return ''
    const hh = String(parsed.getHours()).padStart(2, '0')
    const mm = String(parsed.getMinutes()).padStart(2, '0')
    return `${hh}:${mm} 建卡`
  }
  const timeMatch = /[T\s](\d{1,2}):(\d{2})/.exec(trimmed)
  if (!timeMatch?.[1] || !timeMatch[2]) return ''
  return `${timeMatch[1].padStart(2, '0')}:${timeMatch[2]} 建卡`
}

/**
 * 为单个会话创建一个可按订阅引用计数启停轮询的共享数据源。
 */
export function createSessionCardsFeed(
  sessionId: string,
  ports: SessionCardsFeedPorts,
): SessionCardsFeed {
  let snapshot: SessionCardsFeedSnapshot = INITIAL_FEED_SNAPSHOT
  const listeners = new Set<() => void>()
  let timer: ReturnType<typeof setInterval> | null = null
  let unsubscribeRefresh: (() => void) | null = null
  let disposed = false
  let generation = 0
  let seq = 0
  let appliedSelf = 0
  let appliedRows = 0
  let appliedDispatcher = 0
  let appliedProject = 0

  const emit = (next: SessionCardsFeedSnapshot) => {
    if (
      next.rows === snapshot.rows
      && next.self === snapshot.self
      && next.dispatcherLabel === snapshot.dispatcherLabel
      && next.project === snapshot.project
      && next.selfFailed === snapshot.selfFailed
      && next.rowsFailed === snapshot.rowsFailed
    ) {
      return
    }
    snapshot = next
    for (const listener of [...listeners]) listener()
  }

  const load = () => {
    if (disposed || listeners.size === 0) return
    const mine = ++seq
    const gen = generation

    const onSelf = (r: RemoteResult<TaskCardSummaryRow | null> | undefined) => {
      if (disposed || gen !== generation || mine <= appliedSelf) return
      appliedSelf = mine
      if (r === undefined || !r.ok) {
        emit({ ...snapshot, selfFailed: true })
        return
      }
      emit({ ...snapshot, self: r.value, selfFailed: false })
    }

    const onRows = (r: RemoteResult<readonly TaskCardSummaryRow[]> | undefined) => {
      if (disposed || gen !== generation || mine <= appliedRows) return
      appliedRows = mine
      if (r === undefined || !r.ok) {
        emit({ ...snapshot, rowsFailed: true })
        return
      }
      emit({ ...snapshot, rows: r.value, rowsFailed: false })
    }

    // 保持单层 .then 语义，避免引入额外微任务延迟影响假定时器与乱序收敛。
    void ports.cardOfSession(sessionId).then(onSelf, () => { onSelf(undefined) })
    void ports.sessionCards(sessionId).then(onRows, () => { onRows(undefined) })

    if (ports.dispatcherState) {
      void ports.dispatcherState().then((r) => {
        if (disposed || gen !== generation || mine <= appliedDispatcher) return
        appliedDispatcher = mine
        if (!r.ok) return
        const nextLabel = r.value.status === 'ready' ? r.value.label.trim() : ''
        if (nextLabel !== snapshot.dispatcherLabel) {
          emit({ ...snapshot, dispatcherLabel: nextLabel })
        }
      }, () => {})
    }

    if (ports.status) {
      void ports.status().then((r) => {
        if (disposed || gen !== generation || mine <= appliedProject) return
        appliedProject = mine
        if (!r.ok) return
        const nextProject = r.value.project.trim()
        if (nextProject !== snapshot.project) {
          emit({ ...snapshot, project: nextProject })
        }
      }, () => {})
    }
  }

  const start = () => {
    if (disposed || timer !== null) return
    unsubscribeRefresh = subscribeSessionRefresh(sessionId, load)
    timer = setInterval(load, 3000)
    load()
  }

  const stop = () => {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
    if (unsubscribeRefresh !== null) {
      unsubscribeRefresh()
      unsubscribeRefresh = null
    }
    // 停止后提升代际，隔离尚未返回的旧在途回包。
    generation += 1
  }

  return {
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      if (disposed) return () => {}
      const first = listeners.size === 0
      listeners.add(listener)
      if (first) start()
      return () => {
        if (!listeners.delete(listener)) return
        if (listeners.size === 0) stop()
      }
    },
    refresh: load,
    dispose: () => {
      if (disposed) return
      disposed = true
      listeners.clear()
      stop()
    },
  }
}

/**
 * 创建会话级共享 Feed 注册表（在插件 apply 内创建一次，ui.effect 统一销毁）。
 */
export function createSessionCardsFeedRegistry(ports: SessionCardsFeedPorts): SessionCardsFeedRegistry {
  const feeds = new Map<string, SessionCardsFeed>()
  let disposed = false

  return {
    forSession: (sessionId: string) => {
      const existing = feeds.get(sessionId)
      if (existing !== undefined) return existing
      const created = createSessionCardsFeed(sessionId, ports)
      if (!disposed) feeds.set(sessionId, created)
      else created.dispose()
      return created
    },
    dispose: () => {
      if (disposed) return
      disposed = true
      for (const feed of feeds.values()) feed.dispose()
      feeds.clear()
    },
  }
}
