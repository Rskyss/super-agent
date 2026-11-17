/**
 * 挂载在 `shell.overlay` 的首次配置 / 更换模型原生 Modal 包装器：
 * - 状态尚未探测完毕或后台探测失败时保持锁定，不提前放行新会话与聊天输入，并提供明确重试入口；
 * - 后台仍返回未配置（`required === true` 或 `configured === false`）时，绝不允许完成回调关闭或绕过引导；
 * - 首次引导未完成时禁止点击遮罩或 Esc 退出（背后靠 DSH Modal 的遮罩和焦点圈住，不另锁应用根元素，见下方说明）；
 * - 从设置“更换”打开时允许取消关闭；全程使用 DSH 原生 Modal，不改核心 DOM。
 */
import { useEffect, useState, useSyncExternalStore, type ReactElement } from 'react'
import { Button, Modal } from '@deepseek-ai/dsh-client-ui-primitives'
import type { LegacyKeyOption, OnboardingModelItem, OnboardingSetupPayload, OnboardingState } from '../types.ts'
import { FirstUseSetup } from './FirstUseSetup.tsx'
import css from './OnboardingOverlay.module.css'

export interface OnboardingUiSnapshot {
  readonly loaded: boolean
  readonly keychainEnabled: boolean
  readonly required: boolean
  readonly modalOpen: boolean
  readonly canCancel: boolean
  readonly probeError: string
  readonly state: OnboardingState | null
}

function isStateReady(state: OnboardingState | undefined | null): boolean {
  if (!state) return false
  if (!state.keychainEnabled) return true
  return state.configured && !state.required
}

/** 轻量外部状态存储，供 shell.overlay、新会话入口、输入框阻塞与设置页面共享。 */
export class OnboardingUiStore {
  private snapshot: OnboardingUiSnapshot = {
    loaded: false,
    keychainEnabled: false,
    // 未完成首次状态探测前先保守锁定，拿到后台确认已就绪或未启用钥匙串才解锁
    required: true,
    modalOpen: false,
    canCancel: false,
    probeError: '',
    state: null,
  }
  private readonly listeners = new Set<() => void>()

  getSnapshot = (): OnboardingUiSnapshot => this.snapshot

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private update(next: OnboardingUiSnapshot): void {
    this.snapshot = next
    for (const listener of this.listeners) {
      listener()
    }
  }

  applyHostState(nextState: OnboardingState): void {
    const required = !isStateReady(nextState)
    this.update({
      loaded: true,
      keychainEnabled: nextState.keychainEnabled,
      required,
      // 首次配置未完成时自动弹出；若正在手动“更换”弹窗中则保留打开态直到完成或取消
      modalOpen: required ? true : (this.snapshot.canCancel && this.snapshot.modalOpen),
      canCancel: required ? false : this.snapshot.canCancel,
      probeError: required && nextState.error ? nextState.error : '',
      state: nextState,
    })
  }

  /** 首次状态探测失败时：保持锁定、拉起弹窗并显示可重试提示，绝不静默放行聊天。 */
  applyHostProbeFailure(message: string): void {
    this.update({
      ...this.snapshot,
      loaded: true,
      required: true,
      modalOpen: true,
      canCancel: false,
      probeError: message || '暂时无法确认超级玩家配置状态，请重试',
    })
  }

  openForChange(): void {
    if (!this.snapshot.loaded || this.snapshot.required) return
    this.update({
      ...this.snapshot,
      modalOpen: true,
      canCancel: true,
      probeError: '',
    })
  }

  closeIfAllowed(): void {
    if (this.snapshot.required) return
    this.update({
      ...this.snapshot,
      modalOpen: false,
      canCancel: false,
    })
  }

  /** 仅当后台明确返回已就绪状态时才能结束引导；否则严禁绕过引导或把 required 置为 false。 */
  markCompleted(nextState?: OnboardingState): void {
    if (!isStateReady(nextState)) {
      if (nextState) {
        this.applyHostState(nextState)
      } else {
        this.update({
          ...this.snapshot,
          loaded: true,
          required: true,
          modalOpen: true,
          canCancel: false,
          probeError: this.snapshot.probeError || '尚未确认配置已在后台生效，请重试',
        })
      }
      return
    }

    this.update({
      loaded: true,
      keychainEnabled: nextState!.keychainEnabled,
      required: false,
      modalOpen: false,
      canCancel: false,
      probeError: '',
      state: nextState!,
    })
  }
}

export interface OnboardingOverlayInjected {
  readonly store: OnboardingUiStore
  readonly loadModels: (request: OnboardingSetupPayload) => Promise<readonly OnboardingModelItem[]>
  readonly complete: (request: OnboardingSetupPayload) => Promise<void>
  readonly onOnboardingDone: (wasForced: boolean) => Promise<void>
  readonly onRetryProbe?: () => Promise<void>
  /** 第 17 项：旧版复制过来的模型 Key（只有条目名，没有密钥）；读不到就当没有。 */
  readonly loadLegacyOptions?: () => Promise<readonly LegacyKeyOption[]>
}

const ignoreClose = (): void => {}

export function OnboardingOverlay(props: OnboardingOverlayInjected): ReactElement | null {
  const snap = useSyncExternalStore(props.store.subscribe, props.store.getSnapshot, props.store.getSnapshot)
  const [retrying, setRetrying] = useState(false)
  const [legacyOptions, setLegacyOptions] = useState<readonly LegacyKeyOption[]>([])
  const loadLegacyOptions = props.loadLegacyOptions

  useEffect(() => {
    if (!snap.modalOpen || !loadLegacyOptions) return
    let alive = true
    void loadLegacyOptions()
      .then((list) => { if (alive) setLegacyOptions(Array.isArray(list) ? list : []) })
      .catch(() => { if (alive) setLegacyOptions([]) })
    return () => { alive = false }
  }, [snap.modalOpen, loadLegacyOptions])

  // 不再自己给应用根元素加 inert（11-16 用户实测：配置完窗口点不动）。
  // 病根：DSH 自己的浮层也锁这个根元素（按计数），两家各记各的“原来的样子”，开关顺序一交错就会永远锁住或提前解锁。
  // DSH 原生 Modal 已经有整屏遮罩挡鼠标、Tab 焦点只在框内转、Esc 和快捷键归框，背后本来就点不到，不需要再锁。

  if (!snap.modalOpen) return null

  const handleRetryProbe = async () => {
    if (!props.onRetryProbe || retrying) return
    setRetrying(true)
    try {
      await props.onRetryProbe()
    } finally {
      setRetrying(false)
    }
  }

  return (
    <Modal
      open
      title="配置超级玩家模型"
      headless
      onClose={snap.canCancel ? () => { props.store.closeIfAllowed() } : ignoreClose}
      className={css.dialog as string}
    >
      <div className={css.content}>
        {snap.probeError ? (
          <div className={css.probeBanner} role="alert">
            <span>{snap.probeError}</span>
            {props.onRetryProbe ? (
              <Button size="sm" variant="outline" disabled={retrying} onClick={() => { void handleRetryProbe() }}>
                {retrying ? '检查中…' : '重试检测'}
              </Button>
            ) : null}
          </div>
        ) : null}
        <FirstUseSetup
          loadModels={props.loadModels}
          complete={props.complete}
          canCancel={snap.canCancel}
          onCancel={() => { props.store.closeIfAllowed() }}
          initialProvider={snap.state?.provider}
          initialBaseUrl={snap.state?.baseUrl}
          existingMaskedKey={snap.state?.maskedKey}
          legacyOptions={legacyOptions}
          onDone={() => props.onOnboardingDone(!snap.canCancel)}
        />
      </div>
    </Modal>
  )
}

/** 仅在已解锁后从设置“更换模型”时渲染的可取消弹窗包装器（首次引导改由整页承接）。 */
export function ChangeModelOverlay(props: OnboardingOverlayInjected): ReactElement | null {
  const snap = useSyncExternalStore(props.store.subscribe, props.store.getSnapshot, props.store.getSnapshot)
  if (!snap.modalOpen || !snap.canCancel) return null
  return <OnboardingOverlay {...props} />
}
