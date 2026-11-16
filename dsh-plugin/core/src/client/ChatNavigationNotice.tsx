/**
 * 挂载在 `shell.overlay` 的全局新建对话错误提示（第 19 项）：
 * 当快捷键或品牌区“新建会话”触发的新建聊天失败（包括首次配置未完成锁定或后台拒绝）时，
 * 通过 DSH 原生 Toast 如实呈现错误信息，不静默也不回退到旧会话复用路径。
 */
import { useSyncExternalStore, type ReactElement } from 'react'
import { IconWarningOutlineRegular, Toast } from '@deepseek-ai/dsh-client-ui-primitives'

export interface ChatNavigationNoticeState {
  readonly seq: number
  readonly message: string
}

/** 轻量外部状态存储，用于向全局叠层发送新建会话失败提示。 */
export class ChatNavigationNoticeStore {
  private seq = 0
  private snapshot: ChatNavigationNoticeState | null = null
  private readonly listeners = new Set<() => void>()

  getSnapshot = (): ChatNavigationNoticeState | null => this.snapshot

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  show(message: string): void {
    const text = message.trim()
    if (!text) return
    this.snapshot = { seq: ++this.seq, message: text }
    this.emit()
  }

  clear(expectedSeq?: number): void {
    if (this.snapshot === null) return
    if (expectedSeq !== undefined && this.snapshot.seq !== expectedSeq) return
    this.snapshot = null
    this.emit()
  }

  private emit(): void {
    for (const listener of this.listeners) {
      listener()
    }
  }
}

export interface ChatNavigationNoticeInjected {
  readonly store: ChatNavigationNoticeStore
}

export function ChatNavigationNotice({ store }: ChatNavigationNoticeInjected): ReactElement | null {
  const notice = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot)
  if (notice === null) return null
  return (
    <Toast
      key={notice.seq}
      text={notice.message}
      icon={<IconWarningOutlineRegular />}
      holdMs={6000}
      onDone={() => { store.clear(notice.seq) }}
    />
  )
}
