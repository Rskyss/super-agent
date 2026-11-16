/**
 * 左栏“对话 | 任务”导航区域及顶部“新会话”入口（第 7 项）。
 * - 替换 DSH 默认工作区列表：对话页只列普通聊天，任务页按“等你处理 / 进行中 / 已完成”三组展示任务卡。
 * - 顶部新会话通过 sidebar.session.action 替换原入口，确保每次创建全新聊天模式会话，不复用空任务对话。
 */
import { useEffect, useRef, useState, type FormEvent, type ReactElement, type ReactNode, type RefCallback } from 'react'
import type { SessionListState, SessionSummary } from '@deepseek-ai/dsh-api-session-controller/client'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { WorkspaceSnapshot } from '@deepseek-ai/dsh-api-workspace-controller/client'
import { Button, IconNewChatOutlineMedium, Input, StateDot, Tag } from '@deepseek-ai/dsh-client-ui-primitives'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import { FINISHED_CARD_STATES, FINISHED_PHASE_CODES, type CardRow } from '../types.ts'
import { formatChatNavigationError } from './chat-navigation.ts'
import { sanitizeProgressText } from './progress-view.ts'
import css from './TaskSidebar.module.css'

type SidebarTab = 'chats' | 'tasks'

/** TaskSidebar 对外公开属性（与组件测试及 DSH sidebar.workspaces 注册契约保持一致）。 */
export interface TaskSidebarProps {
  readonly cards: () => Promise<RemoteResult<readonly CardRow[]>>
  readonly taskSessions: () => Promise<RemoteResult<Record<string, string>>>
  readonly useSessions: <T>(selector: (snapshot: SessionListState) => T) => T
  readonly useWorkspaces: <T>(selector: (snapshot: WorkspaceSnapshot) => T) => T
  readonly openSession: (id: SessionId) => void
  readonly openTask: (card: string) => Promise<void>
  readonly startChat: () => Promise<void>
  readonly renameSession: (id: SessionId, title: string) => Promise<RemoteResult<unknown>>
  readonly archiveSession: (id: SessionId) => Promise<RemoteResult<unknown>>
  readonly showOverview: () => void
  readonly wide: boolean
  readonly expandSidebar: () => void
  readonly useSectionScroller?: () => { readonly bindScrollerRef: RefCallback<HTMLElement> }
}

/** 顶部新会话替换组件属性（注册在 sidebar.session.action HOC 槽位）。 */
export interface NewChatActionProps {
  readonly wide: boolean
  readonly startChat: () => Promise<void>
  readonly useSetupLocked?: (() => boolean) | undefined
  readonly children?: ReactNode
}

const TASK_SESSION_NAME_RE = /^task-(?:[0-9a-f]{8}-)?[A-Za-z0-9][A-Za-z0-9-]{0,63}$/i

/** 替换 DSH 原生新会话入口，每次直接建立聊天模式新对话，不渲染传入的旧 children。 */
export function NewChatAction({ wide, startChat, useSetupLocked }: NewChatActionProps): ReactElement {
  const [busy, setBusy] = useState(false)
  const [errorText, setErrorText] = useState('')
  const aliveRef = useRef(true)
  const locked = useSetupLocked ? useSetupLocked() : false

  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  const handleClick = () => {
    if (busy || locked) return
    setBusy(true)
    setErrorText('')
    void startChat()
      .catch((error: unknown) => {
        if (!aliveRef.current) return
        setErrorText(formatChatNavigationError(error))
      })
      .finally(() => {
        if (aliveRef.current) setBusy(false)
      })
  }

  return (
    <div className={css.newChatWrap}>
      <Button
        size="sm"
        variant="outline"
        className={(wide ? css.newChatBtn : css.newChatRailBtn) ?? ''}
        aria-label="新会话"
        disabled={busy || locked}
        onClick={handleClick}
      >
        <IconNewChatOutlineMedium size={14} />
        {wide ? <span>{busy ? '正在新建…' : '新会话'}</span> : null}
      </Button>
      {wide && errorText ? <div className={css.error}>{errorText}</div> : null}
    </div>
  )
}

/** 左栏“对话 | 任务”面板。 */
export function TaskSidebar(props: TaskSidebarProps): ReactElement {
  const sessionList = props.useSessions(snapshot => ({
    phase: snapshot.phase,
    ids: snapshot.ids,
    byId: snapshot.byId,
  }))
  const workspaceInfo = props.useWorkspaces(snapshot => ({
    archivedSessionIds: snapshot.archivedSessionIds,
  }))
  const scroller = props.useSectionScroller ? props.useSectionScroller() : null

  const cardsFnRef = useRef(props.cards)
  cardsFnRef.current = props.cards
  const taskSessionsFnRef = useRef(props.taskSessions)
  taskSessionsFnRef.current = props.taskSessions
  const aliveRef = useRef(true)

  const [tab, setTab] = useState<SidebarTab>('chats')
  const [cards, setCards] = useState<readonly CardRow[]>([])
  const [hasValidCards, setHasValidCards] = useState(false)
  const [cardsError, setCardsError] = useState<string | null>(null)

  const [taskMap, setTaskMap] = useState<Record<string, string>>({})
  const [hasValidTaskMap, setHasValidTaskMap] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)

  const [editingId, setEditingId] = useState<SessionId | null>(null)
  const [draftTitle, setDraftTitle] = useState('')
  const [busySessionId, setBusySessionId] = useState<SessionId | null>(null)
  const [chatError, setChatError] = useState<string | null>(null)

  const [openingCard, setOpeningCard] = useState<string | null>(null)
  const [taskError, setTaskError] = useState<string | null>(null)

  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  useEffect(() => {
    let alive = true
    let cardsSeq = 0
    let appliedCardsSeq = 0
    let mapSeq = 0
    let appliedMapSeq = 0

    const refreshCards = async () => {
      const mine = ++cardsSeq
      try {
        const res = await cardsFnRef.current()
        if (!alive || mine <= appliedCardsSeq) return
        appliedCardsSeq = mine
        if (!res.ok) {
          setCardsError(sanitizeProgressText(res.error.message) || '读不到任务列表')
          return
        }
        setCards(Array.isArray(res.value) ? res.value : [])
        setHasValidCards(true)
        setCardsError(null)
      } catch (error: unknown) {
        if (!alive || mine <= appliedCardsSeq) return
        appliedCardsSeq = mine
        const msg = sanitizeProgressText(error instanceof Error ? error.message : String(error))
        setCardsError(msg || '读不到任务列表')
      }
    }

    const refreshMap = async () => {
      const mine = ++mapSeq
      try {
        const res = await taskSessionsFnRef.current()
        if (!alive || mine <= appliedMapSeq) return
        appliedMapSeq = mine
        if (!res.ok || !res.value || typeof res.value !== 'object' || Array.isArray(res.value)) {
          const msg = !res.ok ? sanitizeProgressText(res.error.message) : '任务会话记录格式不对'
          setMapError(msg || '暂时读不到任务会话记录')
          return
        }
        setTaskMap(res.value)
        setHasValidTaskMap(true)
        setMapError(null)
      } catch (error: unknown) {
        if (!alive || mine <= appliedMapSeq) return
        appliedMapSeq = mine
        const msg = sanitizeProgressText(error instanceof Error ? error.message : String(error))
        setMapError(msg || '暂时读不到任务会话记录')
      }
    }

    const pollAll = () => {
      void refreshCards()
      void refreshMap()
    }

    pollAll()
    const timer = setInterval(pollAll, 2500)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  const archivedSet = new Set(workspaceInfo.archivedSessionIds)
  const boundTaskSessions = new Set(Object.values(taskMap))

  const visibleChats: SessionSummary[] = hasValidTaskMap
    ? sessionList.ids
      .map(id => sessionList.byId[id])
      .filter((item): item is SessionSummary => {
        if (!item) return false
        if (archivedSet.has(item.id)) return false
        if (item.origin === 'subagent' || Boolean(item.parentId)) return false
        if (item.projectionValues?.agentPreset === 'superagent-task') return false
        if (boundTaskSessions.has(item.id)) return false
        if (TASK_SESSION_NAME_RE.test(item.id)) return false
        return true
      })
    : []

  const waitingCards: CardRow[] = []
  const ongoingCards: CardRow[] = []
  const finishedCards: CardRow[] = []
  for (const row of cards) {
    if (row.archived) continue
    if (row.needsYou) {
      waitingCards.push(row)
    } else if (FINISHED_PHASE_CODES.has(row.phaseCode) || FINISHED_CARD_STATES.has(row.state)) {
      finishedCards.push(row)
    } else {
      ongoingCards.push(row)
    }
  }

  if (!props.wide) {
    return (
      <div className={css.rail}>
        <Button
          size="sm"
          variant={tab === 'chats' ? 'primary' : 'ghost'}
          aria-label="对话"
          onClick={() => {
            setTab('chats')
            props.expandSidebar()
          }}
        >
          聊
        </Button>
        <Button
          size="sm"
          variant={tab === 'tasks' ? 'primary' : 'ghost'}
          aria-label={`任务 ${waitingCards.length}`}
          onClick={() => {
            setTab('tasks')
            props.showOverview()
            props.expandSidebar()
          }}
        >
          任
        </Button>
      </div>
    )
  }

  const startRename = (session: SessionSummary) => {
    setEditingId(session.id)
    setDraftTitle(sessionLabel(session))
    setChatError(null)
  }

  const cancelRename = () => {
    setEditingId(null)
    setDraftTitle('')
  }

  const submitRename = async (event: FormEvent, sessionId: SessionId) => {
    event.preventDefault()
    if (busySessionId) return
    const nextTitle = draftTitle.trim()
    if (!nextTitle) {
      setChatError('对话名称不能为空')
      return
    }
    setBusySessionId(sessionId)
    setChatError(null)
    try {
      const res = await props.renameSession(sessionId, nextTitle)
      if (!aliveRef.current) return
      if (!res.ok) {
        setChatError(sanitizeProgressText(res.error.message) || '重命名失败，请再试一次')
        return
      }
      setEditingId(null)
      setDraftTitle('')
    } catch (error: unknown) {
      if (!aliveRef.current) return
      setChatError(sanitizeProgressText(error instanceof Error ? error.message : String(error)) || '重命名失败，请再试一次')
    } finally {
      if (aliveRef.current) setBusySessionId(null)
    }
  }

  const handleArchive = async (sessionId: SessionId) => {
    if (busySessionId) return
    setBusySessionId(sessionId)
    setChatError(null)
    try {
      const res = await props.archiveSession(sessionId)
      if (!aliveRef.current) return
      if (!res.ok) {
        setChatError(sanitizeProgressText(res.error.message) || '暂时不能归档')
      }
    } catch (error: unknown) {
      if (!aliveRef.current) return
      setChatError(sanitizeProgressText(error instanceof Error ? error.message : String(error)) || '暂时不能归档')
    } finally {
      if (aliveRef.current) setBusySessionId(null)
    }
  }

  const handleOpenCard = (cardId: string) => {
    if (openingCard === cardId) return
    setOpeningCard(cardId)
    setTaskError(null)
    void props.openTask(cardId)
      .catch((error: unknown) => {
        if (!aliveRef.current) return
        const msg = sanitizeProgressText(error instanceof Error ? error.message : String(error))
        setTaskError(`打不开任务对话：${msg || '请检查当前项目设置'}`)
      })
      .finally(() => {
        if (aliveRef.current) setOpeningCard(null)
      })
  }

  return (
    <div className={css.root}>
      <div className={css.tabs} role="tablist" aria-label="左栏视图切换">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'chats'}
          className={`${css.tab} ${tab === 'chats' ? css.tabActive : ''}`}
          onClick={() => { setTab('chats') }}
        >
          对话
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'tasks'}
          className={`${css.tab} ${tab === 'tasks' ? css.tabActive : ''}`}
          onClick={() => {
            setTab('tasks')
            props.showOverview()
          }}
        >
          {`任务 ${waitingCards.length}`}
        </button>
      </div>

      <div ref={scroller?.bindScrollerRef} className={css.scroll}>
        <div
          role="tabpanel"
          aria-label="对话列表"
          hidden={tab !== 'chats'}
          style={tab !== 'chats' ? { display: 'none' } : undefined}
          className={css.section}
        >
          {chatError ? <div className={css.error}>{chatError}</div> : null}
          {mapError && !hasValidTaskMap ? (
            <div className={css.error}>{`暂不显示普通对话：${mapError}`}</div>
          ) : !hasValidTaskMap ? (
            <div className={css.empty}>正在读取对话列表…</div>
          ) : visibleChats.length === 0 ? (
            <div className={css.empty}>还没有普通对话，点顶部“新会话”开始聊吧</div>
          ) : (
            visibleChats.map((session) => {
              const label = sessionLabel(session)
              const active = (session.retainedBy?.mainView ?? 0) > 0
              const rowBusy = busySessionId === session.id
              const editing = editingId === session.id
              return (
                <div key={session.id} className={`${css.row} ${active ? css.rowActive : ''}`}>
                  {editing ? (
                    <form className={css.renameForm} onSubmit={(e) => { void submitRename(e, session.id) }}>
                      <Input
                        aria-label="对话名称"
                        className={css.renameInput ?? ''}
                        value={draftTitle}
                        disabled={rowBusy}
                        onChange={(e) => { setDraftTitle(e.target.value) }}
                      />
                      <Button size="sm" variant="primary" type="submit" disabled={rowBusy}>保存</Button>
                      <Button size="sm" variant="ghost" type="button" disabled={rowBusy} onClick={cancelRename}>取消</Button>
                    </form>
                  ) : (
                    <>
                      <button
                        type="button"
                        className={css.rowMain}
                        aria-current={active ? 'page' : undefined}
                        onClick={() => { props.openSession(session.id) }}
                      >
                        {session.running ? <StateDot state="ongoing" size={12} /> : null}
                        <span className={css.rowTitle}>{label}</span>
                      </button>
                      <div className={css.rowActions}>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`重命名 ${label}`}
                          disabled={rowBusy}
                          onClick={() => { startRename(session) }}
                        >
                          改名
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`归档 ${label}`}
                          disabled={rowBusy}
                          onClick={() => { void handleArchive(session.id) }}
                        >
                          归档
                        </Button>
                      </div>
                    </>
                  )}
                </div>
              )
            })
          )}
        </div>

        <div
          role="tabpanel"
          aria-label="任务列表"
          hidden={tab !== 'tasks'}
          style={tab !== 'tasks' ? { display: 'none' } : undefined}
          className={css.section}
        >
          {cardsError ? <div className={css.error}>{`读不到最新任务：${cardsError}`}</div> : null}
          {taskError ? <div className={css.error}>{taskError}</div> : null}
          {!hasValidCards ? (
            cardsError ? null : <div className={css.empty}>正在读取任务…</div>
          ) : cards.length === 0 ? (
            cardsError ? null : <div className={css.empty}>还没有任务</div>
          ) : (
            <>
              <TaskGroup
                title="等你处理"
                rows={waitingCards}
                openingCard={openingCard}
                renderLeading={() => <StateDot state="warning" size={10} />}
                onOpen={handleOpenCard}
              />
              <TaskGroup
                title="进行中"
                rows={ongoingCards}
                openingCard={openingCard}
                renderLeading={() => <StateDot state="ongoing" size={12} />}
                onOpen={handleOpenCard}
              />
              <TaskGroup
                title="已完成"
                rows={finishedCards}
                openingCard={openingCard}
                renderTrailing={(row) => {
                  const dropped = row.state === '已放弃' || row.phaseCode === 'dropped'
                  const timeText = formatCardTime(row.updated)
                  if (!dropped && !timeText) return null
                  return (
                    <>
                      {dropped ? <span className={css.droppedBadge}>不做了</span> : null}
                      {timeText ? <span className={css.meta}>{timeText}</span> : null}
                    </>
                  )
                }}
                onOpen={handleOpenCard}
              />
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function TaskGroup(props: {
  readonly title: string
  readonly rows: readonly CardRow[]
  readonly openingCard: string | null
  readonly renderLeading?: (row: CardRow) => ReactNode
  readonly renderTrailing?: (row: CardRow) => ReactNode
  readonly onOpen: (cardId: string) => void
}): ReactElement {
  return (
    <div className={css.section}>
      <div className={css.groupTitle}>{props.title}</div>
      {props.rows.length === 0 ? (
        <div className={css.empty}>暂无</div>
      ) : (
        props.rows.map(row => (
          <div key={row.id} className={css.row}>
            <button
              type="button"
              className={css.rowMain}
              disabled={props.openingCard === row.id}
              onClick={() => { props.onOpen(row.id) }}
            >
              {props.renderLeading?.(row)}
              <Tag>{row.id}</Tag>
              <span className={css.rowTitle}>{sanitizeProgressText(row.title) || row.id}</span>
              {props.renderTrailing?.(row)}
            </button>
          </div>
        ))
      )}
    </div>
  )
}

function sessionLabel(session: SessionSummary): string {
  const title = session.title?.trim()
  if (title) return title
  const display = session.displayTitle?.trim()
  if (display) return display
  return '未命名对话'
}

function formatCardTime(raw: unknown): string {
  if (raw === null || raw === undefined) return ''
  let date: Date | null = null
  if (typeof raw === 'number') {
    if (!Number.isFinite(raw) || raw <= 0) return ''
    date = new Date(raw < 1e11 ? raw * 1000 : raw)
  } else if (typeof raw === 'string') {
    const trimmed = raw.trim()
    if (!trimmed) return ''
    if (/^\d+(?:\.\d+)?$/.test(trimmed)) {
      const num = Number(trimmed)
      if (!Number.isFinite(num) || num <= 0) return ''
      date = new Date(num < 1e11 ? num * 1000 : num)
    } else {
      const dateOnly = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(trimmed)
      if (dateOnly) {
        const year = Number(dateOnly[1])
        const month = Number(dateOnly[2])
        const day = Number(dateOnly[3])
        if (year >= 2000 && year <= 2100 && month >= 1 && month <= 12 && day >= 1 && day <= 31) {
          return `${month}月${day}日`
        }
        return ''
      }
      const parsed = new Date(trimmed)
      if (!Number.isNaN(parsed.getTime())) date = parsed
    }
  }
  if (!date || Number.isNaN(date.getTime())) return ''
  const year = date.getFullYear()
  if (year < 2000 || year > 2100) return ''
  const month = date.getMonth() + 1
  const day = date.getDate()
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${month}月${day}日 ${hour}:${minute}`
}
