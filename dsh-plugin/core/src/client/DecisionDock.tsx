/**
 * 任务决定卡：在输入框上方展示方案卡、验收卡或停下卡（同一时刻只出一张）。
 * 支持收起（保留主按钮）、详情展开看原文、安全打开本机预览、以及基于最新草稿提交修改意见。
 */
import { Fragment, useEffect, useLayoutEffect, useRef, useState, type ReactElement } from 'react'
import { Button, Tag } from '@deepseek-ai/dsh-client-ui-primitives'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import { initialPoll, markHandled, receivePoll, type PollState } from '../confirm-state.ts'
import type { CardActionOptions, CardActionResult, CardPlanPayload, CardViewPayload, PrepareRevisionResult, PreviewResult } from '../types.ts'
import { buildDecisionView, isLocalHttpUrl, isPlanPhase, type DecisionAction, type DecisionCardView } from './decision-model.ts'
import { sanitizeProgressText } from './progress-view.ts'
import css from './ConfirmDock.module.css'

const REVISION_PREPARE_PHASES = new Set([
  'plan_ready',
  'plan_waiting',
  'plan_failed',
  'planning',
  'review',
  'blocked',
])

/** DecisionDock 对外公开属性（与组件测试及桥接层保持一致）。 */
export interface DecisionDockProps {
  readonly card: string
  readonly view: (card: string) => Promise<RemoteResult<string>>
  readonly plan: (card: string) => Promise<RemoteResult<string>>
  readonly cardAction: (card: string, action: string, text: string, options?: CardActionOptions) => Promise<RemoteResult<CardActionResult>>
  readonly prepareRevision?: (card: string, text: string) => Promise<RemoteResult<PrepareRevisionResult>>
  readonly onRevisionPrepared?: () => void
  readonly preview: (card: string, action: 'start' | 'stop') => Promise<RemoteResult<PreviewResult>>
  readonly openPreview: (url: string) => boolean | void
  readonly readDraft: () => string
  readonly clearDraft: () => void
  readonly hasAttachments?: () => boolean
  readonly supportsAttachments?: boolean | undefined
}

/** 按 card 隔离实例，避免切卡或切会话时残留旧状态与旧请求回包。 */
export function DecisionDock(props: DecisionDockProps): ReactElement | null {
  return <DecisionDockCard key={props.card} {...props} />
}

function DecisionDockCard(props: DecisionDockProps): ReactElement | null {
  const { card } = props
  const viewRef = useRef(props.view)
  viewRef.current = props.view
  const planRef = useRef(props.plan)
  planRef.current = props.plan
  const actionRef = useRef(props.cardAction)
  actionRef.current = props.cardAction
  const prepareRevisionRef = useRef(props.prepareRevision)
  prepareRevisionRef.current = props.prepareRevision
  const onRevisionPreparedRef = useRef(props.onRevisionPrepared)
  onRevisionPreparedRef.current = props.onRevisionPrepared
  const previewRef = useRef(props.preview)
  previewRef.current = props.preview
  const openPreviewRef = useRef(props.openPreview)
  openPreviewRef.current = props.openPreview
  const readDraftRef = useRef(props.readDraft)
  readDraftRef.current = props.readDraft
  const clearDraftRef = useRef(props.clearDraft)
  clearDraftRef.current = props.clearDraft
  const hasAttachmentsRef = useRef(props.hasAttachments)
  hasAttachmentsRef.current = props.hasAttachments
  const supportsAttachmentsRef = useRef(props.supportsAttachments === true)
  supportsAttachmentsRef.current = props.supportsAttachments === true
  const cardRef = useRef(card)
  cardRef.current = card
  const aliveRef = useRef(true)
  const busyRef = useRef(false)
  const pollSeqRef = useRef(0)
  const pollFloorRef = useRef(0)

  const [poll, setPoll] = useState<PollState<DecisionCardView>>(initialPoll)
  const [pollError, setPollError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [previewBusy, setPreviewBusy] = useState(false)
  // 保留原折叠状态注释以便需要时恢复：const [collapsed, setCollapsed] = useState(false)
  const [showDetail, setShowDetail] = useState(false)
  const [actionNote, setActionNote] = useState('')
  const [openedPreviewUrl, setOpenedPreviewUrl] = useState<string | null>(null)
  const prevDecisionIdRef = useRef<string | null>(null)

  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  useEffect(() => {
    let alive = true
    let appliedSeq = 0

    const isStalePoll = (mine: number): boolean => (
      !alive || cardRef.current !== card || mine <= appliedSeq || mine <= pollFloorRef.current || busyRef.current
    )

    const recordPollFailure = (mine: number, message: string) => {
      if (isStalePoll(mine)) return
      appliedSeq = mine
      setPollError(message)
    }

    const load = async () => {
      if (busyRef.current) return
      const mine = ++pollSeqRef.current
      let readingPlan = false
      try {
        const viewRes = await viewRef.current(card)
        if (isStalePoll(mine)) return
        if (!viewRes.ok) {
          recordPollFailure(mine, `读不到最新进度：${sanitizeProgressText(viewRes.error.message) || '请稍候再试'}`)
          return
        }
        const viewPayload = parsePayload<CardViewPayload>(viewRes.value)
        if (!viewPayload) {
          recordPollFailure(mine, '读不到最新进度：返回数据格式不对')
          return
        }

        const phaseCode = String(viewPayload.card?.phase?.code ?? '')
        let planPayload: CardPlanPayload | null = null
        if (isPlanPhase(phaseCode)) {
          readingPlan = true
          const planRes = await planRef.current(card)
          if (isStalePoll(mine)) return
          if (!planRes.ok) {
            recordPollFailure(mine, `读不到最新方案：${sanitizeProgressText(planRes.error.message) || '请稍候再试'}`)
            return
          }
          planPayload = parsePayload<CardPlanPayload>(planRes.value)
          if (!planPayload) {
            recordPollFailure(mine, '读不到最新方案：返回数据格式不对')
            return
          }
        }

        const nextDecision = buildDecisionView(card, viewPayload, planPayload)
        if (isStalePoll(mine)) return
        appliedSeq = mine
        setPollError(null)
        setPoll(state => receivePoll(state, mine, nextDecision))
      } catch (error: unknown) {
        const message = sanitizeProgressText(error instanceof Error ? error.message : String(error)) || '请稍候再试'
        recordPollFailure(mine, `${readingPlan ? '读不到最新方案' : '读不到最新进度'}：${message}`)
      }
    }

    void load()
    const timer = setInterval(() => { void load() }, 2000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [card])

  const decision = poll.current
  // 在浏览器绘制与用户可点击交互之前同步重置卡片展开态，避免首帧点击“展开”后被异步 effect 收起
  useLayoutEffect(() => {
    const nextId = decision?.id ?? null
    if (prevDecisionIdRef.current !== nextId) {
      prevDecisionIdRef.current = nextId
      setShowDetail(false)
      setOpenedPreviewUrl(null)
      if (nextId !== null) setActionNote('')
    }
  }, [decision?.id])

  if (!decision) {
    if (!pollError) return null
    return (
      <div className={css.dock}>
        <div className={css.card} role="status" aria-live="polite">
          <div className={css.head}>
            <span className={css.title}>暂时读不到最新内容</span>
            <Tag>{card}</Tag>
            <span className={css.status}>暂不可操作</span>
          </div>
          <div className={css.note}>{pollError}</div>
        </div>
      </div>
    )
  }

  const stale = pollError !== null

  const submitAction = (item: DecisionAction) => {
    if (busyRef.current || busy || previewBusy || stale) return
    const targetCard = card
    const targetId = decision.id
    let submittedDraft = ''
    let textToSend = ''

    if (item.needsDraft) {
      if (hasAttachmentsRef.current?.() === true && !supportsAttachmentsRef.current) {
        setActionNote('当前无法读取或转交截图附件，不能只转交文字修改意见')
        return
      }
      submittedDraft = readDraftRef.current()
      textToSend = submittedDraft.trim()
      if (!textToSend) {
        setActionNote('请先在输入框里写清楚怎么改，再点修改按钮')
        return
      }
    }

    busyRef.current = true
    setBusy(true)
    setActionNote('')
    setOpenedPreviewUrl(null)

    const shouldPrepareRevision = Boolean(
      prepareRevisionRef.current
      && item.action === 'revise'
      && REVISION_PREPARE_PHASES.has(decision.phaseCode),
    )

    if (shouldPrepareRevision) {
      void prepareRevisionRef.current!(targetCard, textToSend).then((result) => {
        if (!aliveRef.current || cardRef.current !== targetCard) return
        busyRef.current = false
        setBusy(false)
        if (!result.ok) {
          setActionNote(`没交上：${sanitizeProgressText(result.error.message) || '请再试一次'}`)
          return
        }
        if (!result.value.ok) {
          setActionNote(sanitizeProgressText(result.value.message) || '没交上，请再试一次')
          return
        }
        pollFloorRef.current = pollSeqRef.current
        if (result.value.pending) {
          setActionNote('')
          onRevisionPreparedRef.current?.()
          return
        }
        if (item.needsDraft && readDraftRef.current() === submittedDraft) {
          clearDraftRef.current()
        }
        setPoll(state => markHandled(state, targetId))
        setActionNote('')
        setShowDetail(false)
        onRevisionPreparedRef.current?.()
      }).catch((error: unknown) => {
        if (!aliveRef.current || cardRef.current !== targetCard) return
        busyRef.current = false
        setBusy(false)
        const message = error instanceof Error ? error.message : String(error)
        setActionNote(`没交上：${sanitizeProgressText(message) || '请再试一次'}`)
      })
      return
    }

    void actionRef.current(targetCard, item.action, textToSend).then((result) => {
      if (!aliveRef.current || cardRef.current !== targetCard) return
      busyRef.current = false
      setBusy(false)
      if (!result.ok) {
        setActionNote(`没交上：${sanitizeProgressText(result.error.message) || '请再试一次'}`)
        return
      }
      if (!result.value.ok) {
        setActionNote(sanitizeProgressText(result.value.message) || '没交上，请再试一次')
        return
      }
      pollFloorRef.current = pollSeqRef.current
      if (item.needsDraft && readDraftRef.current() === submittedDraft) {
        clearDraftRef.current()
      }
      setPoll(state => markHandled(state, targetId))
      setActionNote('')
      setShowDetail(false)
    }).catch((error: unknown) => {
      if (!aliveRef.current || cardRef.current !== targetCard) return
      busyRef.current = false
      setBusy(false)
      const message = error instanceof Error ? error.message : String(error)
      setActionNote(`没交上：${sanitizeProgressText(message) || '请再试一次'}`)
    })
  }

  const handleOpenPreview = () => {
    if (busy || previewBusy || stale) return
    const targetCard = card
    setPreviewBusy(true)
    setActionNote('')
    setOpenedPreviewUrl(null)

    void previewRef.current(targetCard, 'start').then((result) => {
      if (!aliveRef.current || cardRef.current !== targetCard) return
      setPreviewBusy(false)
      if (!result.ok) {
        setActionNote(sanitizeProgressText(result.error.message) || '打不开预览')
        return
      }
      if (!result.value.ok) {
        setActionNote(sanitizeProgressText(result.value.reason ?? '') || '打不开预览')
        return
      }
      if (result.value.mode === 'folder') {
        const folderPath = sanitizeProgressText(result.value.path ?? '')
        setActionNote(
          folderPath
            ? `这项没有网页预览，请按上面文字步骤验收（副本位置：${folderPath}）`
            : '这项没有网页预览，请按上面文字步骤验收',
        )
        return
      }
      const rawUrl = result.value.url
      if (!isLocalHttpUrl(rawUrl)) {
        setActionNote('打不开预览：预览地址不是本机网页地址')
        return
      }
      // 带 noopener 打开时部分浏览器或桌面壳会固定返回 null，不以返回值判拦截，统一展示本机链接。
      openPreviewRef.current(rawUrl)
      setOpenedPreviewUrl(rawUrl)
    }).catch((error: unknown) => {
      if (!aliveRef.current || cardRef.current !== targetCard) return
      setPreviewBusy(false)
      const message = error instanceof Error ? error.message : String(error)
      setActionNote(sanitizeProgressText(message) || '打不开预览')
    })
  }

  const visibleActions = decision.actions

  return (
    <div className={css.dock}>
      <div className={`${css.card} ${css.decisionCard}`} role="group" aria-label={`${decision.title}：${decision.card}`}>
        <div className={css.head}>
          <span className={css.title}>{decision.title}</span>
          {/* 旧版头部卡号与收起按钮注释保留：
          <Tag>{decision.card}</Tag>
          <Button size="sm" variant="ghost" aria-expanded={!collapsed} onClick={() => { setCollapsed(value => !value) }}>
            {collapsed ? '展开' : '收起'}
          </Button>
          */}
          <span className={css.headActions}>
            <Tag tone={stale ? 'warning' : 'warning'} className={stale ? css.warn : undefined}>
              {stale ? '不是最新' : decision.statusLabel}
            </Tag>
          </span>
        </div>

        {renderBody(
          decision,
          showDetail,
          () => { setShowDetail(value => !value) },
          openedPreviewUrl,
        )}

        <div className={css.actions}>
          {decision.kind === 'accept' && decision.canPreview ? (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || previewBusy || stale}
              onClick={handleOpenPreview}
            >
              {previewBusy ? '正在打开预览…' : '打开预览窗口'}
            </Button>
          ) : null}
          {visibleActions.map(item => (
            <Button
              key={item.action}
              size="sm"
              variant={item.primary ? 'primary' : 'outline'}
              disabled={busy || previewBusy || stale}
              onClick={() => { submitAction(item) }}
            >
              {item.label}
            </Button>
          ))}
        </div>

        {pollError ? <div className={css.note}>{pollError}（当前显示的不是最新内容，请等待恢复后再操作）</div> : null}
        {actionNote ? <div className={css.note}>{actionNote}</div> : null}
        {/* 05 验收卡预览正常态去重，不在按钮下方重复展示裸网址与备用链接（已登记至 docs/页面对齐改版/已藏清单.md）：
        {openedPreviewUrl && isLocalHttpUrl(openedPreviewUrl) ? (
          <div className={css.note}>
            {'预览已打开，没看到就点这里：'}
            <a className={css.previewLink} href={openedPreviewUrl} target="_blank" rel="noopener noreferrer">
              {openedPreviewUrl}
            </a>
          </div>
        ) : null}
        */}
        {/* 展开态备用链接注释保留：用户图外地址不展示（包括展开）
        {showDetail && openedPreviewUrl && isLocalHttpUrl(openedPreviewUrl) ? (
          <div className={css.note}>
            {'预览已打开，没看到就点这里：'}
            <a className={css.previewLink} href={openedPreviewUrl} target="_blank" rel="noopener noreferrer">
              {openedPreviewUrl}
            </a>
          </div>
        ) : null}
        */}
      </div>
    </div>
  )
}

function renderBody(
  decision: DecisionCardView,
  showDetail: boolean,
  onToggleDetail: () => void,
  openedPreviewUrl: string | null,
): ReactElement {
  if (decision.kind === 'plan') {
    return (
      <>
        <div className={css.rows}>
          {decision.rows.map(row => (
            <Fragment key={row.label}>
              <span className={css.label}>
                {row.label === '要你拍板的' ? <span className={css.warn} aria-hidden="true">？</span> : null}
                <span>{row.label}</span>
              </span>
              <span className={css.value}>{row.value}</span>
            </Fragment>
          ))}
        </div>
        {decision.detailSections.length > 0 ? (
          <>
            <div className={css.sectionLabel}>
              <Button size="sm" variant="ghost" aria-expanded={showDetail} onClick={onToggleDetail}>
                {showDetail ? '收起原文' : '查看详情'}
              </Button>
            </div>
            {showDetail ? (
              <div className={css.details}>
                {decision.detailSections.map(section => (
                  <div key={section.label}>
                    <div className={css.label}>{section.label}</div>
                    <div className={css.detailBlock}>{section.value}</div>
                  </div>
                ))}
              </div>
            ) : null}
          </>
        ) : null}
      </>
    )
  }

  if (decision.kind === 'accept') {
    const hasExpandable = decision.criteria.length > 0 || decision.changeNotes.length > 0
    const hasOpenedPreview = Boolean(openedPreviewUrl && isLocalHttpUrl(openedPreviewUrl))
    return (
      <>
        <div className={css.acceptSummary}>
          <span>{decision.result}</span>
          {decision.criteria.length > 0 ? (
            <span className={css.acceptCriteriaTag}>
              {`${decision.criteria.length} 条验收标准全部 `}
              <span className={css.okCheck}>✓</span>
            </span>
          ) : null}
          {hasExpandable ? (
            <Button
              size="sm"
              variant="ghost"
              className={css.inlineExpandBtn}
              aria-expanded={showDetail}
              onClick={onToggleDetail}
            >
              {showDetail ? '收起' : '展开'}
            </Button>
          ) : null}
        </div>

        <div className={css.acceptHow}>
          {/* 原内嵌裸地址文案注释保留：
          {hasOpenedPreview
            ? `预览已经打开（${openedPreviewUrl}）。${decision.how}`
            : ...}
          */}
          {hasOpenedPreview
            ? `预览已经打开（可从顶部后台任务查看或停止）。${decision.how}`
            : !decision.canPreview
              ? `这次改动没有网页界面，请按下面步骤验收：${decision.how}`
              : decision.how}
        </div>

        {showDetail && hasExpandable ? (
          <div className={css.acceptExpanded}>
            {decision.criteria.length > 0 ? (
              <>
                <div className={css.sectionLabel}>
                  {decision.changeNotes.length > 0 && !decision.criteriaUpdated ? '原验收标准（以追加要求为准）' : '验收标准（请你核对）'}
                </div>
                <ol className={css.list}>
                  {decision.criteria.map((item, idx) => (
                    <li key={`${idx}-${item}`}>{item}</li>
                  ))}
                </ol>
              </>
            ) : null}

            {decision.changeNotes.length > 0 ? (
              <>
                <div className={css.sectionLabel}>追加要求记录</div>
                <ul className={css.list}>
                  {decision.changeNotes.map((item, idx) => (
                    <li key={`${idx}-${item}`}>{item}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
        ) : null}
      </>
    )
  }

  return (
    <>
      {decision.stopReviews.length > 0 ? (
        <table className={css.stopTable} role="table">
          <thead role="rowgroup">
            <tr role="row">
              <th role="columnheader">轮次</th>
              <th role="columnheader">复查发现</th>
              <th role="columnheader">写代码改了什么</th>
            </tr>
          </thead>
          <tbody role="rowgroup">
            {decision.stopReviews.map(row => (
              <tr key={row.round} role="row">
                <td role="cell">{`第 ${row.round} 轮`}</td>
                <td role="cell">{row.found}</td>
                <td role="cell" className={css.stopChangedCell}>{row.changed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className={css.rows}>
          <span className={css.label}>停下原因</span>
          <span className={css.value}>{decision.reason}</span>
        </div>
      )}
      {decision.stopAdvice ? (
        <div className={css.adviceRow}>
          <strong>我的建议</strong>
          <span>：</span>
          <span>{decision.stopAdvice}</span>
        </div>
      ) : null}
      {decision.detailText ? (
        <>
          <div className={css.sectionLabel}>
            <Button size="sm" variant="ghost" aria-expanded={showDetail} onClick={onToggleDetail}>
              {showDetail ? '收起详情' : '查看详情'}
            </Button>
          </div>
          {showDetail ? (
            <div className={css.details}>
              <div className={css.detailBlock}>{decision.detailText}</div>
            </div>
          ) : null}
        </>
      ) : null}
    </>
  )
}

function parsePayload<T>(raw: string): T | null {
  try {
    const value = JSON.parse(raw) as unknown
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null
    return value as T
  } catch (_error) {
    return null
  }
}
