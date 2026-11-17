/**
 * 超级玩家独占左栏（页面对齐 1-2）：
 * - 顺序：碗图标 + 超级玩家 + 真实版本号 / AIsoup Lab → 整宽 + 新会话 → 对话 | 任务（橙点只数等你处理） → 列表 → 底部 ⚙ 设置。
 * - 保留会话/任务筛选、导航、改名归档、防重复、首配锁定、真实运行判定（isCardRunningNow / CardRow.isRunningNow）与错误处理。
 */
import { useEffect, useId, useRef, useState, type FormEvent, type ReactElement, type ReactNode, type RefCallback } from 'react'
import type { SessionListState, SessionSummary } from '@deepseek-ai/dsh-api-session-controller/client'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { WorkspaceSnapshot } from '@deepseek-ai/dsh-api-workspace-controller/client'
import {
  Button,
  IconNewChatOutlineMedium,
  IconPanelLeftOutlineRegular,
  IconPlusOutlineRegular,
  IconSettingsOutlineMedium,
  Input,
  SegmentedTabs,
  StateDot,
  useModalLayer,
} from '@deepseek-ai/dsh-client-ui-primitives'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import { FINISHED_CARD_STATES, FINISHED_PHASE_CODES, type CardRow } from '../types.ts'
import { isCardRunningNow } from '../work-duration.ts'
import { SuperagentBrandMark } from './Branding.tsx'
import { formatChatNavigationError } from './chat-navigation.ts'
import { sanitizeProgressText } from './progress-view.ts'
import css from './TaskSidebar.module.css'

type SidebarTab = 'chats' | 'tasks'

/** TaskSidebar 对外公开属性（兼容组件测试及独立 sidebar 槽位接线）。 */
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
  readonly collapseSidebar?: (() => void) | undefined
  readonly drawer?: boolean | undefined
  readonly collapsed?: boolean | undefined
  readonly openSettings?: (() => void) | undefined
  readonly useSetupLocked?: (() => boolean) | undefined
  readonly version?: string | undefined
  readonly settingsPortalSlot?: ReactNode | undefined
  readonly useSectionScroller?: () => { readonly bindScrollerRef: RefCallback<HTMLElement> }
}

/** 顶部新会话替换组件属性（旧 sidebar.session.action 注册已停用，保留组件以维持既有测试与可恢复性）。 */
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

/** 超级玩家独占左栏。 */
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
  const locked = props.useSetupLocked ? props.useSetupLocked() : false

  const cardsFnRef = useRef(props.cards)
  cardsFnRef.current = props.cards
  const taskSessionsFnRef = useRef(props.taskSessions)
  taskSessionsFnRef.current = props.taskSessions
  const sessionListRef = useRef(sessionList)
  sessionListRef.current = sessionList
  const lockedRef = useRef(locked)
  lockedRef.current = locked
  const startChatFnRef = useRef(props.startChat)
  startChatFnRef.current = props.startChat
  const aliveRef = useRef(true)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const tabsId = useId()

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

  const [newChatBusy, setNewChatBusy] = useState(false)
  const newChatBusyRef = useRef(newChatBusy)
  newChatBusyRef.current = newChatBusy
  const [newChatError, setNewChatError] = useState('')
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

  const visibleChats: SessionSummary[] = !locked && hasValidTaskMap
    ? sessionList.ids
      .map(id => sessionList.byId[id])
      .filter((item): item is SessionSummary => {
        if (!item) return false
        if (item.blank) return false
        if (archivedSet.has(item.id)) return false
        if (item.origin === 'subagent' || Boolean(item.parentId)) return false
        if (isTaskSession(item, hasValidTaskMap, boundTaskSessions)) return false
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

  const activeCardId = hasValidTaskMap
    ? cards.find((row) => {
        if (row.archived) return false
        if (!Object.prototype.hasOwnProperty.call(taskMap, row.id)) return false
        const mappedSessionId = taskMap[row.id]
        const sessionId = mappedSessionId ? sessionList.ids.find((id) => id === mappedSessionId) : undefined
        const session = sessionId ? sessionList.byId[sessionId] : undefined
        return (session?.retainedBy?.mainView ?? 0) > 0
      })?.id ?? null
    : null

  const activeTaskSessionId = activeCardId ? taskMap[activeCardId] : undefined
  const resolvedTaskSessionId = activeTaskSessionId
    ? sessionList.ids.find((id) => id === activeTaskSessionId)
    : undefined
  const activeTaskSession = resolvedTaskSessionId ? sessionList.byId[resolvedTaskSessionId] : undefined
  const activeMainSessionId = sessionList.ids.find((id) => (sessionList.byId[id]?.retainedBy?.mainView ?? 0) > 0)
  const activeMainSession = activeMainSessionId ? sessionList.byId[activeMainSessionId] : undefined
  const effectiveMainSession = activeTaskSession ?? activeMainSession
  const activeMainTab: SidebarTab | null = effectiveMainSession
    ? (activeTaskSession || isTaskSession(effectiveMainSession, hasValidTaskMap, boundTaskSessions) ? 'tasks' : 'chats')
    : null
  const activeMainKey = effectiveMainSession && activeMainTab
    ? `${activeMainTab}:${effectiveMainSession.id}`
    : null
  const lastSyncedMainKeyRef = useRef<string | null>(null)

  // 仅当主视图当前激活会话的身份或类型变化时同步左栏标签，避免点击“任务”看总览或手动浏览列表时回弹。
  useEffect(() => {
    if (lastSyncedMainKeyRef.current === activeMainKey) return
    lastSyncedMainKeyRef.current = activeMainKey
    if (activeMainTab) {
      setTab(activeMainTab)
    }
  }, [activeMainKey, activeMainTab])

  const handleNewChat = () => {
    if (!aliveRef.current || newChatBusyRef.current || lockedRef.current) return
    newChatBusyRef.current = true
    setNewChatBusy(true)
    setNewChatError('')
    void startChatFnRef.current()
      .then(() => {
        if (!aliveRef.current) return
        setTab('chats')
      })
      .catch((error: unknown) => {
        if (!aliveRef.current) return
        setNewChatError(formatChatNavigationError(error))
      })
      .finally(() => {
        newChatBusyRef.current = false
        if (aliveRef.current) setNewChatBusy(false)
      })
  }

  // 版本号先藏（用户 11-16：“这个字不需要”）；放回：恢复本行、下面品牌行的版本号和 formatVersionBadge。见 docs/页面对齐改版/已藏清单.md
  // const versionText = formatVersionBadge(props.version)
  const drawerOpen = Boolean(props.wide && props.drawer && !props.collapsed)
  const chatsTabId = `${tabsId}-chats-tab`
  const tasksTabId = `${tabsId}-tasks-tab`
  const chatsPanelId = `${tabsId}-chats-panel`
  const tasksPanelId = `${tabsId}-tasks-panel`

  useModalLayer(rootRef, drawerOpen, () => { props.collapseSidebar?.() })

  if (!props.wide) {
    return (
      <div className={css.railRoot}>
        <div className={css.rail}>
          <Button
            size="sm"
            variant="ghost"
            className={css.railIconBtn ?? ''}
            aria-label="展开侧边栏"
            onClick={() => { props.expandSidebar() }}
          >
            <IconPanelLeftOutlineRegular size={18} />
          </Button>
          <Button
            size="sm"
            variant="outline"
            className={css.newChatRailBtn ?? ''}
            aria-label="新会话"
            disabled={newChatBusy || locked}
            onClick={handleNewChat}
          >
            <IconNewChatOutlineMedium size={16} />
          </Button>
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
            aria-label={waitingCards.length > 0 ? `任务 ${waitingCards.length}` : '任务'}
            onClick={() => {
              setTab('tasks')
              props.showOverview()
              props.expandSidebar()
            }}
          >
            任
          </Button>
        </div>
        <div className={css.railFoot}>
          <Button
            size="sm"
            variant="ghost"
            className={css.railIconBtn ?? ''}
            aria-label="设置"
            onClick={() => { props.openSettings?.() }}
          >
            <IconSettingsOutlineMedium size={18} />
          </Button>
        </div>
        {props.settingsPortalSlot}
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
    const initialSessions = sessionListRef.current
    const wasMainSession =
      initialSessions.ids.includes(sessionId) &&
      (initialSessions.byId[sessionId]?.retainedBy?.mainView ?? 0) > 0
    setBusySessionId(sessionId)
    setChatError(null)
    try {
      const res = await props.archiveSession(sessionId)
      if (!aliveRef.current) return
      if (!res.ok) {
        setChatError(sanitizeProgressText(res.error.message) || '暂时不能归档')
        return
      }
      const latestSessions = sessionListRef.current
      const switchedToOther = latestSessions.ids.some(
        (id) => id !== sessionId && (latestSessions.byId[id]?.retainedBy?.mainView ?? 0) > 0
      )
      // 仅在归档当前主对话且请求期间未切换到其他会话时打开默认对话
      if (wasMainSession && !switchedToOther) {
        handleNewChat()
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

  const nowMs = Date.now()

  return (
    <div
      ref={rootRef}
      className={drawerOpen ? `${css.root} ${css.drawerRoot}` : css.root}
      role={drawerOpen ? 'dialog' : undefined}
      aria-label={drawerOpen ? '侧边栏' : undefined}
      aria-modal={drawerOpen ? 'true' : undefined}
      data-shortcut-modal={drawerOpen ? 'sidebar' : undefined}
      tabIndex={drawerOpen ? -1 : undefined}
    >
      {/* 红绿灯那一行的可拖顶条（DSH 左栏原有，换成我们的左栏后补回）；只盖顶部留白，不盖按钮。 */}
      <div className={css.dragStrip} data-window-drag="" aria-hidden="true" />
      <div className={css.brandArea} data-window-drag="">
        <div className={css.brandRow}>
          <SuperagentBrandMark size={24} />
          <span className={css.brandTitle}>超级玩家</span>
          {/* {versionText ? <span className={css.brandVersion}>{versionText}</span> : null} */}
          <Button
            size="sm"
            variant="ghost"
            className={css.collapseBtn ?? ''}
            aria-label="收起侧边栏"
            onClick={() => { props.collapseSidebar?.() }}
          >
            <IconPanelLeftOutlineRegular size={16} />
          </Button>
        </div>
        <div className={css.brandStudio}>AIsoup Lab</div>
      </div>

      <div className={css.newChatWrap}>
        <Button
          size="md"
          variant="outline"
          className={css.newChatBtn ?? ''}
          aria-label="新会话"
          disabled={newChatBusy || locked}
          onClick={handleNewChat}
        >
          <IconPlusOutlineRegular size={14} />
          <span>{newChatBusy ? '正在新建…' : '新会话'}</span>
        </Button>
        {newChatError ? <div className={css.error}>{newChatError}</div> : null}
      </div>

      <SegmentedTabs<SidebarTab>
        size="sm"
        className={css.segmentedTabs ?? ''}
        label="左栏视图切换"
        value={tab}
        onChange={(nextTab) => {
          setTab(nextTab)
          if (nextTab === 'tasks') {
            props.showOverview()
          }
        }}
        items={[
          {
            value: 'chats',
            id: chatsTabId,
            panelId: chatsPanelId,
            label: '对话',
          },
          {
            value: 'tasks',
            id: tasksTabId,
            panelId: tasksPanelId,
            label: (
              <span className={css.tabLabel}>
                <span>任务</span>
                {waitingCards.length > 0 ? <span className={css.tabBadge}>{waitingCards.length}</span> : null}
              </span>
            ),
          },
        ]}
      />
      {/* 旧自制标签保留注释，改由 DSH SegmentedTabs 承担视图切换与键盘交互：
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
          <span>任务</span>
          {waitingCards.length > 0 ? <span className={css.tabBadge}>{waitingCards.length}</span> : null}
        </button>
      </div>
      */}

      <div ref={scroller?.bindScrollerRef} className={css.scroll}>
        <div
          id={chatsPanelId}
          role="tabpanel"
          aria-labelledby={chatsTabId}
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
            <div className={css.empty}>还没有对话</div>
          ) : (
            visibleChats.map((session) => {
              const label = sessionLabel(session)
              const active = (session.retainedBy?.mainView ?? 0) > 0
              const rowBusy = busySessionId === session.id
              const editing = editingId === session.id
              const timeText = formatChatRelativeTime(session.updatedAt, nowMs)
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
                        {timeText ? <span className={css.rowTime}>{timeText}</span> : null}
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
          id={tasksPanelId}
          role="tabpanel"
          aria-labelledby={tasksTabId}
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
                activeCardId={activeCardId}
                renderTrailing={() => <StateDot state="warning" size={10} />}
                onOpen={handleOpenCard}
              />
              <TaskGroup
                title="进行中"
                rows={ongoingCards}
                openingCard={openingCard}
                activeCardId={activeCardId}
                renderTrailing={(row) => (
                  isTaskCardRunning(row) ? (
                    <StateDot state="ongoing" size={12} />
                  ) : (
                    <span className={css.pausedBadge}>
                      <StateDot state="idle" appearance="step" size={11} />
                      <span className={css.pausedText}>暂停</span>
                    </span>
                  )
                )}
                onOpen={handleOpenCard}
              />
              <TaskGroup
                title="已完成"
                rows={finishedCards}
                openingCard={openingCard}
                activeCardId={activeCardId}
                renderTrailing={(row) => {
                  const dropped = row.state === '已放弃' || row.phaseCode === 'dropped'
                  const timeText = formatCardRelativeTime(row.completedAt ?? row.updated, nowMs)
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

      <div className={css.footer}>
        <Button
          size="md"
          variant="ghost"
          className={css.settingsBtn ?? ''}
          aria-label="设置"
          onClick={() => { props.openSettings?.() }}
        >
          <IconSettingsOutlineMedium size={16} />
          <span>设置</span>
        </Button>
        {props.settingsPortalSlot}
      </div>
    </div>
  )
}

function TaskGroup(props: {
  readonly title: string
  readonly rows: readonly CardRow[]
  readonly openingCard: string | null
  readonly activeCardId: string | null
  readonly renderTrailing?: (row: CardRow) => ReactNode
  readonly onOpen: (cardId: string) => void
}): ReactElement | null {
  if (props.rows.length === 0) return null
  return (
    <div className={css.section}>
      <div className={css.groupTitle}>
        <span>{props.title}</span>
        <span className={css.groupCount}>{props.rows.length}</span>
      </div>
      {props.rows.map((row) => {
        const cardNo = formatCardNumber(row.id)
        const active = props.activeCardId === row.id
        return (
          <div key={row.id} className={`${css.row} ${active ? css.rowActive : ''}`}>
            <button
              type="button"
              className={css.rowMain}
              aria-current={active ? 'page' : undefined}
              disabled={props.openingCard === row.id}
              onClick={() => { props.onOpen(row.id) }}
            >
              <span className={css.cardNumber}>{cardNo}</span>
              <span className={css.rowTitle}>{sanitizeProgressText(row.title) || row.id}</span>
              {props.renderTrailing ? (
                <span className={css.rowTrailing}>{props.renderTrailing(row)}</span>
              ) : null}
            </button>
          </div>
        )
      })}
    </div>
  )
}

function isTaskCardRunning(row: CardRow): boolean {
  if (typeof row.isRunningNow === 'boolean') return row.isRunningNow
  return isCardRunningNow(row.state, row.phaseCode, undefined, row.needsYou)
}

function isTaskSession(
  session: SessionSummary,
  hasValidTaskMap: boolean,
  boundTaskSessions: ReadonlySet<string>,
): boolean {
  if (session.projectionValues?.agentPreset === 'superagent-task') return true
  if (hasValidTaskMap && boundTaskSessions.has(session.id)) return true
  if (TASK_SESSION_NAME_RE.test(session.id)) return true
  return false
}

// 版本号先藏，见上方 versionText 处说明。
// function formatVersionBadge(raw: string | undefined): string {
//   const trimmed = raw?.trim()
//   if (!trimmed) return ''
//   return trimmed.startsWith('v') || trimmed.startsWith('V') ? trimmed : `v${trimmed}`
// }

function formatCardNumber(cardId: string): string {
  const match = /^CARD-0*(\d+)([A-Za-z]*)$/i.exec(cardId.trim())
  if (!match) return cardId
  return `${match[1]}${match[2] ?? ''} 号`
}

function sessionLabel(session: SessionSummary): string {
  const title = session.title?.trim()
  if (title) return title
  const display = session.displayTitle?.trim()
  if (display) return display
  return '未命名对话'
}

function parseEpochMs(raw: unknown): { readonly ms: number; readonly dateOnly: boolean } | null {
  if (raw === null || raw === undefined) return null
  if (typeof raw === 'number') {
    if (!Number.isFinite(raw) || raw <= 0) return null
    return { ms: raw < 1e11 ? raw * 1000 : raw, dateOnly: false }
  }
  if (typeof raw === 'string') {
    const trimmed = raw.trim()
    if (!trimmed) return null
    if (/^\d+(?:\.\d+)?$/.test(trimmed)) {
      const num = Number(trimmed)
      if (!Number.isFinite(num) || num <= 0) return null
      return { ms: num < 1e11 ? num * 1000 : num, dateOnly: false }
    }
    const dateOnly = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(trimmed)
    if (dateOnly) {
      const year = Number(dateOnly[1])
      const month = Number(dateOnly[2])
      const day = Number(dateOnly[3])
      if (year < 2000 || year > 2100 || month < 1 || month > 12 || day < 1 || day > 31) return null
      const localDate = new Date(year, month - 1, day)
      if (localDate.getFullYear() !== year || localDate.getMonth() !== month - 1 || localDate.getDate() !== day) return null
      return { ms: localDate.getTime(), dateOnly: true }
    }
    const parsed = new Date(trimmed)
    if (Number.isNaN(parsed.getTime())) return null
    const year = parsed.getFullYear()
    if (year < 2000 || year > 2100) return null
    return { ms: parsed.getTime(), dateOnly: false }
  }
  return null
}

function calendarDayDiff(targetMs: number, nowMs: number): number {
  const targetDate = new Date(targetMs)
  const nowDate = new Date(nowMs)
  const targetStart = new Date(targetDate.getFullYear(), targetDate.getMonth(), targetDate.getDate()).getTime()
  const nowStart = new Date(nowDate.getFullYear(), nowDate.getMonth(), nowDate.getDate()).getTime()
  return Math.round((nowStart - targetStart) / 86_400_000)
}

function formatChatRelativeTime(raw: unknown, nowMs: number): string {
  const parsed = parseEpochMs(raw)
  if (!parsed) return ''
  const diffMs = Math.max(0, nowMs - parsed.ms)
  if (!parsed.dateOnly) {
    if (diffMs < 60_000) return '刚刚'
    if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)} 分钟`
  }
  const days = calendarDayDiff(parsed.ms, nowMs)
  if (days <= 0 && !parsed.dateOnly) return `${Math.floor(diffMs / 3_600_000)} 小时`
  if (days <= 0) return '今天'
  if (days === 1) return '昨天'
  if (days < 30) return `${days} 天`
  if (days < 365) return `${Math.floor(days / 30)} 个月`
  return `${Math.floor(days / 365)} 年`
}

function formatCardRelativeTime(raw: unknown, nowMs: number): string {
  const parsed = parseEpochMs(raw)
  if (!parsed) return ''
  const days = calendarDayDiff(parsed.ms, nowMs)
  if (days <= 0) return '今天'
  if (days === 1) return '昨天'
  if (days < 30) return `${days} 天`
  const date = new Date(parsed.ms)
  const year = date.getFullYear()
  if (year < 2000 || year > 2100) return ''
  return `${date.getMonth() + 1}月${date.getDate()}日`
}
