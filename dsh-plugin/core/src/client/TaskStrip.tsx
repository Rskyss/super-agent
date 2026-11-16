/**
 * 03b：聊天里点确认建出的任务卡，在输入框上面显示一条小条（卡号、标题、现在到哪、查看任务）。
 * 在任务卡自己的对话里，显示“这是哪张卡、现在到哪”的一行。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { Button, Tag } from '@deepseek-ai/dsh-client-ui-primitives'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import css from './ConfirmDock.module.css'
import { subscribeSessionRefresh } from './session-dock-sync.ts'

interface Row { readonly id: string; readonly title: string; readonly phaseLabel: string; readonly needsYou: boolean; readonly phaseCode: string }

export interface TaskStripInjected {
  sessionCards: (sessionId: string) => Promise<RemoteResult<Row[]>>
  cardOfSession: (sessionId: string) => Promise<RemoteResult<Row | null>>
  openTask: (card: string) => Promise<void>
}

/** 已结束的卡不再占小条：done 已完成、dropped 不做了（后台 phase.py 的叫法；复查第 4 项中等 4）。 */
const FINISHED = new Set(['done', 'dropped'])

type TaskStripProps = TaskStripInjected & { sessionId: string }

/** 按 sessionId 物理隔离会话实例，切换会话时清掉旧会话的任务条/自卡状态。 */
export function TaskStrip(props: TaskStripProps): ReactElement | null {
  return <TaskStripSession key={props.sessionId} {...props} />
}

function TaskStripSession(props: TaskStripProps): ReactElement | null {
  const { sessionId } = props
  const propsRef = useRef(props)
  propsRef.current = props
  const aliveRef = useRef(true)

  const [rows, setRows] = useState<Row[]>([])
  const [self, setSelf] = useState<Row | null>(null)
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set())
  const [error, setError] = useState('')
  // 两项查询各记各的失败：一项好了不能把另一项的失败提示清掉（复查第 4 项第 2 轮中等 2）。
  const [selfFailed, setSelfFailed] = useState(false)
  const [rowsFailed, setRowsFailed] = useState(false)
  const stale = selfFailed || rowsFailed

  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  useEffect(() => {
    let alive = true
    let seq = 0
    // 只丢掉比已用过的更旧的回包（网络慢也照样更新；复查第 4 项中等 6）。
    let appliedSelf = 0
    let appliedRows = 0
    // 成功、失败、异常都按同一个先后顺序处理：只认比已处理的更新的回包（复查第 4 项第 3 轮中等）。
    const load = () => {
      const mine = ++seq
      const onSelf = (r: Awaited<ReturnType<TaskStripInjected['cardOfSession']>> | undefined) => {
        if (!alive || mine <= appliedSelf) return
        appliedSelf = mine
        if (r === undefined || !r.ok) { setSelfFailed(true); return }
        setSelf(r.value)
        setSelfFailed(false)
      }
      const onRows = (r: Awaited<ReturnType<TaskStripInjected['sessionCards']>> | undefined) => {
        if (!alive || mine <= appliedRows) return
        appliedRows = mine
        if (r === undefined || !r.ok) { setRowsFailed(true); return }
        setRows(r.value)
        setRowsFailed(false)
      }
      void propsRef.current.cardOfSession(sessionId).then(onSelf, () => { onSelf(undefined) })
      void propsRef.current.sessionCards(sessionId).then(onRows, () => { onRows(undefined) })
    }
    load()
    const timer = setInterval(load, 3000)
    // 同会话内确认建卡后立刻拉一次，不等下个 3 秒轮询周期。
    const unsubscribe = subscribeSessionRefresh(sessionId, load)
    return () => { alive = false; clearInterval(timer); unsubscribe() }
  }, [sessionId])

  const visible = rows.filter(r => !hidden.has(r.id) && !FINISHED.has(r.phaseCode) && r.id !== self?.id).slice(0, 3)
  // 什么都没有：读得到就不显示；读不到要说出来，免得分不清“没有任务”和“读取失败”（复查第 4 项第 2 轮中等 3）。
  if (!self && visible.length === 0) {
    return stale ? <div className={css.dock}><div className={css.note}>读不到任务卡信息，稍后会自动再试。</div></div> : null
  }
  return (
    <div className={css.dock}>
      {self ? (
        <div className={css.card} style={visible.length > 0 ? { marginBottom: 6 } : undefined} aria-label={`任务卡 ${self.id}`}>
          <div className={css.head}>
            <span className={css.title}>{self.id} {self.title}</span>
            <Tag>任务卡</Tag>
            <span className={css.status}>{self.phaseLabel}</span>
          </div>
          <div className={css.note}>{stale ? '读不到最新进度，上面的状态可能不是最新的。' : '这是这张卡自己的对话：问进度、要改要求都在这里说，不影响别的对话。'}</div>
        </div>
      ) : null}
      {visible.map(r => (
        <div key={r.id} className={css.card} style={{ marginBottom: 6 }} aria-label={`任务卡小条 ${r.id}`}>
          <div className={css.head}>
            <span className={css.title}>{r.id} {r.title}</span>
            <span className={css.status}>{r.needsYou ? `等你处理 · ${r.phaseLabel}` : `进行中 · ${r.phaseLabel}`}</span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                const targetSession = sessionId
                setError('')
                void propsRef.current.openTask(r.id).catch((e: unknown) => {
                  if (aliveRef.current && propsRef.current.sessionId === targetSession) {
                    setError(`没打开：${e instanceof Error ? e.message : String(e)}`)
                  }
                })
              }}
            >
              查看任务
            </Button>
            <Button size="sm" variant="ghost" aria-label="收起这条" onClick={() => { setHidden(new Set([...hidden, r.id])) }}>×</Button>
          </div>
        </div>
      ))}
      {error ? <div className={css.note}>{error}</div> : null}
      {!self && stale ? <div className={css.note}>读不到最新进度，状态可能不是最新的。</div> : null}
    </div>
  )
}
