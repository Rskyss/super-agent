/**
 * 右栏“任务进度”页签定义与容器：只读当前任务对话绑定的任务卡，并隔离会话状态。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { SidebarRightNavigationParams, SidebarRightTabDefinition } from '@deepseek-ai/dsh-client-ui-sidebar-right/client'
import type { InjectFace, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type { CardRow } from '../types.ts'
import { ProgressPanel } from './ProgressPanel.tsx'
import { sanitizeProgressText } from './progress-view.ts'
import css from './ProgressPanel.module.css'

/** 右栏页签注册标识。 */
export const PROGRESS_TAB_ID = '@superagent/core/progress'

/** 右栏页签类型名（与施工单约定一致）。 */
export const PROGRESS_TAB_KIND = 'superagent-progress'

declare module '@deepseek-ai/dsh-client-ui-sidebar-right/client' {
  interface SidebarRightTabParamsMap {
    /** 当前任务会话展示的任务卡编号。 */
    'superagent-progress': { card: string }
  }
}

/** 构造右栏页签的类型定义。 */
export function progressTabDefinition(): SidebarRightTabDefinition {
  return {
    id: PROGRESS_TAB_ID,
    kind: PROGRESS_TAB_KIND,
    title: () => '任务进度',
  }
}

function readCardParam(params: SidebarRightNavigationParams): string | undefined {
  if (params === undefined || !('card' in params)) return undefined
  return typeof params.card === 'string' && params.card ? params.card : undefined
}

/** 右栏页签容器所需的网关能力。 */
export interface ProgressTabInjected {
  readonly cardOfSession: (sessionId: string) => Promise<RemoteResult<CardRow | null>>
  readonly view: (card: string) => Promise<RemoteResult<string>>
}

/** 右栏页签容器完整属性。 */
export type ProgressTabProps = PropsRuntime<'sidebar.right.pane.tab'> & InjectFace<ProgressTabInjected>

/** 按 sessionId 隔离实例，避免切换会话时串用旧会话的任务卡。 */
export function ProgressTab(props: ProgressTabProps): ReactElement {
  return <ProgressTabSession key={props.sessionId} {...props} />
}

function ProgressTabSession(props: ProgressTabProps): ReactElement {
  const { sessionId, useTabInfo, cardOfSession, view } = props
  const cardOfSessionRef = useRef(cardOfSession)
  cardOfSessionRef.current = cardOfSession

  const { tab } = useTabInfo()
  const paramCard = readCardParam(tab.navigation.params)

  const [resolvedCard, setResolvedCard] = useState<string | null | undefined>(undefined)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    setResolvedCard(undefined)
    setError('')
    void cardOfSessionRef.current(sessionId).then(
      (result) => {
        if (!alive) return
        if (!result.ok) {
          setError(`读不到当前任务卡：${sanitizeProgressText(result.error.message)}`)
          return
        }
        setResolvedCard(result.value?.id ?? null)
        setError('')
      },
      (reason: unknown) => {
        if (!alive) return
        const message = reason instanceof Error ? reason.message : String(reason)
        setError(`读不到当前任务卡：${sanitizeProgressText(message)}`)
      },
    )
    return () => { alive = false }
  }, [sessionId, tab.navigation.revision])

  // 以真实会话所属卡片为准；首次打开尚未查回时可先用导航参数里的卡号预展示。
  const activeCard = resolvedCard !== undefined ? resolvedCard : (paramCard ?? null)

  if (activeCard) {
    return <ProgressPanel key={activeCard} card={activeCard} view={view} />
  }
  if (error) {
    return <div className={css.panel}><div className={css.errorBanner} role="status">{error}</div></div>
  }
  if (resolvedCard === undefined) {
    return <div className={css.panel}><div className={css.empty}>正在读取任务卡…</div></div>
  }
  return <div className={css.panel}><div className={css.empty}>当前对话没有关联任务卡。</div></div>
}
