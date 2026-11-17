/**
 * 首次使用整页引导与主面板导航锁定：
 * - 仅在首次配置未就绪或未探测完时渲染，不挂载 Modal 遮罩也不锁 #root；
 * - 中间主体居中展示步骤化配置卡片，底部保留禁用状态的灰输入框提示配置完成前不可发消息；
 * - 导航守卫锁定在首配面板直至后台确认配置完成，完成后回到对话首页且不再抢占后续正常导航。
 */
import { useState, useSyncExternalStore, type ReactElement } from 'react'
import type { ILayout, MainPanelId } from '@deepseek-ai/dsh-client-ui-layout/client'
import {
  Button,
  IconPlusOutlineRegular,
  IconSendOutlineRegular,
  Input,
} from '@deepseek-ai/dsh-client-ui-primitives'
import { FirstUseSetup } from './FirstUseSetup.tsx'
import type { OnboardingOverlayInjected, OnboardingUiStore } from './OnboardingOverlay.tsx'
import css from './OnboardingPage.module.css'

export const ONBOARDING_PANEL_ID = 'superagent-onboarding' as MainPanelId

const DISABLED_COMPOSER_HINT = '接好大模型后就能跟超级玩家说话…'

/** 安装首次配置整页导航守卫；返回卸载清理函数供 `ui.effect` 绑定生命周期。 */
export function installOnboardingPageNavigation(
  store: OnboardingUiStore,
  layout: Pick<ILayout, 'panelInfo' | 'selectPanel'>,
  panelId: MainPanelId,
): () => void {
  let disposed = false

  const isLocked = (): boolean => {
    const snap = store.getSnapshot()
    return !snap.loaded || snap.required
  }

  const syncFromStore = (): void => {
    if (disposed) return
    const activePanelId = layout.panelInfo.getSnapshot().activePanelId
    if (isLocked()) {
      if (activePanelId !== panelId) {
        layout.selectPanel(panelId)
      }
      return
    }
    if (activePanelId === panelId) {
      layout.selectPanel(null)
    }
  }

  const syncFromLayout = (): void => {
    if (disposed || !isLocked()) return
    const activePanelId = layout.panelInfo.getSnapshot().activePanelId
    if (activePanelId !== panelId) {
      layout.selectPanel(panelId)
    }
  }

  syncFromStore()
  const unsubscribeStore = store.subscribe(syncFromStore)
  const unsubscribeLayout = layout.panelInfo.subscribe(syncFromLayout)

  return () => {
    if (disposed) return
    disposed = true
    unsubscribeStore()
    unsubscribeLayout()
  }
}

export function OnboardingPage(props: OnboardingOverlayInjected): ReactElement | null {
  const snap = useSyncExternalStore(props.store.subscribe, props.store.getSnapshot, props.store.getSnapshot)
  const [retrying, setRetrying] = useState(false)

  if (snap.loaded && !snap.required) return null

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
    <div className={css.page}>
      {/* 窗口拖动顶条：盖住滚动区顶部 32px 留白。 */}
      <div className={css.dragStrip} data-window-drag="" aria-hidden="true" />
      <div className={css.scrollArea}>
        <div className={css.centerColumn}>
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
            presentation="page"
            loadModels={props.loadModels}
            complete={props.complete}
            onDone={() => props.onOnboardingDone(true)}
          />
        </div>
      </div>

      <div className={css.composerDock}>
        <div className={css.composerCard} aria-disabled="true">
          <Input
            disabled
            readOnly
            value=""
            aria-label={DISABLED_COMPOSER_HINT}
            placeholder={DISABLED_COMPOSER_HINT}
            className={css.composerInputWrap ?? ''}
          />
          <div className={css.composerActions}>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled
              aria-label="添加附件"
              className={css.composerBtn}
            >
              <IconPlusOutlineRegular aria-hidden="true" />
            </Button>
            <Button
              type="button"
              size="sm"
              variant="primary"
              disabled
              aria-label="发送"
              className={css.composerBtn}
            >
              <IconSendOutlineRegular aria-hidden="true" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
