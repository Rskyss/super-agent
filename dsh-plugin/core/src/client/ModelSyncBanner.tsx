/**
 * 挂载在 `conversation.input.dock`：仅当当前会话没能同步到最新超级助手模型时显示错误提示与重试按钮。
 */
import { useState, useSyncExternalStore, type ReactElement } from 'react'
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import { ModelSyncFailureStore } from './assistant-model-sync.ts'
import css from './assistant-model-sync.module.css'

export interface ModelSyncBannerInjected {
  readonly store: ModelSyncFailureStore
  readonly retrySync: () => Promise<void>
  readonly dismiss: () => void
}

export function ModelSyncBanner(
  props: { readonly sessionId: SessionId } & ModelSyncBannerInjected,
): ReactElement | null {
  const errorText = useSyncExternalStore(
    props.store.subscribe,
    () => props.store.getError(props.sessionId),
    () => props.store.getError(props.sessionId),
  )
  const [retrying, setRetrying] = useState(false)

  if (!errorText) return null

  const handleRetry = async () => {
    if (retrying) return
    setRetrying(true)
    try {
      await props.retrySync()
    } finally {
      setRetrying(false)
    }
  }

  return (
    <div className={css.banner} role="alert">
      <span>当前对话未能同步到最新超级玩家模型：{errorText}</span>
      <div className={css.actions}>
        <Button size="sm" variant="outline" disabled={retrying} onClick={() => { void handleRetry() }}>
          {retrying ? '同步中…' : '重试同步'}
        </Button>
        <Button size="sm" variant="ghost" disabled={retrying} onClick={props.dismiss}>
          收起
        </Button>
      </div>
    </div>
  )
}
