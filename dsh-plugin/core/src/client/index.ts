/**
 * 超级助手 · 浏览器半侧（第 1 项：验证正式接口；左栏和各页面在后面几项做）。
 * 挂上我们自己的 Remote 贡献（不改 DSH 的 api-remotes），注册一个临时的“任务”入口，
 * 页面里读卡片列表和进度、点按钮，全部走 ctx.remote.superagent.*。
 */
import { createElement, useEffect, useSyncExternalStore, type ReactElement } from 'react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-api-remotes/client'
import type { RemoteFailure, RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { MainPanelId } from '@deepseek-ai/dsh-client-ui-layout/client'
import type { PropsRenderSlots, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import { Button, IconPanelLeftOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import type {} from '@deepseek-ai/dsh-client-ui-sidebar/client'
import type {} from '@deepseek-ai/dsh-client-ui-sidebar-right/client'
import type {} from '@deepseek-ai/dsh-client-ui-renderer/client'
import type {} from '@deepseek-ai/dsh-client-ui-chat/client'
import type {
  ComposerAttachment,
  ConversationController,
  DraftAttachmentId,
} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type {} from '@deepseek-ai/dsh-client-ui-session/client'
import type {} from '@deepseek-ai/dsh-client-ui-settings/client'
import type {} from '@deepseek-ai/dsh-api-session-controller/client'
import type {} from '@deepseek-ai/dsh-client-ui-workspace/client'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import type {} from '@deepseek-ai/dsh-api-workspace-controller/client'
import type { WorkspaceId } from '@deepseek-ai/dsh-workspace/types'
import superagentRemote from '@superagent/core/remote'
import { formatSafeOnboardingError } from '../onboarding.ts'
import { BlocksSettingsSection, type BlocksSettingsInjected } from './BlocksSettingsSection.tsx'
import { SettingsPage, type SettingsPageInjected, type ThemeChoice } from './SettingsPage.tsx'
import { TasksProbe, type TasksProbeInjected } from './TasksProbe.tsx'
import { ConfirmDock, type ConfirmDockInjected } from './ConfirmDock.tsx'
import { DispatcherDock, type DispatcherDockInjected } from './DispatcherDock.tsx'
import { ChangeModelOverlay, OnboardingUiStore, type OnboardingOverlayInjected } from './OnboardingOverlay.tsx'
import { installOnboardingPageNavigation, ONBOARDING_PANEL_ID, OnboardingPage } from './OnboardingPage.tsx'
import { ModelSyncBanner, type ModelSyncBannerInjected } from './ModelSyncBanner.tsx'
import {
  ModelSyncFailureStore,
  syncAssistantModelToSessions,
  type AssistantModelSelection,
} from './assistant-model-sync.ts'
/* 如需恢复旧版 input.dock TaskStrip：取消下面导入与下方 conversation.input.dock superagent-tasks 整段注释：
import { TaskStrip, type TaskStripInjected } from './TaskStrip.tsx' */
import { createConfirmedTurnAnchorStore } from './receipt-anchor-store.ts'
import { createReceiptAnchorRecoveryCoordinator } from './receipt-anchor-recovery.ts'
import { createSessionCardsFeedRegistry } from './session-cards-feed.ts'
import { TaskReceipt, type TaskReceiptInjected } from './TaskReceipt.tsx'
/* 第四批改为固定右栏 ProgressPanel，原右栏页签注册注释保留：
import { PROGRESS_TAB_ID, PROGRESS_TAB_KIND, ProgressTab, progressTabDefinition, type ProgressTabInjected } from './ProgressTab.tsx' */
import { TaskDockBridge, type TaskDockBridgeInjected } from './TaskDockBridge.tsx'
import { TaskTrajectory, type TaskTrajectoryInjected } from './TaskTrajectory.tsx'
import { TaskSidebar } from './TaskSidebar.tsx'
/* 第一批临时 conversation.header 入口注释保留（第四批由 TaskPageChrome 统一按对话/任务分派）：
import { SuperagentConversationHeaderSlot } from './ConversationHeader.tsx' */
import { registerSuperagentTaskPageChrome } from './TaskPageChrome.tsx'
import { registerSuperagentBrandSlots } from './Branding.tsx'
import { registerSuperagentChatChrome } from './ChatChrome.tsx'
import { createComposerHintRegistry, registerSuperagentHomeSlots } from './HomeChrome.tsx'
import { registerSuperagentSettingsChrome } from './SettingsChrome.tsx'
import { coalesceChatStart, createChatForCurrentView, formatChatNavigationError, installChatNavigationDefaults } from './chat-navigation.ts'
import { ChatNavigationNotice, ChatNavigationNoticeStore } from './ChatNavigationNotice.tsx'
import { isLocalHttpUrl } from './decision-model.ts'
import { encodeRevisionImages } from './revision-images.ts'
import { CHAT_PRESET, createChatSession, ensureTaskSession, type ChatSessionPorts, type TaskSessionPorts } from './task-session-client.ts'

export const PANEL_ID = 'superagent-tasks' as MainPanelId
/** 第六批：设置整页（点左下“设置”中间换成这一页）。 */
export const SETTINGS_PANEL_ID = 'superagent-settings' as MainPanelId

export const inject = ['slots', 'remote']

export async function apply(ctx: ClientContext): Promise<void> {
  // 先挂上我们自己的 Remote 贡献；用到 remote.superagent 的界面放进子插件，
  // 由 inject 保证挂好之后才注册（DSH 规矩：调用方自己声明 namespace 依赖）。
  const unmount = await ctx.remote.$mount(superagentRemote)
  ctx.effect(() => unmount, 'superagent: remote')
  ctx.plugin({
    name: 'superagent-ui',
    inject: [
      'slots', 'remote', 'remote.superagent', 'remote.session', 'remote.workspace',
      'sessions', 'conversation', 'uiWorkspace', 'layout', 'sidebarRightTabs', 'sidebarRight', 'theme',
    ],
    apply(ui: ClientContext) {
      const createSessionViaController = async (
        opts: { workspaceId?: WorkspaceId; sessionId?: SessionId; agentPreset?: string },
      ): Promise<RemoteResult<{ sessionId: string }>> => {
        try {
          const sessionId = await ui.sessions.create(opts)
          return { ok: true, value: { sessionId } }
        } catch (error: unknown) {
          if (error && typeof error === 'object' && 'rpcError' in error) {
            const rpcError = (error as { rpcError?: RemoteFailure }).rpcError
            if (rpcError) return { ok: false, error: rpcError }
          }
          throw error
        }
      }
      const ports: TaskSessionPorts = {
        taskSession: card => ui.remote.superagent.taskSession(card),
        bindTaskSession: (card, sessionId) => ui.remote.superagent.bindTaskSession(card, sessionId),
        ensureWorkspace: async (path) => {
          const r = await ui.remote.workspace.create({ path })
          return r.ok ? { ok: true as const, value: { workspaceId: String(r.value.workspace.workspaceId) } } : r
        },
        createSession: request => createSessionViaController({ workspaceId: request.workspaceId as WorkspaceId, sessionId: request.sessionId as SessionId, agentPreset: request.agentPreset }),
        renameSession: request => ui.remote.session.rename({ sessionId: request.sessionId as SessionId, title: request.title }),
        archiveSession: sessionId => ui.remote.workspace.archiveSession({ sessionId: sessionId as SessionId }),
      }
      const openTask = async (card: string) => {
        const sessionId = await ensureTaskSession(ports, card)
        ui.uiWorkspace.openSession(sessionId as SessionId)
      }
      const onboardingStore = new OnboardingUiStore()
      const useSetupLocked = (): boolean => useSyncExternalStore(
        onboardingStore.subscribe,
        () => {
          const snap = onboardingStore.getSnapshot()
          return !snap.loaded || snap.required
        },
        () => {
          const snap = onboardingStore.getSnapshot()
          return !snap.loaded || snap.required
        },
      )
      const syncComposerBlocks = () => {
        const snap = onboardingStore.getSnapshot()
        const block = !snap.loaded
          ? { reason: '正在检查超级玩家模型配置，请稍候…' }
          : snap.required
            ? { reason: snap.probeError || '请先完成超级玩家模型配置与钥匙串验证' }
            : undefined
        const ids = ui.sessions.list.getSnapshot().ids
        for (const id of ids) {
          ui.conversation.blocks.set(id, block)
        }
      }
      const modelSyncFailureStore = new ModelSyncFailureStore()
      const syncedModelSig = new Map<string, string>()
      const failedModelSig = new Map<string, string>()
      const inflightModelSig = new Map<string, string>()
      const modelSyncSeq = new Map<string, number>()
      let prevMainSessionIds = new Set<string>()
      let lastModelTargetSig = ''

      const resolveActiveModelTarget = (): { readonly selection: AssistantModelSelection; readonly sig: string } | null => {
        const snap = onboardingStore.getSnapshot()
        if (!snap.loaded || !snap.keychainEnabled || snap.required || !snap.state?.configured) {
          return null
        }
        const provider = snap.state.routeId?.trim() ?? ''
        const model = snap.state.model?.trim() ?? ''
        if (!provider || !model) return null
        return {
          selection: { provider, model },
          sig: `${provider}::${model}`,
        }
      }

      const collectMainViewSessionIds = (): SessionId[] => {
        const snap = ui.sessions.list.getSnapshot()
        const result: SessionId[] = []
        for (const id of snap.ids) {
          const row = snap.byId[id]
          if (!row) continue
          if ((row.retainedBy?.mainView ?? 0) <= 0) continue
          if (row.parentId !== undefined || row.origin === 'subagent') continue
          result.push(id)
        }
        return result
      }

      const syncSingleMainSession = async (
        sessionId: SessionId,
        target: { readonly selection: AssistantModelSelection; readonly sig: string },
      ): Promise<void> => {
        const token = (modelSyncSeq.get(sessionId) ?? 0) + 1
        modelSyncSeq.set(sessionId, token)
        inflightModelSig.set(sessionId, target.sig)
        try {
          await syncAssistantModelToSessions(
            {
              selectModel: request => ui.remote.session.selectModel({
                sessionId: request.sessionId,
                provider: request.provider,
                model: request.model,
                ...(request.reasoningEffort === undefined ? {} : { reasoningEffort: request.reasoningEffort }),
              }),
            },
            target.selection,
            [sessionId],
          )
          if (modelSyncSeq.get(sessionId) !== token || resolveActiveModelTarget()?.sig !== target.sig) return
          inflightModelSig.delete(sessionId)
          syncedModelSig.set(sessionId, target.sig)
          failedModelSig.delete(sessionId)
          modelSyncFailureStore.clearError(sessionId)
        } catch (error: unknown) {
          if (modelSyncSeq.get(sessionId) !== token || resolveActiveModelTarget()?.sig !== target.sig) return
          inflightModelSig.delete(sessionId)
          syncedModelSig.delete(sessionId)
          failedModelSig.set(sessionId, target.sig)
          const msg = error instanceof Error ? error.message : String(error)
          modelSyncFailureStore.setError(sessionId, msg || '同步最新超级玩家模型失败，请点击重试')
        }
      }

      const syncOpenSessions = async (): Promise<void> => {
        const snap = onboardingStore.getSnapshot()
        if (!snap.keychainEnabled) {
          lastModelTargetSig = ''
          failedModelSig.clear()
          modelSyncFailureStore.clearAll()
          prevMainSessionIds = new Set(collectMainViewSessionIds())
          return
        }
        const target = resolveActiveModelTarget()
        const mainIds = collectMainViewSessionIds()
        const nextMainSet = new Set<string>(mainIds)
        for (const id of mainIds) {
          // 再次打开或切回主视图时允许重新尝试恢复，不被前一次短暂失败永久卡住。
          if (!prevMainSessionIds.has(id)) {
            failedModelSig.delete(id)
          }
        }
        prevMainSessionIds = nextMainSet

        if (!target) return
        if (target.sig !== lastModelTargetSig) {
          lastModelTargetSig = target.sig
          failedModelSig.clear()
          modelSyncFailureStore.clearAll()
        }

        const pendingIds = mainIds.filter(id => (
          syncedModelSig.get(id) !== target.sig
          && failedModelSig.get(id) !== target.sig
          && inflightModelSig.get(id) !== target.sig
        ))
        if (pendingIds.length === 0) return
        await Promise.all(pendingIds.map(id => syncSingleMainSession(id, target)))
      }

      const retryMainSessionSync = async (sessionId: SessionId): Promise<void> => {
        failedModelSig.delete(sessionId)
        modelSyncFailureStore.clearError(sessionId)
        const target = resolveActiveModelTarget()
        if (!target) return
        await syncSingleMainSession(sessionId, target)
      }

      const refreshOnboardingState = async (): Promise<void> => {
        try {
          const res = await ui.remote.superagent.onboardingState()
          if (!res.ok) {
            onboardingStore.applyHostProbeFailure(res.error.message || '读取超级玩家配置状态失败，请点重试')
            syncComposerBlocks()
            return
          }
          onboardingStore.applyHostState(res.value)
          syncComposerBlocks()
          void syncOpenSessions()
        } catch (error: unknown) {
          const msg = error instanceof Error ? error.message : String(error)
          onboardingStore.applyHostProbeFailure(msg || '读取超级玩家配置状态失败，请点重试')
          syncComposerBlocks()
        }
      }
      ui.effect(() => {
        syncComposerBlocks()
        const disposers = [
          onboardingStore.subscribe(syncComposerBlocks),
          ui.sessions.list.subscribe(() => {
            syncComposerBlocks()
            void syncOpenSessions()
          }),
          ui.remote.$on('credentials/reference-updated', () => { void refreshOnboardingState() }),
          ui.remote.$on('settings/document-updated', () => { void refreshOnboardingState() }),
          ui.on('connection/reset', () => { void refreshOnboardingState() }),
        ]
        void refreshOnboardingState()
        return () => {
          for (const dispose of disposers) dispose()
        }
      }, 'superagent: onboarding guard')

      const chatPorts: ChatSessionPorts = {
        ensureWorkspace: ports.ensureWorkspace,
        createSession: request => createSessionViaController({
          workspaceId: request.workspaceId as WorkspaceId,
          agentPreset: request.agentPreset,
        }),
      }
      const startChat = coalesceChatStart(async (): Promise<void> => {
        const snap = onboardingStore.getSnapshot()
        if (!snap.loaded || snap.required) {
          throw new Error(snap.probeError || '请先完成超级玩家首次模型配置')
        }
        const signal = ui.layout.beginNavigation()
        await createChatForCurrentView(
          signal,
          async (): Promise<SessionId> => {
            const status = await ui.remote.superagent.status()
            if (!status.ok) throw new Error(status.error.message)
            const projectPath = status.value.project.trim()
            if (projectPath) {
              const sessionId = await createChatSession(chatPorts, projectPath)
              return sessionId as SessionId
            }
            const defWs = await ui.remote.workspace.initializeDefault()
            if (!defWs.ok || !defWs.value) {
              throw new Error(defWs.ok ? '默认工作区尚未就绪，请先选择项目文件夹' : defWs.error.message)
            }
            const created = await createSessionViaController({
              workspaceId: defWs.value.workspace.workspaceId as WorkspaceId,
              agentPreset: CHAT_PRESET,
            })
            if (!created.ok) throw new Error(created.error.message)
            return created.value.sessionId as SessionId
          },
          (sessionId) => {
            ui.uiWorkspace.openSession(sessionId)
          },
        )
      })

      // 19：将原生无参“新建会话”（全局快捷键 / 品牌区入口）统一路由到明确的聊天新建路径，
      // 保留带工作区、草稿或显式模式的原生语义；若新建失败则在公共提示层如实报错，不回退旧复用路径。
      const chatNavNoticeStore = new ChatNavigationNoticeStore()
      ui.slots.inject('shell.overlay', () => ui.slots.register(
        {
          name: 'shell.overlay',
          id: 'superagent-chat-navigation-notice',
          order: 20,
          inject: () => ({ store: chatNavNoticeStore }),
        },
        ChatNavigationNotice,
      ))
      ui.effect(() => {
        const dispose = installChatNavigationDefaults(
          ui.uiWorkspace,
          async () => {
            chatNavNoticeStore.clear()
            await startChat()
          },
          (error: unknown) => {
            chatNavNoticeStore.show(formatChatNavigationError(error))
            console.warn('超级助手新会话创建失败:', error)
          },
        )
        return () => {
          dispose()
          chatNavNoticeStore.clear()
        }
      }, 'superagent: chat navigation defaults')

      const onboardingOverlayInjected: OnboardingOverlayInjected = {
        store: onboardingStore,
        onRetryProbe: refreshOnboardingState,
        loadLegacyOptions: async () => {
          const res = await ui.remote.superagent.legacyKeyOptions()
          return res.ok && Array.isArray(res.value) ? res.value : []
        },
        loadModels: async (request) => {
          const res = await ui.remote.superagent.discoverOnboardingModels(request)
          if (!res.ok) {
            throw new Error(formatSafeOnboardingError(res.error.message, request.key))
          }
          return res.value
        },
        complete: async (request) => {
          const res = await ui.remote.superagent.completeOnboarding(request)
          if (!res.ok) {
            throw new Error(formatSafeOnboardingError(res.error.message, request.key))
          }
        },
        onOnboardingDone: async (wasForced) => {
          const latest = await ui.remote.superagent.onboardingState()
          if (!latest.ok) {
            onboardingStore.applyHostProbeFailure(latest.error.message || '未能确认配置是否生效，请重试')
            syncComposerBlocks()
            throw new Error(latest.error.message || '未能确认配置是否生效，请重试')
          }
          onboardingStore.markCompleted(latest.value)
          syncComposerBlocks()
          if (onboardingStore.getSnapshot().required) {
            throw new Error(latest.value.error || '配置写入后尚未生效（默认模型或钥匙串未就绪），请重试确认')
          }
          await syncOpenSessions()
          if (wasForced) {
            await startChat().catch(() => {})
          }
        },
      }
      // 旧版首次配置 Modal 注册（保留备用；如需恢复弹窗引导：
      // 1) 从 ./OnboardingOverlay.tsx 恢复导入 OnboardingOverlay；
      // 2) 取消下方整段注释并移除下方的 main 面板、导航守卫与 ChangeModelOverlay 注册）：
      // ui.slots.inject('shell.overlay', () => ui.slots.register(
      //   {
      //     name: 'shell.overlay',
      //     id: 'superagent-first-use-setup',
      //     order: -50,
      //     inject: () => onboardingOverlayInjected,
      //   },
      //   OnboardingOverlay,
      // ))
      ui.slots.inject('main', () => ui.slots.register(
        {
          name: 'main',
          key: ONBOARDING_PANEL_ID,
          inject: () => onboardingOverlayInjected,
        },
        OnboardingPage,
      ))
      ui.effect(() => installOnboardingPageNavigation(onboardingStore, ui.layout, ONBOARDING_PANEL_ID))
      ui.slots.inject('shell.overlay', () => ui.slots.register(
        {
          name: 'shell.overlay',
          id: 'superagent-change-model-overlay',
          order: -50,
          inject: () => onboardingOverlayInjected,
        },
        ChangeModelOverlay,
      ))
      const face: TasksProbeInjected = {
        status: () => ui.remote.superagent.status(),
        cards: () => ui.remote.superagent.cards(),
        view: card => ui.remote.superagent.view(card),
        plan: card => ui.remote.superagent.plan(card),
        cardAction: (card, action, text) => ui.remote.superagent.cardAction(card, action, text),
        openTask,
        startChat,
      }
      ui.slots.inject('main', () => ui.slots.register({ name: 'main', key: PANEL_ID, inject: () => face }, TasksProbe))
      // 11：钥匙串模式下，若当前主会话没能跟随最新的超级助手全局模型，在输入框上方展示可重试提示条。
      ui.slots.inject('conversation.input.dock', () => ui.slots.register(
        {
          name: 'conversation.input.dock',
          id: 'superagent-model-sync',
          order: -12,
          inject: (sessionId: SessionId): ModelSyncBannerInjected => ({
            store: modelSyncFailureStore,
            retrySync: () => retryMainSessionSync(sessionId),
            dismiss: () => modelSyncFailureStore.clearError(sessionId),
          }),
        },
        ModelSyncBanner,
      ))
      // 03b：在 apply 内唯一创建的持久轮次定位 store 与同会话共享任务卡 feed 注册表；
      // ConfirmDock 与 TaskReceipt 使用同一 store handle 且同为严格 session scope，不导出模块级单例。
      const receiptAnchorStore = createConfirmedTurnAnchorStore()
      const receiptAnchorRecovery = createReceiptAnchorRecoveryCoordinator()
      const sessionCardsFeedRegistry = createSessionCardsFeedRegistry({
        sessionCards: sessionId => ui.remote.superagent.sessionCards(sessionId),
        cardOfSession: sessionId => ui.remote.superagent.cardOfSession(sessionId),
        dispatcherState: () => ui.remote.superagent.dispatcherState(),
        status: () => ui.remote.superagent.status(),
      })
      const composerHintRegistry = createComposerHintRegistry(sessionCardsFeedRegistry)
      ui.effect(() => () => { sessionCardsFeedRegistry.dispose() }, 'superagent: session cards feed')
      ui.effect(() => () => { composerHintRegistry.dispose() }, 'superagent: composer hint')
      ui.effect(() => () => { receiptAnchorRecovery.dispose() }, 'superagent: receipt anchor recovery')

      // 旧版 input.dock TaskStrip 注册整段注释保留（已迁往下方 conversation.chat.turnTail，如需恢复请先恢复顶部导入并取消注释）：
      // // 03b：任务卡小条 / 任务卡对话的说明行，排在确认卡后面；按会话隔离注入接口，避免跨会话错串。
      // ui.slots.inject('conversation.input.dock', () => ui.slots.register(
      //   {
      //     name: 'conversation.input.dock',
      //     id: 'superagent-tasks',
      //     order: -9,
      //     inject: (sessionId: SessionId): TaskStripInjected => ({
      //       sessionCards: () => ui.remote.superagent.sessionCards(sessionId),
      //       cardOfSession: () => ui.remote.superagent.cardOfSession(sessionId),
      //       openTask,
      //       dispatcherState: () => ui.remote.superagent.dispatcherState(),
      //     }),
      //   },
      //   TaskStrip,
      // ))

      // 03b：已确认需求摘要 + 任务卡小条内联留在原确认轮末尾（conversation.chat.turnTail），不贴底部输入框，也不随后续新消息移动。
      ui.slots.inject('conversation.chat.turnTail', () => ui.slots.register(
        {
          name: 'conversation.chat.turnTail',
          id: 'superagent-receipt',
          order: -10,
          store: receiptAnchorStore,
          inject: (sessionId: SessionId): TaskReceiptInjected => ({
            feed: sessionCardsFeedRegistry.forSession(sessionId),
            openTask,
          }),
        },
        TaskReceipt,
      ))
      // 10：通过对话出示的“接调度员”确认卡（贴在输入框最上方，与任务确认卡互斥）。
      ui.slots.inject('conversation.input.dock', () => ui.slots.register(
        {
          name: 'conversation.input.dock',
          id: 'superagent-dispatcher',
          order: -11,
          inject: (sessionId: SessionId): DispatcherDockInjected => ({
            dispatcherProposal: () => ui.remote.superagent.dispatcherProposal(sessionId),
            dismissDispatcherProposal: (_sid, id) => ui.remote.superagent.dismissDispatcherProposal(sessionId, id),
            connectDispatcher: (tool, opts) => ui.remote.superagent.connectDispatcher(tool, {
              sessionId,
              ...(opts?.proposalId === undefined ? {} : { proposalId: opts.proposalId }),
              ...(opts?.card === undefined ? {} : { card: opts.card }),
            }),
          }),
        },
        DispatcherDock,
      ))
      // 03：确认卡贴在输入框上面，排在 DSH 自己的排队 / 待办 / 目标卡前面；按会话绑定确认卡操作与轮次锚点 store。
      ui.slots.inject('conversation.input.dock', () => ui.slots.register(
        {
          name: 'conversation.input.dock',
          id: 'superagent-confirm',
          order: -10,
          store: receiptAnchorStore,
          inject: (sessionId: SessionId): ConfirmDockInjected => ({
            proposal: () => ui.remote.superagent.proposal(sessionId),
            confirm: (_sid, id) => ui.remote.superagent.confirmProposal(sessionId, id),
            dismiss: (_sid, id) => ui.remote.superagent.dismissProposal(sessionId, id),
            dispatcherStatus: () => ui.remote.superagent.dispatcherStatus(),
            workflow: () => ui.remote.superagent.workflowInfo(),
            sessionCards: () => ui.remote.superagent.sessionCards(sessionId),
            projectStatus: () => ui.remote.superagent.status(),
            anchorRecovery: receiptAnchorRecovery,
            onComposerState: (state) => {
              composerHintRegistry.setConfirmationState(sessionId, state)
            },
            focusInput: () => {
              const actx = ui.sessions.scope(sessionId)
              if (actx) ui.conversation.input.for(actx).focus()
            },
            // 建卡后马上建好它的任务对话（左栏立刻能看到；建不成不挡建卡，点“查看任务”时会再试）。
            onCreated: card => { void ensureTaskSession(ports, card).catch(() => {}) },
          }),
        },
        ConfirmDock,
      ))
      // 06 / 10 / 11：设置里注册正式“积木库”页；超级助手“更换”打开引导弹窗（钥匙串模式），接/换调度员走标准输入控制器打开新对话。
      const readRestrictedSettings = (): boolean => {
        const snap = onboardingStore.getSnapshot()
        return !snap.loaded || snap.required || snap.keychainEnabled
      }

      const useRestrictedSettings = (): boolean => useSyncExternalStore(
        onboardingStore.subscribe,
        readRestrictedSettings,
        readRestrictedSettings,
      )

      const blocksSettingsInjected: BlocksSettingsInjected = {
        dispatcherState: () => ui.remote.superagent.dispatcherState(),
        modelCatalog: () => ui.remote.session.modelCatalog(),
        openAssistantSetup: () => {
          if (!onboardingStore.getSnapshot().keychainEnabled) return false
          onboardingStore.openForChange()
          return true
        },
        useRestrictedSettings,
        startDispatcherChat: async (mode, currentLabel) => {
          const status = await ui.remote.superagent.status()
          if (!status.ok) throw new Error(status.error.message)
          const sessionId = await createChatSession(chatPorts, status.value.project)
          const prompt = mode === 'change'
            ? (currentLabel
                ? `帮我更换调度员（当前是 ${currentLabel}），想换成 Codex 或 agy`
                : '帮我更换调度员，想换成 Codex 或 agy')
            : '帮我接一个调度员（Codex 或 agy）'
          const ref = ui.sessions.retain(sessionId as SessionId, { source: 'controllerOperation' })
          try {
            ui.conversation.input.requestDraftInitialization(ref.binding, {
              prompt,
              whenDraftExists: 'replace',
            })
            ui.uiWorkspace.openSession(sessionId as SessionId)
          } finally {
            ref.release()
          }
        },
      }
      ui.slots.inject('settings.section', () => ui.slots.register(
        {
          name: 'settings.section',
          id: 'superagent-blocks',
          // 原 order: 20，正式模式调至最前（-100）使 Cmd+, 默认打开积木库
          order: -100,
          label: () => '积木库',
          inject: () => blocksSettingsInjected,
        },
        BlocksSettingsSection,
      ))

      // 第六批：设置整页（对照 06 / 07 / 08 / 15 / 16）。积木库那一块沿用 BlocksSettingsSection。
      const startChatWithPrompt = async (prompt: string): Promise<void> => {
        const status = await ui.remote.superagent.status()
        if (!status.ok) throw new Error(status.error.message)
        const sessionId = await createChatSession(chatPorts, status.value.project)
        const ref = ui.sessions.retain(sessionId as SessionId, { source: 'controllerOperation' })
        try {
          if (prompt) {
            ui.conversation.input.requestDraftInitialization(ref.binding, { prompt, whenDraftExists: 'replace' })
          }
          ui.uiWorkspace.openSession(sessionId as SessionId)
        } finally {
          ref.release()
        }
      }
      // DSH 主题服务（ui-theme 提供、ui-layout 依赖，一定在）；这里只用公开的读取和切换。
      const themeService = (ui as unknown as { theme: {
        getTheme(): { preference: string }
        setTheme(id: string): void
      } }).theme
      const settingsPageInjected: SettingsPageInjected = {
        abilities: () => ui.remote.superagent.settingsAbilities(),
        blocks: () => ui.remote.superagent.settingsBlocks(),
        team: () => ui.remote.superagent.settingsTeam(),
        saveTeamRole: (role, tool, model, effort) => ui.remote.superagent.saveTeamRole(role, tool, model, effort),
        workflow: () => ui.remote.superagent.workflowInfo(),
        cards: () => ui.remote.superagent.cards(),
        storedKeys: () => ui.remote.superagent.storedKeys(),
        deleteKey: async (id) => {
          const result = await ui.remote.superagent.deleteKey(id)
          // 删了超级玩家正在用的 Key：重新读一次配置状态，没配好会回到首次配置
          void ui.remote.superagent.onboardingState().then((state) => {
            if (state.ok) onboardingStore.applyHostState(state.value)
          }).catch(() => {})
          return result
        },
        records: () => ui.remote.superagent.records(),
        openRecords: () => ui.remote.superagent.openRecords(),
        theme: {
          get: () => themeService.getTheme().preference,
          set: (id: ThemeChoice) => { themeService.setTheme(id) },
          subscribe: (listener) => {
            const dispose = ui.on('theme/change' as never, listener as never) as unknown as () => void
            return () => { dispose() }
          },
        },
        startChatWithPrompt,
        renderBlocks: () => createElement(BlocksSettingsSection, {
          ...blocksSettingsInjected,
          close: () => {},
          blocks: () => ui.remote.superagent.settingsBlocks(),
          recheckDispatcher: () => ui.remote.superagent.recheckDispatcher(),
          checkBlock: (id: string) => ui.remote.superagent.checkBlock(id),
          checkAssistantModel: () => ui.remote.superagent.checkAssistantModel(),
        }),
      }
      ui.slots.inject('main', () => ui.slots.register(
        { name: 'main', key: SETTINGS_PANEL_ID, inject: () => settingsPageInjected },
        SettingsPage,
      ))
      /* 第四批改用固定右栏 ProgressPanel（rightbar 高优先级占位），原 ProgressTab 注册注释保留：
      // 05：右栏“任务进度”页签（只在任务会话自动打开；普通对话不打开）。
      ui.effect(() => ui.sidebarRightTabs.register(progressTabDefinition()), 'superagent: progress tab type')
      const progressInjected: ProgressTabInjected = {
        cardOfSession: sessionId => ui.remote.superagent.cardOfSession(sessionId),
        view: card => ui.remote.superagent.view(card),
      }
      ui.slots.inject('sidebar.right.pane.tab', () => ui.slots.register(
        {
          name: 'sidebar.right.pane.tab',
          key: PROGRESS_TAB_ID,
          inject: () => progressInjected,
        },
        ProgressTab,
      )) */
      // 05 / 06 / 14：任务对话输入框上方的改要求确认条与阶段决定卡，并在查到任务卡时自动打开右栏。
      ui.slots.inject('conversation.input.dock', () => ui.slots.register(
        {
          name: 'conversation.input.dock',
          id: 'superagent-change',
          order: -8,
          inject: (sessionId: SessionId): TaskDockBridgeInjected => {
            const attachmentPort = resolveConversationAttachmentPort(ui)
            return {
              cardOfSession: () => ui.remote.superagent.cardOfSession(sessionId),
              dispatcherState: () => ui.remote.superagent.dispatcherState(),
              dispatcherProposal: () => ui.remote.superagent.dispatcherProposal(sessionId),
              dispatcherDraft: (tool, card) => ui.remote.superagent.dispatcherDraft(tool, card),
              connectDispatcher: (tool, opts) => ui.remote.superagent.connectDispatcher(tool, {
                sessionId,
                ...(opts?.proposalId === undefined ? {} : { proposalId: opts.proposalId }),
                ...(opts?.card === undefined ? {} : { card: opts.card }),
              }),
              pendingChange: card => ui.remote.superagent.pendingChange(card),
              decideChange: (card, id, confirm) => ui.remote.superagent.decideChange(card, id, confirm),
              view: card => ui.remote.superagent.view(card),
              plan: card => ui.remote.superagent.plan(card),
              cardAction: (card, action, text, options) => ui.remote.superagent.cardAction(card, action, text, options),
              prepareRevision: (card, text, sourceDraft, attachments, sourceAttachmentIds) => (
                ui.remote.superagent.prepareRevision(card, sessionId, text, sourceDraft, attachments, sourceAttachmentIds)
              ),
              ...(attachmentPort ? {
                readRevisionAttachments: async (ids) => {
                  if (ids.length === 0) return []
                  const resolved: readonly ComposerAttachment[] = attachmentPort.resolveDraftAttachments(
                    ids as readonly DraftAttachmentId[],
                  )
                  if (resolved.length !== ids.length) {
                    throw new Error('部分截图已失效或已被移除，请重新选择截图')
                  }
                  const files: File[] = []
                  for (const item of resolved) {
                    if (item.kind !== 'image') {
                      throw new Error('当前只支持附带 PNG 或 JPG 截图，请先移除普通文件附件')
                    }
                    files.push(item.file)
                  }
                  return encodeRevisionImages(files)
                },
                releaseRevisionAttachments: (ids) => {
                  for (const id of ids) {
                    attachmentPort.releaseDraftAttachment(id as DraftAttachmentId)
                  }
                },
              } : {}),
              preview: (card, action) => ui.remote.superagent.preview(card, action),
              openPreview: openLocalPreview,
              openProgress: (_card) => {
                /* 第四批由 fixed rightbar 接管进度右栏，旧自动 openTab 逻辑注释保留：
                if (ui.sidebarRight.mounted.getSnapshot() !== sessionId) return
                try {
                  ui.sidebarRight.openTab(PROGRESS_TAB_KIND, { params: { card: _card } })
                } catch (_error) {
                  // 右栏尚未挂载或已切换会话时忽略，避免影响输入。
                } */
              },
            }
          },
        },
        TaskDockBridge,
      ))
      // ── 页面对齐改版 1-1 / 1-2：原挂在 DSH 左栏子槽（sidebar.workspaces / sidebar.session.action）的注册已停用。
      // 原因：ui-sidebar 已在 cordis.patch.yml 中设 disabled: true，现改由 TaskSidebar 直接独占 sidebar 根槽位；
      // 以下旧注册与组件代码（NewChatAction）保留，供查阅或恢复时取消注释使用。
      // const sidebarInjected: Omit<
      //   TaskSidebarProps,
      //   'useSessions' | 'useWorkspaces' | 'wide' | 'expandSidebar' | 'useSectionScroller'
      // > = {
      //   cards: () => ui.remote.superagent.cards(),
      //   taskSessions: () => ui.remote.superagent.taskSessions(),
      //   openSession: (id) => { ui.uiWorkspace.openSession(id) },
      //   openTask,
      //   startChat,
      //   renameSession: (id, title) => ui.remote.session.rename({ sessionId: id, title }),
      //   archiveSession: id => ui.remote.workspace.archiveSession({ sessionId: id }),
      //   showOverview: () => { ui.layout.selectPanel(PANEL_ID) },
      // }
      // ui.slots.inject('sidebar.workspaces', () => ui.slots.register(
      //   {
      //     name: 'sidebar.workspaces',
      //     priority: -10,
      //     inject: () => sidebarInjected,
      //   },
      //   TaskSidebar,
      // ))
      // ui.slots.inject('sidebar.session.action', () => ui.slots.register(
      //   {
      //     name: 'sidebar.session.action',
      //     id: 'superagent-new-chat',
      //     order: 0,
      //     inject: () => ({ startChat, useSetupLocked }),
      //   },
      //   NewChatAction,
      // ))

      // 通过高优先级 settings.launcher 桥接器拿到 DSH 设置壳的公开 openSettings 方法（同时覆盖账号菜单）。
      let openDshSettingsImpl: ((sectionId?: string) => void) | null = null
      // 第六批：设置改成中间整页，不再打开 DSH 设置弹框（原写法保留注释）：
      // const openSettings = () => {
      //   openDshSettingsImpl?.('superagent-blocks')
      // }
      const openSettings = () => {
        ui.layout.selectPanel(SETTINGS_PANEL_ID)
      }
      function SettingsLauncherBridge(props: PropsRuntime<'settings.launcher'>): null {
        useEffect(() => {
          openDshSettingsImpl = props.openSettings
          return () => {
            if (openDshSettingsImpl === props.openSettings) {
              openDshSettingsImpl = null
            }
          }
        }, [props.openSettings])
        return null
      }
      ui.slots.inject('settings.launcher', () => ui.slots.register(
        {
          name: 'settings.launcher',
          priority: -100,
        },
        SettingsLauncherBridge,
      ))

      const rawClientVersion = process.env.DSH_CLIENT_VERSION
      const clientVersion = rawClientVersion?.trim() || undefined
      type SidebarSlotViewProps = PropsRuntime<'sidebar'> & PropsRenderSlots<'sidebar.settings'>
      function SidebarSlotView(props: SidebarSlotViewProps): ReactElement {
        const wide = props.drawer || !props.collapsed
        return createElement(TaskSidebar, {
          cards: () => ui.remote.superagent.cards(),
          taskSessions: () => ui.remote.superagent.taskSessions(),
          useSessions: props.useSessions,
          useWorkspaces: props.useWorkspaces,
          openSession: (id) => { ui.uiWorkspace.openSession(id) },
          openTask,
          startChat,
          renameSession: (id, title) => ui.remote.session.rename({ sessionId: id, title }),
          archiveSession: id => ui.remote.workspace.archiveSession({ sessionId: id }),
          showOverview: () => { ui.layout.selectPanel(PANEL_ID) },
          wide,
          expandSidebar: () => {
            if (props.collapsed) ui.layout.toggleSidebar()
          },
          collapseSidebar: () => {
            ui.layout.toggleSidebar()
          },
          drawer: props.drawer,
          collapsed: props.collapsed,
          openSettings,
          useSetupLocked,
          version: clientVersion,
          settingsPortalSlot: createElement(
            'div',
            {
              'aria-hidden': 'true',
              style: {
                position: 'fixed',
                width: 0,
                height: 0,
                overflow: 'hidden',
                opacity: 0,
                pointerEvents: 'none',
              },
            },
            props.renderSlot('sidebar.settings', { wide }),
          ),
        })
      }
      ui.slots.inject('sidebar', () => ui.slots.register(
        {
          name: 'sidebar',
          priority: -10,
          children: {
            'sidebar.settings': { kind: 'single', scope: 'root' },
          },
        },
        SidebarSlotView,
      ))

      // 复用 DSH 外框窄屏抽屉/macOS 隐藏左栏后的左上角展开入口（因 ui-sidebar 已关闭，独立补齐 shell.leading）。
      function SidebarLeadingOpener(): ReactElement {
        const openerProps = {
          size: 'sm' as const,
          variant: 'ghost' as const,
          'data-sidebar-opener': '',
          'aria-label': '展开侧边栏',
          onClick: () => { ui.layout.toggleSidebar() },
        }
        return createElement(
          Button,
          openerProps,
          createElement(IconPanelLeftOutlineRegular, { size: 16 }),
        )
      }
      ui.slots.inject('shell.leading', () => ui.slots.register(
        {
          name: 'shell.leading',
          priority: -10,
        },
        SidebarLeadingOpener,
      ))

      ui.slots.inject('conversation.view', () => ui.slots.register(
        {
          name: 'conversation.view',
          id: 'superagent-trajectory',
          order: 11,
          label: '任务轨迹',
          inject: (sessionId: SessionId): TaskTrajectoryInjected => ({
            cardOfSession: async () => {
              const res = await ui.remote.superagent.cardOfSession(sessionId)
              if (!res.ok) throw new Error(res.error.message || '查询当前会话任务卡失败')
              return res.value?.id ?? ''
            },
            trajectory: async (card: string) => {
              const res = await ui.remote.superagent.trajectory(card)
              if (!res.ok) throw new Error(res.error.message || '读取任务轨迹失败')
              return res.value
            },
            cardAttachment: async (card: string, name: string) => {
              const res = await ui.remote.superagent.cardAttachment(card, name)
              if (!res.ok) throw new Error(res.error.message || '读取截图原图失败')
              return res.value
            },
          }),
        },
        TaskTrajectory,
      ))

      /* 第一批临时 conversation.header 占位与 Modal 路径注释保留（第四批由 registerSuperagentTaskPageChrome 通过 main.conversation 新别名接管）：
      // ── 页面对齐改版 1-3：以高优先级接管 conversation.header，替代 DSH 原生会话标题栏（不渲染旧 session.header，避免标签/更多菜单/面板露出）。
      // 恢复说明：如需恢复 DSH 原生 conversation.header 与旧 session.header 菜单/标签，注释或移除下方 conversation.header 注册即可。
      // 临时入口台账：任务对话标题栏右侧“任务轨迹”按钮为第一批临时入口（仅任务会话显示，点击以 DSH Modal 打开 TaskTrajectory），第四批正式双标签时收口。
      const headerSlotPorts = {
        cardOfSession: (sessionId: SessionId) =>
          ui.remote.superagent.cardOfSession(sessionId),
        trajectory: async (card: string) => {
          const res = await ui.remote.superagent.trajectory(card)
          if (!res.ok) throw new Error(res.error.message || '读取任务轨迹失败')
          return res.value
        },
        cardAttachment: async (card: string, name: string) => {
          const res = await ui.remote.superagent.cardAttachment(card, name)
          if (!res.ok) throw new Error(res.error.message || '读取截图原图失败')
          return res.value
        },
      }
      function ConversationHeaderSlotEntry(props: PropsRuntime<'conversation.header'>) {
        return createElement(SuperagentConversationHeaderSlot, {
          ...props,
          ...headerSlotPorts,
        })
      }
      ui.slots.inject('conversation.header', () => ui.slots.register(
        {
          name: 'conversation.header',
          priority: -100,
        },
        ConversationHeaderSlotEntry,
      )) */
      const disposeTaskPageChrome = registerSuperagentTaskPageChrome(ui.slots, {
        feedRegistry: sessionCardsFeedRegistry,
        cardOfSession: sessionId => ui.remote.superagent.cardOfSession(sessionId),
        view: card => ui.remote.superagent.view(card),
        plan: card => ui.remote.superagent.plan(card),
        trajectory: async (card: string) => {
          const res = await ui.remote.superagent.trajectory(card)
          if (!res.ok) throw new Error(res.error.message || '读取任务轨迹失败')
          return res.value
        },
        cardAttachment: async (card: string, name: string) => {
          const res = await ui.remote.superagent.cardAttachment(card, name)
          if (!res.ok) throw new Error(res.error.message || '读取截图原图失败')
          return res.value
        },
        openRightbar: (track, fullscreen) => { ui.layout.openRightbar(track, fullscreen) },
        closeRightbar: () => { ui.layout.closeRightbar() },
      })
      ui.effect(() => disposeTaskPageChrome)
      // 12 / 24：通过 DSH 公共品牌插槽统一注册左侧栏图标与名称、空对话首页品牌图标为“超级玩家”。
      registerSuperagentBrandSlots(ui.slots)
      const disposeHomeSlots = registerSuperagentHomeSlots(ui.slots, {
        startChat,
        useSetupLocked,
        usePlaceholder: composerHintRegistry.usePlaceholder,
        onBootstrapError: (error: unknown) => {
          chatNavNoticeStore.show(formatChatNavigationError(error))
          console.warn('超级助手冷启动默认对话创建失败:', error)
        },
      })
      ui.effect(() => disposeHomeSlots)
      const disposeSettingsChrome = registerSuperagentSettingsChrome(ui.slots, useRestrictedSettings)
      ui.effect(() => disposeSettingsChrome)
      const disposeChatChrome = registerSuperagentChatChrome(ui.slots)
      ui.effect(() => disposeChatChrome)
    },
  })
}

function openLocalPreview(url: string): boolean {
  if (!isLocalHttpUrl(url)) return false
  const win = window.open(url, '_blank', 'noopener,noreferrer')
  return win !== null
}

type ConversationDraftAttachmentPort = Pick<
  ConversationController,
  'resolveDraftAttachments' | 'releaseDraftAttachment'
>

function resolveConversationAttachmentPort(ui: ClientContext): ConversationDraftAttachmentPort | null {
  try {
    const service = typeof ui.get === 'function' ? ui.get('conversation') : undefined
    if (!service || typeof service !== 'object') return null
    const candidate = service as unknown as Partial<ConversationDraftAttachmentPort>
    if (
      typeof candidate.resolveDraftAttachments !== 'function'
      || typeof candidate.releaseDraftAttachment !== 'function'
    ) {
      return null
    }
    return {
      resolveDraftAttachments: candidate.resolveDraftAttachments.bind(service),
      releaseDraftAttachment: candidate.releaseDraftAttachment.bind(service),
    }
  } catch {
    return null
  }
}
