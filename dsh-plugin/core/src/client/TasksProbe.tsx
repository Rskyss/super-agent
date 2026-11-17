import { useEffect, useId, useMemo, useRef, useState, type ReactElement } from 'react'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import {
  Button,
  IconBrowseOutlineRegular,
  IconSearchOutlineRegular,
  Input,
  SegmentedTabs,
  StateDot,
  type SegmentedTab,
} from '@deepseek-ai/dsh-client-ui-primitives'
import { FINISHED_CARD_STATES, FINISHED_PHASE_CODES, type CardRow } from '../types.ts'
import { formatWorkDuration } from '../work-duration.ts'
import { formatTaskDisplayStatus, sanitizeProgressText } from './progress-view.ts'
import styles from './TasksProbe.module.css'

type OverviewTab = 'all' | 'work' | 'ability' | 'connect' | 'archived'
type OverviewStatusKind = 'done' | 'dropped' | 'warning' | 'error' | 'ongoing' | 'paused'

/* 旧五类标签定义保留注释（本期暂藏“造能力 / 接零件”入口，保留 OverviewTab 与 matchesTab 历史数据能力，详见已藏清单）：
const TAB_ITEMS: ReadonlyArray<{ readonly key: OverviewTab; readonly label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'work', label: '干活' },
  { key: 'ability', label: '造能力' },
  { key: 'connect', label: '接零件' },
  { key: 'archived', label: '已归档' },
]
*/

const POLL_INTERVAL_MS = 3000

/** 任务总览页面拿到的正式能力（全部走宿主 Remote 接口与正式导航动作）。 */
export interface TasksProbeInjected {
  readonly status?: () => Promise<RemoteResult<{ running: boolean; port: number; project: string }>>
  readonly cards: () => Promise<RemoteResult<readonly CardRow[]>>
  readonly view?: (card: string) => Promise<RemoteResult<string>>
  readonly plan?: (card: string) => Promise<RemoteResult<string>>
  readonly cardAction?: (card: string, action: string, text: string) => Promise<RemoteResult<{ ok: boolean; message: string }>>
  readonly openTask: (card: string) => Promise<void>
  readonly startChat: () => Promise<void>
  readonly now?: () => Date
}

function isDroppedCard(row: CardRow): boolean {
  return row.state === '已放弃' || row.phaseCode === 'dropped'
}

function isCompletedCard(row: CardRow): boolean {
  if (isDroppedCard(row) || row.needsYou) return false
  return row.state === '已合并' || row.phaseCode === 'done'
}

function isInProgressCard(row: CardRow): boolean {
  if (row.archived || row.needsYou) return false
  if (FINISHED_PHASE_CODES.has(row.phaseCode) || FINISHED_CARD_STATES.has(row.state)) return false
  // 只有后台明确在跑的任务计入“进行中”；undefined 或 false 保守不计入，避免排队/暂停误报
  return row.isRunningNow === true
}

/** 第七批 7-4（对照 09）：等你处理排最前，再进行中 / 没开工的，最后已完成和不做了的。 */
function orderGroup(row: CardRow): number {
  if (!row.archived && row.needsYou) return 0
  if (isCompletedCard(row) || isDroppedCard(row) || FINISHED_PHASE_CODES.has(row.phaseCode) || FINISHED_CARD_STATES.has(row.state)) return 2
  return 1
}

/** 组内按最后更新时间新的在前；时间一样按卡号大的在前。 */
function compareRows(a: CardRow, b: CardRow): number {
  const group = orderGroup(a) - orderGroup(b)
  if (group !== 0) return group
  const time = String(b.updated ?? '').localeCompare(String(a.updated ?? ''))
  if (time !== 0) return time
  return b.id.localeCompare(a.id, undefined, { numeric: true })
}

function formatCardShortId(cardId: string): string {
  const match = /^CARD-0*(\d+)([A-Za-z]*)$/i.exec(cardId.trim())
  if (!match) return cardId
  return `${match[1]}${match[2] ?? ''}`
}

const STRICT_DATE_RE = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[T\s](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(?:\s*(Z|[+-]\d{2}:?\d{2}))?)?$/i

function yearOfDateText(raw: string | null | undefined): number | null {
  if (typeof raw !== 'string') return null
  const trimmed = raw.trim()
  const match = STRICT_DATE_RE.exec(trimmed)
  if (!match) return null

  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) return null
  if (month < 1 || month > 12 || day < 1 || day > 31) return null

  const calendarCheck = new Date(Date.UTC(year, month - 1, day))
  if (
    calendarCheck.getUTCFullYear() !== year
    || calendarCheck.getUTCMonth() !== month - 1
    || calendarCheck.getUTCDate() !== day
  ) {
    return null
  }

  let hour = 0
  let minute = 0
  let second = 0
  if (match[4] !== undefined && match[5] !== undefined) {
    hour = Number(match[4])
    minute = Number(match[5])
    second = match[6] !== undefined ? Number(match[6]) : 0
    if (
      !Number.isInteger(hour) || hour < 0 || hour > 23
      || !Number.isInteger(minute) || minute < 0 || minute > 59
      || !Number.isInteger(second) || second < 0 || second > 59
    ) {
      return null
    }
  }

  let tzSuffix = ''
  if (match[8]) {
    const rawTz = match[8].toUpperCase()
    if (rawTz === 'Z') {
      tzSuffix = 'Z'
    } else {
      const sign = rawTz[0]
      const digits = rawTz.slice(1).replace(':', '')
      const tzHour = Number(digits.slice(0, 2))
      const tzMin = Number(digits.slice(2, 4))
      if (!Number.isInteger(tzHour) || tzHour > 23 || !Number.isInteger(tzMin) || tzMin > 59) {
        return null
      }
      tzSuffix = `${sign}${String(tzHour).padStart(2, '0')}:${String(tzMin).padStart(2, '0')}`
    }
  }

  const frac = match[7] ? `.${match[7].slice(0, 3)}` : ''
  const iso = `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}T${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}${frac}${tzSuffix}`
  const ms = Date.parse(iso)
  if (!Number.isFinite(ms)) return null
  return tzSuffix ? new Date(ms).getFullYear() : year
}

function formatFlowOrKind(row: CardRow): string {
  if (typeof row.flow === 'string' && row.flow.trim()) {
    return sanitizeProgressText(row.flow.trim())
  }
  if (row.kind === 'work') return '干活'
  if (row.kind === 'ability') return '造能力'
  if (row.kind === 'connect') return '接零件'
  return '—'
}

function formatReviewRounds(rounds: number | null | undefined): string {
  if (typeof rounds !== 'number' || !Number.isFinite(rounds) || rounds <= 0) return '—'
  return `${Math.round(rounds)} 轮`
}

function resolveRowStatus(row: CardRow): { readonly kind: OverviewStatusKind; readonly label: string } {
  if (isCompletedCard(row)) {
    const label = formatTaskDisplayStatus({
      phaseCode: row.phaseCode,
      phaseLabel: row.phaseLabel,
      state: row.state,
    }) || '已完成'
    return { kind: 'done', label }
  }
  if (isDroppedCard(row)) {
    const label = formatTaskDisplayStatus({
      phaseCode: row.phaseCode,
      phaseLabel: row.phaseLabel,
      state: row.state,
    }) || '已放弃'
    return { kind: 'dropped', label }
  }
  const rawFallback = sanitizeProgressText(row.phaseLabel || row.state).trim()
  const isBlocked = row.phaseCode === 'blocked'
  const isFailed = row.phaseCode === 'error'
    || row.phaseCode.endsWith('_failed')
    || row.phaseCode === 'interrupted'
    || row.phaseCode === 'copy_missing'
    || rawFallback.includes('失败')
  if (row.needsYou) {
    const label = formatTaskDisplayStatus({
      phaseCode: row.phaseCode,
      phaseLabel: row.phaseLabel,
      state: row.state,
    }) || rawFallback || '等你处理'
    return { kind: 'warning', label }
  }
  if (isBlocked || isFailed) {
    const label = formatTaskDisplayStatus({
      phaseCode: row.phaseCode,
      phaseLabel: row.phaseLabel,
      state: row.state,
    }) || rawFallback || '已停下'
    return { kind: 'error', label }
  }
  if (!row.archived && row.isRunningNow === true) {
    const label = formatTaskDisplayStatus(row) || '运行中'
    return { kind: 'ongoing', label }
  }
  return { kind: 'paused', label: '暂停' }
}

function statusClassName(kind: OverviewStatusKind): string {
  switch (kind) {
    case 'warning': return styles.statusWarn ?? ''
    case 'done': return styles.statusSuccess ?? ''
    case 'dropped': return styles.statusMuted ?? ''
    case 'error': return styles.statusError ?? ''
    case 'ongoing': return styles.statusOngoing ?? ''
    case 'paused': return styles.statusPaused ?? ''
  }
}

function matchesTab(row: CardRow, tab: OverviewTab): boolean {
  const archived = row.archived === true
  if (tab === 'archived') return archived
  if (archived) return false
  if (tab === 'all') return true
  return row.kind === tab
}

function matchesQuery(row: CardRow, query: string): boolean {
  if (!query) return true
  const q = query.toLowerCase()
  const fields = [
    row.id,
    formatCardShortId(row.id),
    sanitizeProgressText(row.title),
    formatFlowOrKind(row),
    resolveRowStatus(row).label,
    sanitizeProgressText(row.phaseLabel || row.state),
  ]
  return fields.some(text => text.toLowerCase().includes(q))
}

/** 正式任务总览页面（09）与无任务引导页（17）。 */
/** 任务总览外框：可拖顶条在滚动层外面，列表滚动时顶条不跟着走。 */
export function TasksProbe(props: TasksProbeInjected): ReactElement {
  return (
    <div className={styles.frame}>
      {/* 窗口拖动顶条：盖住页面顶部 24px 留白，不盖按钮。 */}
      <div className={styles.dragStrip} data-window-drag="" aria-hidden="true" />
      <TasksProbeBody {...props} />
    </div>
  )
}

function TasksProbeBody(props: TasksProbeInjected): ReactElement {
  const cardsFnRef = useRef(props.cards)
  cardsFnRef.current = props.cards
  const aliveRef = useRef(true)
  const tabsId = useId()

  const [cards, setCards] = useState<readonly CardRow[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [activeTab, setActiveTab] = useState<OverviewTab>('all')
  const [query, setQuery] = useState('')
  const [openingCard, setOpeningCard] = useState<string | null>(null)
  const [startingChat, setStartingChat] = useState(false)

  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  useEffect(() => {
    let alive = true
    let seq = 0
    let appliedSeq = 0

    const refresh = async (): Promise<void> => {
      const mine = ++seq
      try {
        const res = await cardsFnRef.current()
        if (!alive || mine <= appliedSeq) return
        appliedSeq = mine
        if (!res.ok) {
          const msg = sanitizeProgressText(res.error.message) || '读取任务列表失败'
          setLoadError(msg)
          return
        }
        setCards(Array.isArray(res.value) ? res.value : [])
        setLoaded(true)
        setLoadError('')
      } catch (error: unknown) {
        if (!alive || mine <= appliedSeq) return
        appliedSeq = mine
        const msg = sanitizeProgressText(error instanceof Error ? error.message : String(error))
        setLoadError(msg || '读取任务列表失败')
      }
    }

    void refresh()
    const timer = setInterval(() => {
      void refresh()
    }, POLL_INTERVAL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  const currentYear = (props.now ? props.now() : new Date()).getFullYear()

  const metrics = useMemo(() => {
    let finishedThisYear = 0
    let inProgress = 0
    let needsYouCount = 0
    let evaluatedCompleted = 0
    let firstPassCount = 0

    for (const row of cards) {
      if (isInProgressCard(row)) {
        inProgress += 1
      }
      if (!row.archived && row.needsYou) {
        needsYouCount += 1
      }
      if (isCompletedCard(row)) {
        if (yearOfDateText(row.completedAt) === currentYear) {
          finishedThisYear += 1
        }
        if (row.firstPass === true || row.firstPass === false) {
          evaluatedCompleted += 1
          if (row.firstPass === true) firstPassCount += 1
        }
      }
    }

    return {
      finishedThisYear,
      inProgress,
      needsYouCount,
      firstPassText: evaluatedCompleted > 0 ? `${firstPassCount} / ${evaluatedCompleted}` : '—',
    }
  }, [cards, currentYear])

  const trimmedQuery = query.trim()
  const filteredRows = useMemo(
    () => cards.filter(row => matchesTab(row, activeTab) && matchesQuery(row, trimmedQuery)).sort(compareRows),
    [cards, activeTab, trimmedQuery],
  )

  const handleOpenTask = (cardId: string): void => {
    if (openingCard === cardId) return
    setOpeningCard(cardId)
    setActionError('')
    void props.openTask(cardId)
      .catch((error: unknown) => {
        if (!aliveRef.current) return
        const msg = sanitizeProgressText(error instanceof Error ? error.message : String(error))
        setActionError(`打不开任务对话：${msg || '请检查当前项目设置'}`)
      })
      .finally(() => {
        if (aliveRef.current) setOpeningCard(null)
      })
  }

  const handleStartChat = (): void => {
    if (startingChat) return
    setStartingChat(true)
    setActionError('')
    void props.startChat()
      .catch((error: unknown) => {
        if (!aliveRef.current) return
        const msg = sanitizeProgressText(error instanceof Error ? error.message : String(error))
        setActionError(`没打开新对话：${msg || '请检查当前项目设置'}`)
      })
      .finally(() => {
        if (aliveRef.current) setStartingChat(false)
      })
  }

  // 初次加载尚未拿到数据时，只展示加载提示，绝不闪烁 17 空态
  if (!loaded && !loadError) {
    return (
      <div className={styles.root}>
        <div className={styles.centerState}>
          <p className={styles.emptyDesc}>正在读取任务…</p>
        </div>
      </div>
    )
  }

  // 初次请求失败且还没有任何历史数据时，只报错，绝不冒充空任务页
  if (!loaded && loadError) {
    return (
      <div className={styles.root}>
        <div className={styles.inner}>
          <div className={styles.errorBanner} role="alert">
            {`读取任务失败：${loadError}`}
          </div>
        </div>
      </div>
    )
  }

  // 页面 17：已成功读取且一张任务卡都没有（包含归档在内一张都没有）
  if (loaded && cards.length === 0) {
    return (
      <div className={styles.root}>
        <div className={styles.centerState}>
          {loadError ? (
            <div className={styles.errorBanner} role="alert">{`刷新任务失败：${loadError}`}</div>
          ) : null}
          {actionError ? <div className={styles.errorBanner} role="alert">{actionError}</div> : null}
          <IconBrowseOutlineRegular size={40} className={styles.emptyIcon ?? ''} />
          <h2 className={styles.emptyTitle}>还没有任务</h2>
          <p className={styles.emptyDesc}>
            和超级玩家聊清楚一件事，点“确认”，它就变成一张任务卡出现在这里。只是聊聊想法不会出现在这里。
          </p>
          <Button
            variant="primary"
            disabled={startingChat}
            onClick={handleStartChat}
          >
            去找超级玩家聊
          </Button>
        </div>
      </div>
    )
  }

  const panelId = `${tabsId}-panel`
  const segmentedTabItems: readonly [SegmentedTab<OverviewTab>, ...SegmentedTab<OverviewTab>[]] = [
    {
      value: 'all',
      id: `${tabsId}-all-tab`,
      panelId,
      label: '全部',
    },
    {
      value: 'work',
      id: `${tabsId}-work-tab`,
      panelId,
      label: '干活',
    },
    // 本期暂藏“造能力 / 接零件”入口（保留类型、数据匹配逻辑与旧展示注释，详见 docs/页面对齐改版/已藏清单.md）：
    // { value: 'ability', id: `${tabsId}-ability-tab`, panelId, label: '造能力' },
    // { value: 'connect', id: `${tabsId}-connect-tab`, panelId, label: '接零件' },
    {
      value: 'archived',
      id: `${tabsId}-archived-tab`,
      panelId,
      label: '已归档',
    },
  ]

  return (
    <div className={styles.root}>
      <div className={styles.inner}>
        <header className={styles.header}>
          <h1 className={styles.title}>任务</h1>
          <p className={styles.subtitle}>
            每次在对话里点“确认”都会生成一张任务卡。聊天归聊天，干活的事都在这里。
          </p>
        </header>

        {loadError ? (
          <div className={styles.errorBanner} role="alert">{`刷新任务失败：${loadError}`}</div>
        ) : null}
        {actionError ? (
          <div className={styles.errorBanner} role="alert">{actionError}</div>
        ) : null}

        <section className={styles.statsGrid} aria-label="任务统计">
          <div className={styles.statCard}>
            <span className={styles.statLabel}>今年完成</span>
            <span className={styles.statValue}>{metrics.finishedThisYear}</span>
          </div>
          <div className={styles.statCard}>
            <span className={styles.statLabel}>进行中</span>
            <span className={`${styles.statValue} ${styles.statValueOngoing}`}>{metrics.inProgress}</span>
          </div>
          <div className={styles.statCard}>
            <span className={styles.statLabel}>等你处理</span>
            <span className={`${styles.statValue} ${styles.statValueWarn}`}>{metrics.needsYouCount}</span>
          </div>
          <div className={styles.statCard}>
            <span className={styles.statLabel}>一次就验收通过</span>
            <span className={styles.statValue}>{metrics.firstPassText}</span>
          </div>
        </section>

        <div className={styles.toolbar}>
          <SegmentedTabs<OverviewTab>
            size="sm"
            className={styles.segmentedTabs ?? ''}
            label="任务类型筛选"
            value={activeTab}
            onChange={(nextTab) => { setActiveTab(nextTab) }}
            items={segmentedTabItems}
          />
          {/* 旧自制分类标签保留注释，改由 DSH SegmentedTabs 承担视图切换与键盘交互：
          <div className={styles.tabs} role="tablist" aria-label="任务类型筛选">
            {TAB_ITEMS.map((item) => {
              const selected = activeTab === item.key
              return (
                <button
                  key={item.key}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  className={`${styles.tab} ${selected ? styles.tabActive : ''}`}
                  onClick={() => { setActiveTab(item.key) }}
                >
                  {item.label}
                </button>
              )
            })}
          </div>
          */}
          <div className={styles.searchWrap}>
            <Input
              aria-label="搜索任务"
              className={styles.searchInput ?? ''}
              icon={<IconSearchOutlineRegular size={16} />}
              placeholder="搜任务名、编号…"
              value={query}
              onChange={(event) => { setQuery(event.target.value) }}
            />
          </div>
        </div>

        <div
          id={panelId}
          role="tabpanel"
          aria-labelledby={`${tabsId}-${activeTab}-tab`}
          className={styles.tableWrap}
        >
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.colId}>编号</th>
                <th>任务</th>
                <th className={styles.colFlow}>流程 / 类型</th>
                <th className={styles.colStatus}>状态</th>
                <th className={styles.colRounds}>复查轮次</th>
                <th
                  className={styles.colElapsed}
                  title="按各轮开工到交付或停下的实际施工时间累加，不计入等你处理时间；正在运行的任务计入当前已跑时长"
                >
                  用时
                </th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((row) => {
                const rowStatus = resolveRowStatus(row)
                const rowBusy = openingCard === row.id
                return (
                  <tr
                    key={row.id}
                    className={styles.tableRow}
                    tabIndex={0}
                    aria-disabled={rowBusy}
                    onClick={() => { handleOpenTask(row.id) }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        handleOpenTask(row.id)
                      }
                    }}
                  >
                    <td className={styles.colId}>{formatCardShortId(row.id)}</td>
                    <td className={styles.colTitle}>{sanitizeProgressText(row.title) || row.id}</td>
                    <td className={styles.colFlow}>{formatFlowOrKind(row)}</td>
                    <td className={`${styles.colStatus} ${statusClassName(rowStatus.kind)}`}>
                      <span className={styles.statusCell}>
                        {rowStatus.kind === 'ongoing' ? <StateDot state="ongoing" size={12} /> : null}
                        {rowStatus.kind === 'paused' ? <StateDot state="idle" appearance="step" size={11} /> : null}
                        <span>{rowStatus.label}</span>
                      </span>
                    </td>
                    <td className={styles.colRounds}>{formatReviewRounds(row.reviewRounds)}</td>
                    <td className={styles.colElapsed}>{formatWorkDuration(row.elapsed, '—')}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {filteredRows.length === 0 ? (
            <div className={styles.filterEmpty}>
              {activeTab === 'archived' && !trimmedQuery
                ? '还没有已归档的任务'
                : '没有符合条件的任务'}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
