import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type { CardTrajectory, ImagePayload, TrajectoryAttachment, TrajectoryDistributionItem } from '../types.ts'
import { sanitizeProgressText } from './progress-view.ts'
import styles from './TaskTrajectory.module.css'

const POLL_INTERVAL_MS = 2000
const SAFE_BASE64_RE = /^[A-Za-z0-9+/=]+$/

const ACTOR_THEME_COLORS = [
  'var(--dsw-alias-state-business-primary)',
  'var(--dsw-alias-state-success-primary)',
  'var(--dsw-alias-state-warn-label)',
  'var(--dsw-alias-brand-primary-new-colorprimary-new-color)',
  'var(--dsw-alias-state-error-primary)',
  'var(--dsw-alias-label-tertiary)',
] as const

export interface TaskTrajectoryInjected {
  readonly cardOfSession: () => Promise<string>
  readonly trajectory: (card: string) => Promise<CardTrajectory>
  readonly cardAttachment?: (card: string, name: string) => Promise<ImagePayload>
}

export type TaskTrajectoryProps = PropsRuntime<'conversation.view'> & TaskTrajectoryInjected

function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return '—'
  const rounded = Math.max(0, Math.round(seconds))
  if (rounded < 60) return `${rounded}秒`
  const minutes = Math.floor(rounded / 60)
  const rem = rounded % 60
  return rem > 0 ? `${minutes}分${rem}秒` : `${minutes}分`
}

function formatUsage(usage: number | null): string {
  if (usage === null || !Number.isFinite(usage)) return '—'
  return usage.toLocaleString()
}

interface TrajectoryState {
  readonly loading: boolean
  readonly boundCard: string
  readonly data: CardTrajectory | null
  readonly error: string
}

const INITIAL_STATE: TrajectoryState = {
  loading: true,
  boundCard: '',
  data: null,
  error: '',
}

function buildSafeImageDataUri(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null
  const record = payload as { mediaType?: unknown; data?: unknown }
  if (record.mediaType !== 'image/png' && record.mediaType !== 'image/jpeg') return null
  if (typeof record.data !== 'string' || !record.data || !SAFE_BASE64_RE.test(record.data)) return null
  return `data:${record.mediaType};base64,${record.data}`
}

function TaskTrajectoryBody({ sessionId, cardOfSession, trajectory, cardAttachment }: TaskTrajectoryProps) {
  const [state, setState] = useState<TrajectoryState>(INITIAL_STATE)
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({})
  const [attachmentError, setAttachmentError] = useState('')
  const [loadingAttachmentKey, setLoadingAttachmentKey] = useState<string | null>(null)
  const [previewImage, setPreviewImage] = useState<{ readonly name: string; readonly src: string } | null>(null)

  const activeSessionRef = useRef(sessionId)
  activeSessionRef.current = sessionId
  const seqRef = useRef(0)
  const appliedSeqRef = useRef(0)
  const previewSeqRef = useRef(0)
  const aliveRef = useRef(true)

  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  const closePreview = useCallback(() => {
    previewSeqRef.current += 1
    setPreviewImage(null)
  }, [])

  useEffect(() => {
    if (!previewImage) return
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        closePreview()
      }
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => { window.removeEventListener('keydown', onKeyDown, true) }
  }, [previewImage, closePreview])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const scheduleNext = (): void => {
      if (cancelled) return
      timer = setTimeout(() => {
        timer = null
        void runPoll()
      }, POLL_INTERVAL_MS)
    }

    const runPoll = async (): Promise<void> => {
      const requestSeq = ++seqRef.current
      const requestSession = sessionId
      try {
        const card = (await cardOfSession()).trim()
        if (cancelled || activeSessionRef.current !== requestSession) return
        if (!card) {
          if (requestSeq < appliedSeqRef.current) return
          appliedSeqRef.current = requestSeq
          setState({ loading: false, boundCard: '', data: null, error: '' })
          scheduleNext()
          return
        }

        const payload = await trajectory(card)
        if (cancelled || activeSessionRef.current !== requestSession) return
        if (requestSeq < appliedSeqRef.current) return
        appliedSeqRef.current = requestSeq
        setState({ loading: false, boundCard: card, data: payload, error: '' })
      } catch (err: unknown) {
        if (cancelled || activeSessionRef.current !== requestSession) return
        if (requestSeq < appliedSeqRef.current) return
        appliedSeqRef.current = requestSeq
        const msg = err instanceof Error && err.message ? err.message : '读取轨迹失败'
        setState(prev => ({
          ...prev,
          loading: false,
          error: `暂时无法刷新任务轨迹：${sanitizeProgressText(msg)}`,
        }))
      }
      scheduleNext()
    }

    void runPoll()

    return () => {
      cancelled = true
      if (timer !== null) {
        clearTimeout(timer)
        timer = null
      }
    }
  }, [sessionId, cardOfSession, trajectory])

  const totalExplicitSeconds = useMemo(() => {
    if (!state.data) return 0
    return state.data.distribution.reduce((sum, item) => sum + item.seconds, 0)
  }, [state.data])

  const toggleExpanded = (rowId: string): void => {
    setExpandedIds(prev => ({ ...prev, [rowId]: !prev[rowId] }))
  }

  const openAttachment = (rowId: string, attachment: TrajectoryAttachment): void => {
    const targetCard = state.boundCard
    if (!targetCard) return
    if (!cardAttachment) {
      setAttachmentError('暂时无法打开截图：读取端口未就绪')
      return
    }
    const reqSeq = ++previewSeqRef.current
    const key = `${rowId}:${attachment.name}`
    setAttachmentError('')
    setLoadingAttachmentKey(key)
    void cardAttachment(targetCard, attachment.name).then(
      (payload) => {
        if (!aliveRef.current || activeSessionRef.current !== sessionId || previewSeqRef.current !== reqSeq) return
        setLoadingAttachmentKey(null)
        const src = buildSafeImageDataUri(payload)
        if (!src) {
          setAttachmentError('无法打开截图：图片格式不受支持或内容无效')
          return
        }
        setPreviewImage({ name: attachment.name, src })
      },
      (err: unknown) => {
        if (!aliveRef.current || activeSessionRef.current !== sessionId || previewSeqRef.current !== reqSeq) return
        setLoadingAttachmentKey(null)
        const msg = err instanceof Error && err.message ? err.message : '读取截图失败'
        setAttachmentError(`无法打开截图：${sanitizeProgressText(msg)}`)
      },
    )
  }

  if (state.loading && !state.data && !state.error) {
    return (
      <div className={styles.root}>
        <div className={styles.notice}>正在加载任务轨迹…</div>
      </div>
    )
  }

  if (!state.loading && !state.boundCard && !state.error) {
    return (
      <div className={styles.root}>
        <div className={styles.notice}>
          当前是普通聊天，没有任务流程。任务轨迹会在左侧“任务”列表对应的任务对话里展示。
        </div>
      </div>
    )
  }

  const distribution: readonly TrajectoryDistributionItem[] = state.data?.distribution ?? []
  const rows = state.data?.rows ?? []

  return (
    <div className={styles.root}>
      {state.error && <div className={styles.error} role="alert">{state.error}</div>}

      <section className={styles.card} aria-label="用时分布">
        <div className={styles.sectionHeader}>
          <h3 className={styles.sectionTitle}>用时分布</h3>
          <span className={styles.sectionMeta}>
            {totalExplicitSeconds > 0
              ? `各角色累计用时 ${formatDuration(totalExplicitSeconds)}`
              : '仅统计明确记录用时的步骤'}
          </span>
        </div>

        {totalExplicitSeconds > 0 ? (
          <>
            <div className={styles.distTrack}>
              {distribution.map((item, idx) => {
                const pct = Math.max(0, (item.seconds / totalExplicitSeconds) * 100)
                const color = ACTOR_THEME_COLORS[idx % ACTOR_THEME_COLORS.length] ?? 'var(--dsw-alias-state-business-primary)'
                return (
                  <div
                    key={item.actor}
                    className={styles.distSegment}
                    style={{ width: `${pct}%`, backgroundColor: color }}
                    title={`${sanitizeProgressText(item.actor)}：${formatDuration(item.seconds)} (${Math.round(pct)}%)`}
                  />
                )
              })}
            </div>
            <div className={styles.distLegend}>
              {distribution.map((item, idx) => {
                const pct = Math.round((item.seconds / totalExplicitSeconds) * 100)
                const color = ACTOR_THEME_COLORS[idx % ACTOR_THEME_COLORS.length] ?? 'var(--dsw-alias-state-business-primary)'
                return (
                  <span key={item.actor} className={styles.legendItem}>
                    <span className={styles.legendDot} style={{ backgroundColor: color }} />
                    <span>
                      {sanitizeProgressText(item.actor)} {formatDuration(item.seconds)} · {pct}%
                    </span>
                  </span>
                )
              })}
            </div>
          </>
        ) : (
          <p className={styles.mutedHint}>暂无明确记录用时的步骤（未记录用时的步骤不参与计算）</p>
        )}
      </section>

      <section className={styles.card} aria-label="流转明细">
        <div className={styles.sectionHeader}>
          <h3 className={styles.sectionTitle}>流转记录</h3>
          <span className={styles.sectionMeta}>
            {state.boundCard ? `${state.boundCard} · 共 ${rows.length} 条 · 每 2 秒刷新` : `共 ${rows.length} 条`}
          </span>
        </div>

        {attachmentError && (
          <div className={styles.attachmentError} role="alert">
            <span>{attachmentError}</span>
            <button type="button" className={styles.rawBtn} onClick={() => setAttachmentError('')}>知道了</button>
          </div>
        )}

        {rows.length === 0 ? (
          <p className={styles.mutedHint}>还没有流转记录</p>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table} role="table">
              <thead role="rowgroup">
                <tr role="row">
                  <th className={styles.colTime} role="columnheader">几点</th>
                  <th className={styles.colActor} role="columnheader">谁</th>
                  <th className={styles.colSummary} role="columnheader">做了什么</th>
                  <th className={styles.colSeconds} role="columnheader">用时</th>
                  <th className={styles.colUsage} role="columnheader">用量</th>
                </tr>
              </thead>
              <tbody role="rowgroup">
                {rows.map((row) => {
                  const hasRaw = row.raw.trim().length > 0
                  const isOpen = expandedIds[row.id] === true
                  return (
                    <TrajectoryTableRow
                      key={row.id}
                      id={row.id}
                      time={row.time}
                      actor={sanitizeProgressText(row.actor)}
                      summary={sanitizeProgressText(row.summary)}
                      seconds={row.seconds}
                      usage={row.usage}
                      raw={hasRaw ? sanitizeProgressText(row.raw) : ''}
                      hasRaw={hasRaw}
                      isOpen={isOpen}
                      attachments={row.attachments}
                      loadingAttachmentKey={loadingAttachmentKey}
                      onToggle={toggleExpanded}
                      onOpenAttachment={openAttachment}
                    />
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {previewImage && typeof document !== 'undefined' ? createPortal(
        <div
          className={styles.imageOverlay}
          role="dialog"
          aria-modal="true"
          aria-label="追加要求截图预览"
        >
          <div className={styles.imageMask} aria-hidden="true" onClick={closePreview} />
          <div className={styles.imageDialog}>
            <div className={styles.imageDialogHead}>
              <span className={styles.imageDialogTitle}>追加要求截图</span>
              <Button size="sm" variant="outline" onClick={closePreview}>关闭截图</Button>
            </div>
            <div className={styles.imageStage}>
              <img
                className={styles.imagePreview}
                src={previewImage.src}
                alt="追加要求截图"
              />
            </div>
          </div>
        </div>,
        document.body,
      ) : null}
    </div>
  )
}

interface TrajectoryTableRowProps {
  readonly id: string
  readonly time: string
  readonly actor: string
  readonly summary: string
  readonly seconds: number | null
  readonly usage: number | null
  readonly raw: string
  readonly hasRaw: boolean
  readonly isOpen: boolean
  readonly attachments?: readonly TrajectoryAttachment[] | undefined
  readonly loadingAttachmentKey: string | null
  readonly onToggle: (id: string) => void
  readonly onOpenAttachment: (rowId: string, attachment: TrajectoryAttachment) => void
}

function TrajectoryTableRow({
  id,
  time,
  actor,
  summary,
  seconds,
  usage,
  raw,
  hasRaw,
  isOpen,
  attachments,
  loadingAttachmentKey,
  onToggle,
  onOpenAttachment,
}: TrajectoryTableRowProps) {
  return (
    <>
      <tr role="row">
        <td className={styles.colTime} role="cell">{time || '—'}</td>
        <td className={styles.colActor} role="cell">{actor || '—'}</td>
        <td className={styles.colSummary} role="cell">
          <span>{summary || '—'}</span>
          {hasRaw && (
            <button
              type="button"
              className={styles.rawBtn}
              aria-expanded={isOpen}
              onClick={() => onToggle(id)}
            >
              {isOpen ? '收起原文' : '看原文'}
            </button>
          )}
          {attachments && attachments.length > 0 && attachments.map((att, idx) => (
            <button
              key={`${att.name}-${idx}`}
              type="button"
              className={styles.rawBtn}
              disabled={loadingAttachmentKey === `${id}:${att.name}`}
              onClick={() => onOpenAttachment(id, att)}
            >
              {`查看截图 ${idx + 1}`}
            </button>
          ))}
        </td>
        <td className={styles.colSeconds} role="cell">{formatDuration(seconds)}</td>
        <td className={styles.colUsage} role="cell">{formatUsage(usage)}</td>
      </tr>
      {hasRaw && isOpen && (
        <tr className={styles.rawRow} role="row">
          <td colSpan={5} role="cell">
            <pre className={styles.rawPre}>{raw}</pre>
          </td>
        </tr>
      )}
    </>
  )
}

/** 任务卡流转轨迹视图（注册在 `conversation.view`，按会话隔离轮询）。 */
export function TaskTrajectory(props: TaskTrajectoryProps) {
  return <TaskTrajectoryBody key={props.sessionId} {...props} />
}
