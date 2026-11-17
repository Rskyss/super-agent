/**
 * 超级玩家第四批任务页核心呈现与插槽包装：
 * - 通过公开 `useProjection('agentPreset')` 区分普通对话与任务会话（`superagent-task`）；
 * - 真实退出 hero 并保持包装组件与 slot component 身份稳定，不重建原编辑器；
 * - 复用 DSH `conversation.session.header`（`SegmentedTabs` 控制对话 / 轨迹）、`ui-jobs` 后台任务控件、
 *   以及 DSH `rightbar` 轨道/拖宽来展示固定七步进度右栏。
 */
import {
  createElement,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ComponentType,
  type ReactElement,
  type ReactNode,
} from 'react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-agent-preset-registry/types'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { ChatSnapshot } from '@deepseek-ai/dsh-client-ui-chat/client'
import type {
  ConversationContentInputProps,
  ConversationSessionHeaderSlotProps,
  ConversationSlotProps,
  ConversationStore,
  ConversationViewsProps,
} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { JobListActionProps } from '@deepseek-ai/dsh-client-ui-jobs/client'
import type { RightbarOwnerProps } from '@deepseek-ai/dsh-client-ui-layout/client'
import { SegmentedTabs, type SegmentedTab } from '@deepseek-ai/dsh-client-ui-primitives'
import type {} from '@deepseek-ai/dsh-client-ui-session/client'
import type {
  FactoryComponentPropsOf,
  PropsRenderSlots,
  PropsRuntime,
  SlotComponent,
  StoredEntry,
} from '@deepseek-ai/dsh-client-ui-slots'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import type { CardPlanPayload, CardRow, CardTrajectory, CardViewPayload, ImagePayload, ViewHandoff } from '../types.ts'
import { SuperagentConversationHeaderSlot } from './ConversationHeader.tsx'
import { isReviewFailureStopView, parsePlanMarkdown } from './decision-model.ts'
import { TASK_COMPOSER_FOOTER_SLOT } from './HomeChrome.tsx'
import { ProgressPanel } from './ProgressPanel.tsx'
import { formatTaskDisplayStatus, sanitizeProgressText } from './progress-view.ts'
import { displayTaskNumber, type SessionCardsFeedRegistry } from './session-cards-feed.ts'
import { TaskTrajectory } from './TaskTrajectory.tsx'
import css from './TaskPageChrome.module.css'

export const TASK_PRESET = 'superagent-task'
export const TASK_CHAT_VIEW_ID = 'chat'
export const TASK_TRAJECTORY_VIEW_ID = 'superagent-trajectory'

const TASK_HEADER_ALIAS = 'superagent.conversation.header'
const TASK_SESSION_HEADER_ALIAS = 'superagent.session.header'
const TASK_SESSION_JOBS_ALIAS = 'superagent.session.header.jobs'
const TASK_RIGHTBAR_SESSION_ALIAS = 'superagent.rightbar.session'
const TASK_SESSION_VIEW_FACTORY = 'superagent.session.view'
const HANDOFF_POLL_MS = 2000

const KEY_TIMELINE_KINDS = new Set([
  // 第七批 7-1：跑着时确认的修改——排队 / 生效 / 没生效都要让你看到（复查中等 7）
  '改要求排队',
  '改要求生效',
  '改要求没生效',
  '需求',
  '写方案',
  '方案',
  '批准',
  '开工',
  '开跑',
  '回复',
  '判定',
  '页面检查',
  '全量',
  '追加要求',
  '退回',
  '转人工',
  '停下',
  '结束',
  '合并',
  '放弃',
])

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface SlotMap {
    /** 超级玩家对话头部新别名（替代 `main.conversation` 内的 `conversation.header` 转发）。 */
    'superagent.conversation.header': {
      kind: 'single'
      scope: 'session-maybe'
    }
    /** 超级玩家任务会话头部新别名（复制原 `conversation.session.header` 的 store/inject/locale）。 */
    'superagent.session.header': {
      kind: 'single'
      scope: 'session'
      owner: {
        hideChrome: boolean
      }
    }
    /** 超级玩家任务头部后台任务控件子槽别名（镜像原 `ui-jobs` `job-list`）。 */
    'superagent.session.header.jobs': {
      kind: 'list'
      scope: 'session'
    }
    /** 超级玩家固定右栏的 session-maybe 子槽别名（由框架注入当前会话与 projection）。 */
    'superagent.rightbar.session': {
      kind: 'single'
      scope: 'session-maybe'
      owner: RightbarOwnerProps & {
        readonly isChatPanel: boolean
      }
    }
  }

  interface SlotFactoryMap {
    /** 超级玩家任务会话视图选择工厂：共享 `conversation.session` 的 store handle 读取当前 `view`。 */
    'superagent.session.view': {
      scope: 'session'
      store: ConversationStore
      props: {
        readonly isTask: boolean
        readonly renderNative: (owner: { view?: string }) => ReactNode
        readonly timeline?: ReactNode
      }
    }
  }
}

export type TaskHeaderViewId = typeof TASK_CHAT_VIEW_ID | typeof TASK_TRAJECTORY_VIEW_ID

export interface TaskPageHeaderTask {
  readonly id: string
  readonly title: string
  readonly phaseCode?: string | undefined
  readonly phaseLabel?: string | undefined
  readonly state?: string | undefined
  readonly isRunningNow?: boolean | undefined
}

export interface TaskPageHeaderProps {
  readonly task: TaskPageHeaderTask | null
  readonly dispatcherLabel?: string | undefined
  readonly view: string | null | undefined
  readonly selectView: (view: string) => void
  readonly jobs?: ReactNode
}

/**
 * 为 `conversation.content` factory 构造输入：任务会话退出 hero 并将 hero 阶段转为 active，
 * 保留 `settling` 且不修改原输入对象；非任务会话原样返回。
 */
export function taskConversationContentInput(
  input: ConversationContentInputProps,
  isTask: boolean,
): ConversationContentInputProps {
  if (!isTask) return input
  return {
    ...input,
    hero: false,
    phase: input.phase === 'hero' ? 'active' : input.phase,
  }
}

/**
 * 任务页顶部主行 + 原生“对话 / 轨迹”双标签纯展示组件（无业务副作用）。
 */
export function TaskPageHeader({
  task,
  dispatcherLabel,
  view,
  selectView,
  jobs,
}: TaskPageHeaderProps): ReactElement {
  const baseId = useId()
  const taskNumber = task?.id ? displayTaskNumber(task.id) : ''
  const titleText = task
    ? taskNumber
      ? (task.title.trim() ? `${taskNumber} 号：${task.title}` : `${taskNumber} 号`)
      : (task.title.trim() || '任务')
    : '任务'
  const cleanDispatcher = dispatcherLabel?.trim() ?? ''
  const whoLabel = cleanDispatcher && cleanDispatcher !== '调度员'
    ? `调度员 ${cleanDispatcher}`
    : '调度员'
  const statusText = formatTaskDisplayStatus(task)
  const subtitle = statusText ? `${whoLabel} · ${statusText}` : whoLabel
  const activeTab: TaskHeaderViewId = view === TASK_TRAJECTORY_VIEW_ID
    ? TASK_TRAJECTORY_VIEW_ID
    : TASK_CHAT_VIEW_ID

  const tabItems: [SegmentedTab<TaskHeaderViewId>, SegmentedTab<TaskHeaderViewId>] = [
    {
      value: TASK_CHAT_VIEW_ID,
      label: '对话',
      id: `${baseId}-tab-chat`,
      panelId: `${baseId}-panel-chat`,
    },
    {
      value: TASK_TRAJECTORY_VIEW_ID,
      label: '轨迹',
      id: `${baseId}-tab-trajectory`,
      panelId: `${baseId}-panel-trajectory`,
    },
  ]

  return (
    <header className={css.header}>
      <div className={css.headerMainRow}>
        <div className={css.headerTitleCluster}>
          <span className={css.headerTitle} title={titleText}>{titleText}</span>
          <span className={css.headerStatus}>{subtitle}</span>
        </div>
        {jobs ? <div className={css.headerJobs}>{jobs}</div> : null}
      </div>
      <div className={css.headerTabsRow}>
        <SegmentedTabs<TaskHeaderViewId>
          items={tabItems}
          value={activeTab}
          onChange={selectView}
          label="任务视图"
          size="sm"
          className={css.segmentedTabs}
        />
      </div>
    </header>
  )
}

interface AssistantBlockLike {
  readonly kind?: unknown
  readonly text?: unknown
}

interface LegacyNodeLike {
  readonly kind?: unknown
  readonly blocks?: readonly AssistantBlockLike[] | undefined
}

export interface AssistantTextSnapshotLike {
  readonly legacy?: {
    readonly nodes?: readonly LegacyNodeLike[] | undefined
    readonly partial?: {
      readonly blocks?: readonly AssistantBlockLike[] | undefined
    } | null | undefined
  } | undefined
}

function countCodePoints(text: string): number {
  let count = 0
  for (const _cp of text) {
    count += 1
  }
  return count
}

function countBlocksCharacters(blocks: readonly AssistantBlockLike[] | undefined): number {
  if (!Array.isArray(blocks)) return 0
  let total = 0
  for (const block of blocks) {
    if (!block || block.kind !== 'text' || typeof block.text !== 'string') continue
    total += countCodePoints(block.text)
  }
  return total
}

/**
 * 仅统计当前已加载 `ChatSnapshot.legacy.nodes` 中助手正文 `text` 块与 `partial.blocks` 的 Unicode 码点数，
 * 排除用户消息、推理与工具块；不从 tokens 换算，也不冒充未加载历史总字数。
 */
export function countAssistantTextCharacters(
  snapshot: ChatSnapshot | AssistantTextSnapshotLike | null | undefined,
): number {
  const legacy = snapshot?.legacy
  if (!legacy || typeof legacy !== 'object') return 0
  let total = 0
  if (Array.isArray(legacy.nodes)) {
    for (const node of legacy.nodes) {
      if (!node || node.kind !== 'assistant') continue
      total += countBlocksCharacters(node.blocks)
    }
  }
  if (legacy.partial && typeof legacy.partial === 'object') {
    total += countBlocksCharacters(legacy.partial.blocks)
  }
  return total
}

export interface TaskJobRowLike {
  readonly kind?: unknown
  readonly status?: unknown
}

/**
 * 为原生后台任务控件生成带类型标识的计数文案：仅当当前会话存在活跃中的预览任务时追加“ · 预览”。
 */
export function formatTaskJobsLabel(
  label: string,
  rows: readonly TaskJobRowLike[] | undefined,
): string {
  if (!Array.isArray(rows) || rows.length === 0) return label
  const hasActivePreview = rows.some(
    row => row?.kind === 'preview' && (row.status === 'running' || row.status === 'stopping'),
  )
  if (!hasActivePreview) return label
  return label.endsWith(' · 预览') ? label : `${label} · 预览`
}

const EMPTY_PLAN_QUESTION_RE = /^(?:无|没有|暂无|不需要|不用确认|无需确认|无问题)[。！!.]*$/
const ANNOUNCEMENT_STOP_PHASES = new Set(['blocked', 'interrupted', 'start_failed', 'plan_failed', 'copy_missing'])

function countPlanQuestions(plan: Pick<CardPlanPayload, 'plan'> | null | undefined): number {
  const markdown = typeof plan?.plan === 'string' ? plan.plan : ''
  if (!markdown.trim()) return 0
  const { questions } = parsePlanMarkdown(markdown)
  const clean = questions.trim()
  if (!clean || EMPTY_PLAN_QUESTION_RE.test(clean)) return 0
  const lines = clean
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(line => line.length > 0 && !/^---+$/.test(line))
  if (lines.length === 0) return 0
  const bulletLines = lines.filter(line => /^(?:\d+[.、)\]]|[-*•]|[①-⑩])\s*/.test(line))
  if (bulletLines.length > 0) return bulletLines.length
  const primaryLines = lines.filter(line => !/^[（(]?建议[：:]/.test(line))
  return primaryLines.length > 0 ? primaryLines.length : 1
}

/**
 * 生成任务页中间时间线之后、对话正文之前展示的独立阶段说明文案（04a / 14 / planning）。
 */
export function getTaskPhaseAnnouncement(
  payload: CardViewPayload | null | undefined,
  plan?: Pick<CardPlanPayload, 'plan'> | null | undefined,
): string {
  if (!payload || typeof payload !== 'object') return ''
  const phase = payload.card?.phase
  const phaseCode = String(phase?.code ?? '').trim()
  if (phaseCode === 'planning') {
    return '调度员正在写方案，好了我叫你。'
  }
  if (phaseCode === 'plan_ready') {
    const count = countPlanQuestions(plan)
    if (count > 0) {
      return `调度员把方案写好了，有 ${count} 个问题要你答，我标了建议。你批了才开工。`
    }
    return '调度员把方案写好了，你批了才开工。'
  }
  if (ANNOUNCEMENT_STOP_PHASES.has(phaseCode)) {
    if (isReviewFailureStopView(payload)) {
      const rounds = Array.isArray(payload.stopReviews) ? payload.stopReviews.length : 0
      if (rounds > 0) {
        return `复查 ${rounds} 轮都没通过，我先停下来了，详情在下面。`
      }
    }
    const rawReason = String(
      phase?.issues ?? phase?.detail ?? payload.now?.text ?? payload.card?.state ?? '',
    ).trim()
    const cleanReason = rawReason ? sanitizeProgressText(rawReason) : ''
    return cleanReason || '当前进度已停下来，详情在下面。'
  }
  return ''
}

const MAX_TIMELINE_HANDOFFS = 3

/**
 * 为任务页中间时间线挑选展示的关键进展：
 * - 先按既有 KEY_TIMELINE_KINDS 与非空摘要过滤，不改动原数组；
 * - 当前处于 blocked 且确为复查失败停下时，优先取最近最多 3 条复查员回复；
 * - 其他阶段或没有明确复查事件时，取已过滤最新最多 3 条。
 */
export function selectTaskKeyHandoffs(
  payload: CardViewPayload | null | undefined,
): readonly ViewHandoff[] {
  if (!payload || typeof payload !== 'object') return []
  const rawList = Array.isArray(payload.handoffs) ? payload.handoffs : []
  const filtered = rawList.filter((item): item is ViewHandoff => {
    if (!item || typeof item !== 'object') return false
    const summary = String(item.summary ?? '').trim()
    if (!summary) return false
    return !item.kind || KEY_TIMELINE_KINDS.has(item.kind)
  })
  if (filtered.length === 0) return []
  const phaseCode = String(payload.card?.phase?.code ?? '').trim()
  if (phaseCode === 'blocked' && isReviewFailureStopView(payload)) {
    const reviewReplies = filtered.filter((item) => (
      item.kind === '回复' && String(item.from ?? '').trim().startsWith('复查员')
    ))
    if (reviewReplies.length > 0) {
      const stopReviewCount = Array.isArray(payload.stopReviews) ? payload.stopReviews.length : 0
      const reviewLimit = stopReviewCount > 0
        ? Math.min(stopReviewCount, MAX_TIMELINE_HANDOFFS)
        : 1
      return reviewReplies.slice(-reviewLimit)
    }
  }
  return filtered.slice(-MAX_TIMELINE_HANDOFFS)
}

export interface TaskPageChromePorts {
  readonly feedRegistry: SessionCardsFeedRegistry
  readonly cardOfSession: (sessionId: SessionId) => Promise<RemoteResult<CardRow | null>>
  readonly view: (card: string) => Promise<RemoteResult<string>>
  readonly plan?: ((card: string) => Promise<RemoteResult<string>>) | undefined
  readonly trajectory: (card: string) => Promise<CardTrajectory>
  readonly cardAttachment: (card: string, name: string) => Promise<ImagePayload>
  readonly openRightbar: (track: boolean, fullscreen: boolean) => void
  readonly closeRightbar: () => void
}

interface IsolatedSlotsApi {
  entries(name: string): readonly StoredEntry[]
  subscribe(name: string, cb: () => void): () => void
  inject(name: string, factory: () => () => void): () => void
  register(options: Record<string, unknown>, component: unknown): () => void
}

function pickLowestPriority(entries: readonly StoredEntry[]): StoredEntry | undefined {
  let best: StoredEntry | undefined
  let bestPriority = Infinity
  for (const entry of entries) {
    const priority = entry.options?.priority ?? 0
    if (best === undefined || priority < bestPriority) {
      best = entry
      bestPriority = priority
    }
  }
  return best
}

function cloneSlotOptions(
  name: string,
  source: StoredEntry,
  overrides?: {
    priority?: number
    children?: Record<string, unknown>
  },
): Record<string, unknown> {
  const cloned: Record<string, unknown> = { name }
  const opts = source.options
  if (opts !== undefined) {
    if (opts.id !== undefined) cloned.id = opts.id
    if (opts.order !== undefined) cloned.order = opts.order
    if (opts.label !== undefined) cloned.label = opts.label
    if (opts.key !== undefined) cloned.key = opts.key
    if (opts.priority !== undefined) cloned.priority = opts.priority
  }
  if (overrides?.priority !== undefined) {
    cloned.priority = overrides.priority
  }
  if (overrides?.children !== undefined) {
    cloned.children = overrides.children
  }
  if (source.store !== undefined) {
    cloned.store = source.store
  }
  if (source.inject !== undefined) {
    cloned.inject = source.inject
  }
  if (source.locale !== undefined) {
    cloned.locale = source.locale
  }
  return cloned
}

function injectSlotRegistration(
  api: IsolatedSlotsApi,
  slotName: string,
  options: Record<string, unknown>,
  component: unknown,
): () => void {
  let cancelled = false
  let disposeEntry: (() => void) | undefined
  const disposeInject = api.inject(slotName, () => {
    if (cancelled) return () => {}
    const cleanup = api.register(options, component)
    disposeEntry = cleanup
    return cleanup
  })
  return () => {
    if (cancelled) return
    cancelled = true
    disposeInject?.()
    disposeEntry?.()
    disposeEntry = undefined
  }
}

function formatHandoffSummary(item: ViewHandoff): string {
  const summary = sanitizeProgressText(String(item.summary ?? '').trim())
  const from = sanitizeProgressText(String(item.from ?? '').trim())
  if (!summary) return ''
  if (from && !summary.startsWith(from) && item.kind === '回复') {
    return `${from}：${summary}`
  }
  return summary
}

function formatHandoffTime(raw: unknown): string {
  if (typeof raw !== 'string') return ''
  const trimmed = raw.trim()
  return /^\d{2}:\d{2}:\d{2}$/.test(trimmed) ? trimmed : ''
}

interface TimelineProps {
  readonly sessionId: SessionId
  readonly feedRegistry: SessionCardsFeedRegistry
  readonly view: (card: string) => Promise<RemoteResult<string>>
  readonly plan?: ((card: string) => Promise<RemoteResult<string>>) | undefined
}

function TaskHandoffTimeline({ sessionId, feedRegistry, view, plan }: TimelineProps): ReactElement | null {
  const feed = useMemo(() => feedRegistry.forSession(sessionId), [feedRegistry, sessionId])
  const feedSnapshot = useSyncExternalStore(
    useCallback((cb: () => void) => feed.subscribe(cb), [feed]),
    useCallback(() => feed.getSnapshot(), [feed]),
  )
  const cardId = feedSnapshot.self?.id ?? null
  const viewRef = useRef(view)
  viewRef.current = view
  const planRef = useRef(plan)
  planRef.current = plan

  const [timelineState, setTimelineState] = useState<{
    readonly cardId: string | null
    readonly items: readonly ViewHandoff[]
    readonly announcement: string
  }>({ cardId: null, items: [], announcement: '' })

  useEffect(() => {
    if (!cardId) {
      setTimelineState({ cardId: null, items: [], announcement: '' })
      return
    }
    let alive = true
    let seq = 0
    let applied = 0
    const targetCard = cardId

    setTimelineState(prev => (
      prev.cardId === targetCard
        ? prev
        : { cardId: targetCard, items: [], announcement: '' }
    ))

    const load = async () => {
      const mine = ++seq
      try {
        const res = await viewRef.current(targetCard)
        if (!alive || mine <= applied || !res.ok) return
        const parsed = JSON.parse(res.value) as CardViewPayload
        const filtered = selectTaskKeyHandoffs(parsed)
        let planPayload: CardPlanPayload | null = null
        if (parsed.card?.phase?.code === 'plan_ready' && planRef.current) {
          try {
            const planRes = await planRef.current(targetCard)
            if (!alive || mine <= applied) return
            if (planRes.ok) {
              planPayload = JSON.parse(planRes.value) as CardPlanPayload
            }
          } catch {
            planPayload = null
          }
        }
        if (!alive || mine <= applied) return
        applied = mine
        const announcement = getTaskPhaseAnnouncement(parsed, planPayload)
        setTimelineState({ cardId: targetCard, items: filtered, announcement })
      } catch {
        // 保持既有时间线与说明，等待下次刷新。
      }
    }

    void load()
    const timer = setInterval(() => { void load() }, HANDOFF_POLL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [cardId])

  const hasItems = timelineState.items.length > 0
  const hasAnnouncement = timelineState.announcement.length > 0
  if (!cardId || timelineState.cardId !== cardId || (!hasItems && !hasAnnouncement)) {
    return null
  }

  return (
    <>
      {hasItems ? (
        <div className={css.timeline} aria-label="关键进展时间线">
          {timelineState.items.map((item, idx) => {
            const text = formatHandoffSummary(item)
            if (!text) return null
            const time = formatHandoffTime(item.time)
            const key = item.seq !== undefined ? `${item.seq}-${idx}` : `h-${idx}`
            return (
              <div key={key} className={css.timelineRow}>
                {time ? <span className={css.timelineTime}>{time}</span> : null}
                <span className={css.timelineSummary}>{text}</span>
              </div>
            )
          })}
        </div>
      ) : null}
      {hasAnnouncement ? (
        <div className={css.phaseAnnouncement} aria-label="阶段说明">
          {timelineState.announcement}
        </div>
      ) : null}
    </>
  )
}

export interface TaskSessionViewBridgeProps {
  readonly isTask: boolean
  readonly view: string | null | undefined
  readonly renderNative: (owner: { view?: string }) => ReactNode
  readonly renderTrajectory: () => ReactNode
  readonly timeline?: ReactNode
}

/**
 * 任务会话视图桥（纯呈现）：始终在同一位置保留原 Session 容器挂载以维持草稿持久化与
 * 对话节点状态；当任务会话选中自有轨迹视图时，通过 `{ view: 'chat' }` 让原容器继续停留在
 * 聊天视图并隐藏容器，仅额外呈现轨迹视图；普通会话或切回对话时直接呈现原 Session。
 */
export function TaskSessionViewBridge({
  isTask,
  view,
  renderNative,
  renderTrajectory,
  timeline,
}: TaskSessionViewBridgeProps): ReactElement {
  const showTrajectory = isTask && view === TASK_TRAJECTORY_VIEW_ID
  return (
    <>
      {isTask && !showTrajectory ? (timeline ?? null) : null}
      <div className={css.nativeSessionSeat} hidden={showTrajectory}>
        {renderNative(showTrajectory ? { view: TASK_CHAT_VIEW_ID } : {})}
      </div>
      {showTrajectory ? renderTrajectory() : null}
    </>
  )
}

function createTaskSessionViewFactory(
  ports: TaskPageChromePorts,
): SlotComponent<FactoryComponentPropsOf<typeof TASK_SESSION_VIEW_FACTORY>> {
  function TaskSessionViewFactory(
    props: FactoryComponentPropsOf<typeof TASK_SESSION_VIEW_FACTORY>,
  ): ReactElement {
    const view = props.useStore(s => s.view)
    const renderTrajectory = useCallback(
      () => (
        <TaskTrajectory
          sessionId={props.sessionId}
          cardOfSession={async () => {
            const res = await ports.cardOfSession(props.sessionId)
            if (!res.ok) throw new Error(res.error.message || '查询当前会话任务卡失败')
            return res.value ? res.value.id : ''
          }}
          trajectory={ports.trajectory}
          cardAttachment={ports.cardAttachment}
        />
      ),
      [props.sessionId],
    )

    return (
      <TaskSessionViewBridge
        isTask={props.isTask}
        view={view}
        renderNative={props.renderNative}
        renderTrajectory={renderTrajectory}
        timeline={props.timeline}
      />
    )
  }
  return TaskSessionViewFactory
}

function createTaskAwareViews(ports: TaskPageChromePorts): ComponentType<ConversationViewsProps> {
  function TaskAwareConversationViews(props: ConversationViewsProps): ReactElement | null {
    const isTask = props.useProjection('agentPreset') === TASK_PRESET
    const renderSlotRef = useRef(props.renderSlot)
    renderSlotRef.current = props.renderSlot

    const renderNative = useCallback(
      (owner: { view?: string }) => renderSlotRef.current('conversation.session', owner),
      [],
    )

    const timeline = isTask
      ? (
          <TaskHandoffTimeline
            sessionId={props.sessionId}
            feedRegistry={ports.feedRegistry}
            view={ports.view}
            plan={ports.plan}
          />
        )
      : undefined

    return (
      <>
        {props.renderFactorySlot(
          TASK_SESSION_VIEW_FACTORY,
          {
            isTask,
            renderNative,
            timeline,
          },
          {
            fallback: (
              <>
                {timeline ?? null}
                {props.renderSlot('conversation.session', {})}
              </>
            ),
          },
        )}
      </>
    )
  }
  return TaskAwareConversationViews
}

function createMainConversationWrapper(
  OriginalMain: ComponentType<ConversationSlotProps>,
  TaskAwareViews: ComponentType<ConversationViewsProps>,
): ComponentType<ConversationSlotProps> {
  function SuperagentMainConversation(props: ConversationSlotProps): ReactElement {
    const { renderSlot, renderFactorySlot, ...restProps } = props
    const isTask = props.useProjection('agentPreset') === TASK_PRESET

    const isTaskRef = useRef(isTask)
    isTaskRef.current = isTask
    const renderSlotRef = useRef(renderSlot)
    renderSlotRef.current = renderSlot
    const renderFactorySlotRef = useRef(renderFactorySlot)
    renderFactorySlotRef.current = renderFactorySlot

    const adaptedRenderSlot = useCallback<ConversationSlotProps['renderSlot']>(
      (slotName, owner, options) => {
        if (slotName === 'conversation.header') {
          const forward = renderSlotRef.current as unknown as (
            name: string,
            slotOwner: unknown,
            slotOptions?: unknown,
          ) => ReactNode
          return options === undefined
            ? forward(TASK_HEADER_ALIAS, owner)
            : forward(TASK_HEADER_ALIAS, owner, options)
        }
        const forward = renderSlotRef.current as unknown as (
          name: string,
          slotOwner: unknown,
          slotOptions?: unknown,
        ) => ReactNode
        return options === undefined
          ? forward(slotName, owner)
          : forward(slotName, owner, options)
      },
      [],
    )

    const adaptedRenderFactorySlot = useCallback<ConversationSlotProps['renderFactorySlot']>(
      (name, factoryInput, options) => {
        if (name === 'conversation.content') {
          const typedInput = factoryInput as ConversationContentInputProps
          const nextInput = taskConversationContentInput(typedInput, isTaskRef.current)
          const origSlots = options?.slots as Record<string, unknown> | undefined
          const nextOptions = options
            ? {
                ...options,
                slots: {
                  ...origSlots,
                  views: TaskAwareViews,
                },
              }
            : { slots: { views: TaskAwareViews } }
          return renderFactorySlotRef.current(
            name,
            nextInput as never,
            nextOptions as never,
          )
        }
        return renderFactorySlotRef.current(name, factoryInput as never, options as never)
      },
      [],
    )

    const originalProps = {
      ...restProps,
      renderSlot: adaptedRenderSlot,
      renderFactorySlot: adaptedRenderFactorySlot,
    } as unknown as ConversationSlotProps

    return (
      <div
        className={css.mainRoot}
        data-superagent-task-active={isTask ? 'true' : undefined}
      >
        {createElement(OriginalMain, originalProps)}
      </div>
    )
  }
  return SuperagentMainConversation
}

type TaskConversationHeaderProps = PropsRuntime<'superagent.conversation.header'>
  & PropsRenderSlots<typeof TASK_SESSION_HEADER_ALIAS>

function createTaskConversationHeader(ports: TaskPageChromePorts): ComponentType<TaskConversationHeaderProps> {
  function SuperagentTaskConversationHeader(props: TaskConversationHeaderProps): ReactElement | null {
    const isTask = props.useProjection('agentPreset') === TASK_PRESET
    if (!isTask || props.sessionId === undefined) {
      return createElement(SuperagentConversationHeaderSlot, {
        ...props,
        cardOfSession: ports.cardOfSession,
        trajectory: ports.trajectory,
        cardAttachment: ports.cardAttachment,
      })
    }
    return (
      <div className={css.taskHeaderOuter} data-window-drag="">
        {props.renderSlot(TASK_SESSION_HEADER_ALIAS, { hideChrome: false })}
      </div>
    )
  }
  return SuperagentTaskConversationHeader
}

type TaskSessionHeaderSlotProps = Omit<ConversationSessionHeaderSlotProps, 'renderSlot'>
  & PropsRenderSlots<typeof TASK_SESSION_JOBS_ALIAS>

function createTaskSessionHeaderSlot(ports: TaskPageChromePorts): ComponentType<TaskSessionHeaderSlotProps> {
  function SuperagentTaskSessionHeaderSlot(props: TaskSessionHeaderSlotProps): ReactElement {
    const view = props.useStore(s => s.view)
    const feed = useMemo(
      () => ports.feedRegistry.forSession(props.sessionId),
      [props.sessionId],
    )
    const feedSnapshot = useSyncExternalStore(
      useCallback((cb: () => void) => feed.subscribe(cb), [feed]),
      useCallback(() => feed.getSnapshot(), [feed]),
    )
    const summary = props.useSessions(s => s.byId[props.sessionId])
    const fallbackTitle = typeof (summary?.displayTitle || summary?.title) === 'string'
      ? String(summary?.displayTitle || summary?.title)
      : ''
    const task: TaskPageHeaderTask | null = feedSnapshot.self
      ? {
          id: feedSnapshot.self.id,
          title: feedSnapshot.self.title,
          phaseCode: feedSnapshot.self.phaseCode,
          phaseLabel: feedSnapshot.self.phaseLabel,
          state: feedSnapshot.self.state,
          isRunningNow: feedSnapshot.self.isRunningNow,
        }
      : fallbackTitle
        ? { id: '', title: fallbackTitle }
        : null
    const dispatcherLabel = (feedSnapshot.self?.dispatcher ?? '').trim() || feedSnapshot.dispatcherLabel
    const jobs = props.renderSlot(TASK_SESSION_JOBS_ALIAS, {})

    return (
      <TaskPageHeader
        task={task}
        dispatcherLabel={dispatcherLabel}
        view={view}
        selectView={props.selectView}
        jobs={jobs}
      />
    )
  }
  return SuperagentTaskSessionHeaderSlot
}

function createTaskComposerFooter(ports: TaskPageChromePorts): ComponentType<PropsRuntime<typeof TASK_COMPOSER_FOOTER_SLOT>> {
  function SuperagentTaskComposerFooter(props: PropsRuntime<typeof TASK_COMPOSER_FOOTER_SLOT>): ReactElement | null {
    const isTask = props.useProjection('agentPreset') === TASK_PRESET
    const charCount = props.useChat(countAssistantTextCharacters)
    const feed = useMemo(
      () => ports.feedRegistry.forSession(props.sessionId),
      [props.sessionId],
    )
    const feedSnapshot = useSyncExternalStore(
      useCallback((cb: () => void) => feed.subscribe(cb), [feed]),
      useCallback(() => feed.getSnapshot(), [feed]),
    )

    if (!isTask) return null

    const readyDispatcher = (feedSnapshot.self?.dispatcher ?? '').trim() || feedSnapshot.dispatcherLabel.trim()
    const leftText = readyDispatcher
      ? `调度员 ${readyDispatcher} · 订阅额度`
      : '调度员 · 待接入'

    return (
      <div className={css.composerFooter} aria-label="任务页状态栏">
        <span>{leftText}</span>
        <span>{`超级玩家 ${charCount} 字`}</span>
      </div>
    )
  }
  return SuperagentTaskComposerFooter
}

type SuperagentRightbarRootProps = PropsRuntime<'rightbar'>
  & PropsRenderSlots<typeof TASK_RIGHTBAR_SESSION_ALIAS>

function SuperagentRightbarRoot(props: SuperagentRightbarRootProps): ReactElement {
  const isChatPanel = props.usePanelInfo(info => info.activePanelId === null)
  return (
    <>
      {props.renderSlot(TASK_RIGHTBAR_SESSION_ALIAS, {
        width: props.width,
        viewportWidth: props.viewportWidth,
        canShow: props.canShow,
        isChatPanel,
      })}
    </>
  )
}

function createTaskRightbarSession(
  ports: TaskPageChromePorts,
): ComponentType<PropsRuntime<typeof TASK_RIGHTBAR_SESSION_ALIAS>> {
  function SuperagentRightbarSession(
    props: PropsRuntime<typeof TASK_RIGHTBAR_SESSION_ALIAS>,
  ): ReactElement | null {
    const { sessionId, isChatPanel, width, canShow } = props
    const isTaskPreset = props.useProjection('agentPreset') === TASK_PRESET
    const feed = useMemo(
      () => (sessionId ? ports.feedRegistry.forSession(sessionId) : null),
      [sessionId],
    )
    const feedSnapshot = useSyncExternalStore(
      useCallback((cb: () => void) => (feed ? feed.subscribe(cb) : () => {}), [feed]),
      useCallback(() => (feed ? feed.getSnapshot() : null), [feed]),
    )

    const activeCardId = isChatPanel && sessionId && isTaskPreset
      ? (feedSnapshot?.self?.id ?? null)
      : null
    const shouldShow = Boolean(activeCardId && canShow && width > 0)

    useEffect(() => {
      if (shouldShow) {
        ports.openRightbar(true, false)
      } else {
        ports.closeRightbar()
      }
    }, [shouldShow])

    useEffect(() => () => {
      ports.closeRightbar()
    }, [])

    if (!shouldShow || !activeCardId) {
      return null
    }

    return (
      <aside
        className={css.rightbar}
        style={{ width }}
        aria-label="任务进度右栏"
        data-superagent-rightbar="true"
      >
        <ProgressPanel key={activeCardId} card={activeCardId} view={ports.view} />
      </aside>
    )
  }
  return SuperagentRightbarSession
}

function createTaskJobsActionWrapper(
  OriginalJobsAction: ComponentType<JobListActionProps>,
): ComponentType<JobListActionProps> {
  function SuperagentTaskJobsAction(props: JobListActionProps): ReactElement {
    const isTask = props.useProjection('agentPreset') === TASK_PRESET
    const rows = props.useJobs(s => s.rows[props.sessionId])
    const origT = props.t
    const rowsRef = useRef(rows)
    rowsRef.current = rows
    const origTRef = useRef(origT)
    origTRef.current = origT
    const isTaskRef = useRef(isTask)
    isTaskRef.current = isTask

    const wrappedT = useCallback<JobListActionProps['t']>(
      (key, params) => {
        const base = origTRef.current(key, params)
        if (isTaskRef.current && (key === 'count.idle.one' || key === 'count.idle.other')) {
          return formatTaskJobsLabel(base, rowsRef.current)
        }
        return base
      },
      [origT, rows, isTask],
    )

    return createElement(OriginalJobsAction, {
      ...props,
      t: wrappedT,
    })
  }
  return SuperagentTaskJobsAction
}

/**
 * 注册第四批任务页全套公共插槽：
 * - 包装 `main.conversation` 并启用 `superagent.conversation.header` / `superagent.session.header` / `superagent.session.header.jobs`
 * - 注册 `superagent.composer.footer`
 * - 以高优先级注册 `rightbar` 与 `superagent.rightbar.session` 固定右栏
 */
export function registerSuperagentTaskPageChrome(
  slots: Pick<ClientContext['slots'], 'entries' | 'subscribe' | 'inject' | 'register' | 'registerFactory'>,
  ports: TaskPageChromePorts,
): () => void {
  const api = slots as unknown as IsolatedSlotsApi
  const TaskAwareViews = createTaskAwareViews(ports)
  const TaskSessionViewFactory = createTaskSessionViewFactory(ports)
  const TaskConversationHeader = createTaskConversationHeader(ports)
  const TaskSessionHeaderSlot = createTaskSessionHeaderSlot(ports)
  const TaskComposerFooter = createTaskComposerFooter(ports)
  const TaskRightbarSession = createTaskRightbarSession(ports)

  let disposed = false

  // 1. 注册 conversation.header 新别名、footer 与固定 rightbar 根槽
  const disposeConvHeader = injectSlotRegistration(
    api,
    TASK_HEADER_ALIAS,
    {
      name: TASK_HEADER_ALIAS,
      children: {
        [TASK_SESSION_HEADER_ALIAS]: { kind: 'single', scope: 'session' },
      },
    },
    TaskConversationHeader,
  )

  const disposeFooter = injectSlotRegistration(
    api,
    TASK_COMPOSER_FOOTER_SLOT,
    {
      name: TASK_COMPOSER_FOOTER_SLOT,
    },
    TaskComposerFooter,
  )

  const disposeRightbarRoot = injectSlotRegistration(
    api,
    'rightbar',
    {
      name: 'rightbar',
      priority: -100,
      children: {
        [TASK_RIGHTBAR_SESSION_ALIAS]: { kind: 'single', scope: 'session-maybe' },
      },
    },
    SuperagentRightbarRoot,
  )

  const disposeRightbarSession = injectSlotRegistration(
    api,
    TASK_RIGHTBAR_SESSION_ALIAS,
    {
      name: TASK_RIGHTBAR_SESSION_ALIAS,
    },
    TaskRightbarSession,
  )

  // 2. 动态镜像 main.conversation（保留原 StoredEntry 组件与 widthControls）
  let currentMainSource: StoredEntry | undefined
  let disposeMain: (() => void) | undefined
  const ownMainComponents = new Set<unknown>()

  const syncMain = () => {
    if (disposed) return
    const candidates = api.entries('main.conversation').filter(e => !ownMainComponents.has(e.component))
    const nextSource = pickLowestPriority(candidates)
    if (nextSource === currentMainSource) return
    if (disposeMain) {
      const cleanup = disposeMain
      disposeMain = undefined
      cleanup()
    }
    currentMainSource = nextSource
    if (!nextSource) return

    const Wrapper = createMainConversationWrapper(
      nextSource.component as ComponentType<ConversationSlotProps>,
      TaskAwareViews,
    )
    ownMainComponents.add(Wrapper)
    const sourcePriority = nextSource.options?.priority ?? 0
    const mainOptions = cloneSlotOptions('main.conversation', nextSource, {
      priority: Math.min(sourcePriority, 0) - 100,
      children: {
        [TASK_HEADER_ALIAS]: { kind: 'single', scope: 'session-maybe' },
      },
    })
    disposeMain = injectSlotRegistration(api, 'main.conversation', mainOptions, Wrapper)
  }

  // 3. 动态镜像 conversation.session.header 的 store/inject/locale 到新别名 superagent.session.header
  let currentSessionHeaderSource: StoredEntry | undefined
  let disposeSessionHeader: (() => void) | undefined

  const syncSessionHeader = () => {
    if (disposed) return
    const nextSource = pickLowestPriority(api.entries('conversation.session.header'))
    if (nextSource === currentSessionHeaderSource) return
    if (disposeSessionHeader) {
      const cleanup = disposeSessionHeader
      disposeSessionHeader = undefined
      cleanup()
    }
    currentSessionHeaderSource = nextSource
    if (!nextSource) return

    const headerOptions = cloneSlotOptions(TASK_SESSION_HEADER_ALIAS, nextSource, {
      children: {
        [TASK_SESSION_JOBS_ALIAS]: { kind: 'list', scope: 'session' },
      },
    })
    disposeSessionHeader = injectSlotRegistration(
      api,
      TASK_SESSION_HEADER_ALIAS,
      headerOptions,
      TaskSessionHeaderSlot,
    )
  }

  // 4. 动态镜像 ui-jobs 的 job-list 到新别名 superagent.session.header.jobs
  let currentJobsSource: StoredEntry | undefined
  let disposeJobs: (() => void) | undefined

  const syncJobs = () => {
    if (disposed) return
    const jobEntries = api.entries('conversation.session.header.actions').filter(
      e => e.options?.id === 'job-list',
    )
    const nextSource = pickLowestPriority(jobEntries)
    if (nextSource === currentJobsSource) return
    if (disposeJobs) {
      const cleanup = disposeJobs
      disposeJobs = undefined
      cleanup()
    }
    currentJobsSource = nextSource
    if (!nextSource) return

    const jobsOptions = cloneSlotOptions(TASK_SESSION_JOBS_ALIAS, nextSource)
    const WrappedJobsAction = createTaskJobsActionWrapper(
      nextSource.component as ComponentType<JobListActionProps>,
    )
    disposeJobs = injectSlotRegistration(
      api,
      TASK_SESSION_JOBS_ALIAS,
      jobsOptions,
      WrappedJobsAction,
    )
  }

  // 5. 动态镜像 conversation.session 的 store handle，注册唯一 session 级视图选择工厂
  let currentSessionSource: StoredEntry | undefined
  let disposeSessionViewFactory: (() => void) | undefined

  const syncSessionViewFactory = () => {
    if (disposed) return
    const candidates = api.entries('conversation.session').filter(
      e => e.store !== undefined,
    )
    const nextSource = pickLowestPriority(candidates)
    const nextStore = nextSource?.store as ConversationStore | undefined
    if (nextSource === currentSessionSource) return
    if (disposeSessionViewFactory) {
      const cleanup = disposeSessionViewFactory
      disposeSessionViewFactory = undefined
      cleanup()
    }
    currentSessionSource = nextSource
    if (!nextSource || nextStore === undefined) return

    disposeSessionViewFactory = slots.registerFactory(
      {
        name: TASK_SESSION_VIEW_FACTORY,
        scope: 'session',
        store: nextStore,
      },
      TaskSessionViewFactory,
    )
  }

  const unsubMain = api.subscribe('main.conversation', syncMain)
  const unsubSession = api.subscribe('conversation.session', syncSessionViewFactory)
  const unsubSessionHeader = api.subscribe('conversation.session.header', syncSessionHeader)
  const unsubHeaderActions = api.subscribe('conversation.session.header.actions', syncJobs)

  syncMain()
  syncSessionViewFactory()
  syncSessionHeader()
  syncJobs()

  return () => {
    if (disposed) return
    disposed = true
    unsubMain()
    unsubSession()
    unsubSessionHeader()
    unsubHeaderActions()
    disposeMain?.()
    disposeSessionViewFactory?.()
    disposeSessionHeader?.()
    disposeJobs?.()
    disposeRightbarSession()
    disposeRightbarRoot()
    disposeFooter()
    disposeConvHeader()
  }
}
