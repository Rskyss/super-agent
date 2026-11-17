/**
 * 改要求确认条：只在有待确认的改要求时显示，用户点“确认改”或“算了”才提交。
 * 成功立即隐藏且旧轮询不可复活；失败保留确认条可重试。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import { initialPoll, markHandled, receivePoll, type PollState } from '../confirm-state.ts'
import type { CardActionResult, PendingChange } from '../types.ts'
import { sanitizeProgressText } from './progress-view.ts'
import css from './ConfirmDock.module.css'

/** ChangeDock 对外公开属性（与组件测试保持一致）。 */
export interface ChangeDockProps {
  readonly card: string
  readonly pendingChange: (card: string) => Promise<RemoteResult<PendingChange | null>>
  readonly decideChange: (card: string, id: string, confirm: boolean) => Promise<RemoteResult<CardActionResult>>
  readonly onStatusChange?: (status: 'loading' | 'active' | 'none') => void
  readonly onDecided?: (change: PendingChange, confirm: boolean) => void
  readonly refreshTick?: number
}

/** 按 card 隔离组件实例，切换任务卡或会话时立刻清除旧状态。 */
export function ChangeDock(props: ChangeDockProps): ReactElement | null {
  return <ChangeDockCard key={props.card} {...props} />
}

function ChangeDockCard({ card, pendingChange, decideChange, onStatusChange, onDecided, refreshTick }: ChangeDockProps): ReactElement | null {
  const pendingRef = useRef(pendingChange)
  pendingRef.current = pendingChange
  const decideRef = useRef(decideChange)
  decideRef.current = decideChange
  const statusRef = useRef(onStatusChange)
  statusRef.current = onStatusChange
  const onDecidedRef = useRef(onDecided)
  onDecidedRef.current = onDecided
  const cardRef = useRef(card)
  cardRef.current = card
  const aliveRef = useRef(true)
  const busyRef = useRef(false)
  const seqRef = useRef(0)

  const [poll, setPoll] = useState<PollState<PendingChange>>(initialPoll)
  const [settled, setSettled] = useState(false)
  const [busy, setBusy] = useState(false)
  // 保留原折叠状态注释以便需要时恢复：const [collapsed, setCollapsed] = useState(false)
  const [note, setNote] = useState('')

  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  useEffect(() => {
    let alive = true
    const load = () => {
      const mine = ++seqRef.current
      void pendingRef.current(card).then((result) => {
        if (!alive) return
        setSettled(true)
        if (!result.ok || busyRef.current) return
        setPoll(state => receivePoll(state, mine, result.value))
      }).catch(() => {
        if (alive) setSettled(true)
      })
    }
    load()
    const timer = setInterval(load, 1500)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [card, refreshTick])

  useEffect(() => {
    // 只有还有确认条要你点（或正在提交）才占住输入框上方；确认后只剩一句“已确认……”时不占，
    // 卡进了待验收等阶段，验收卡照常出来（复查第 1 轮严重 1）
    if (poll.current || busy) {
      statusRef.current?.('active')
      return
    }
    statusRef.current?.(settled ? 'none' : 'loading')
  }, [poll.current, busy, settled])

  // “已确认，这一轮跑完生效”看过就行，8 秒后自己收起
  useEffect(() => {
    if (!note || poll.current || busy) return
    const timer = setTimeout(() => { setNote('') }, 8000)
    return () => { clearTimeout(timer) }
  }, [note, poll.current, busy])

  const change = poll.current
  if (!change) {
    return note ? (
      <div className={css.dock}>
        <div className={css.card}>
          <div className={css.note}>{note}</div>
        </div>
      </div>
    ) : null
  }

  const isActive = (targetCard: string, targetId: string): boolean => (
    aliveRef.current && cardRef.current === targetCard && poll.current?.id === targetId
  )

  const submit = (confirm: boolean) => {
    if (busy) return
    const targetCard = card
    const targetId = change.id
    const decidedChange = change
    busyRef.current = true
    setBusy(true)
    setNote(confirm ? '正在转给调度员…' : '')
    void decideRef.current(targetCard, targetId, confirm).then((result) => {
      if (!aliveRef.current || cardRef.current !== targetCard) return
      busyRef.current = false
      setBusy(false)
      if (!result.ok) {
        setNote(`没交上：${sanitizeProgressText(result.error.message)}`)
        return
      }
      if (!result.value.ok) {
        setNote(sanitizeProgressText(result.value.message) || '没交上，请再试一次')
        return
      }
      setPoll(state => markHandled(state, targetId))
      // 第七批 7-1：跑着时确认，后台排队回“已确认，这一轮跑完生效”——留着这句给你看；平常交上了就清掉
      const doneNote = confirm ? sanitizeProgressText(result.value.message).trim() : ''
      if (doneNote) setNote(doneNote)
      else if (isActive(targetCard, targetId)) setNote('')
      onDecidedRef.current?.(decidedChange, confirm)
    }).catch((error: unknown) => {
      if (!aliveRef.current || cardRef.current !== targetCard) return
      busyRef.current = false
      setBusy(false)
      setNote(`没交上：${sanitizeProgressText(error instanceof Error ? error.message : String(error))}`)
    })
  }

  return (
    <div className={css.dock}>
      <div className={css.card} role="group" aria-label={`改要求确认：${card}`}>
        {/* 旧版头部卡号与收起按钮注释保留：
        <div className={css.head}>
          <span className={css.title}>改要求</span>
          <Tag>{card}</Tag>
          <span className={css.headActions}>
            <span className={css.status}>等你确认</span>
            <Button size="sm" variant="ghost" aria-expanded={!collapsed} onClick={() => { setCollapsed(value => !value) }}>
              {collapsed ? '展开' : '收起'}
            </Button>
          </span>
        </div>
        */}
        <div className={css.changeSummary}>{sanitizeProgressText(change.summary)}</div>
        {typeof change.attachmentCount === 'number' && change.attachmentCount > 0 ? (
          <div className={css.changeAttachmentMeta}>{`附带${change.attachmentCount}张截图`}</div>
        ) : null}
        <div className={css.actions}>
          <Button size="sm" variant="primary" disabled={busy} onClick={() => { submit(true) }}>确认改</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => { submit(false) }}>算了</Button>
        </div>
        {note ? <div className={css.note}>{note}</div> : null}
      </div>
    </div>
  )
}
