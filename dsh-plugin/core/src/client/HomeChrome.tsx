import {
  createElement,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ChangeEvent,
  type ComponentType,
  type KeyboardEvent,
  type MouseEvent,
  type PropsWithChildren,
} from 'react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {
  InjectFace,
  PropsLocale,
  PropsRenderSlots,
  PropsRuntime,
  StoredEntry,
} from '@deepseek-ai/dsh-client-ui-slots'
import type {
  ComposerAttachmentsOwnerProps,
  ComposerBarInjected,
  ComposerBarProps,
  EmptyWorkspaceOwnerProps,
  HeroAgentPresetOwnerProps,
  HeroFrameOwnerProps,
} from '@deepseek-ai/dsh-client-ui-conversation/client'
import { Button, MenuSurface, focusWithoutRing } from '@deepseek-ai/dsh-client-ui-primitives'
import type { SessionCardsFeedRegistry } from './session-cards-feed.ts'
import css from './HomeChrome.module.css'

const HOME_HERO_PLACEHOLDER = '跟超级玩家说你想做什么…'
const HOME_PROPOSAL_READY_PLACEHOLDER = '回复超级玩家，比如“没登录的算没通，会花钱的也测”…'
const HOME_REPLY_PLACEHOLDER = '回复超级玩家…'
const HOME_CONFIRMED_CHAT_PLACEHOLDER = '接着聊…'
const TASK_PLAN_PLACEHOLDER = '对方案有意见直接说，比如“挨个测就行”…'
const TASK_RUNNING_PLACEHOLDER = '问进度、插话改要求，都可以在这里说…'
const TASK_ACCEPT_PLACEHOLDER = '接着聊不会把验收卡顶走；哪里不对直接说，可以贴截图…'
const TASK_STOP_PLACEHOLDER = '点“按我说的改”前，先在这里说怎么改…'
const HOME_ATTACHMENTS_SLOT = 'superagent.home.attachments'
const HOME_OVERLAY_SLOT = 'superagent.home.overlay'
export const TASK_COMPOSER_FOOTER_SLOT = 'superagent.composer.footer'
const ALLOWED_OVERLAY_IDS = new Set(['slash-menu', 'command-popup'])

const HOME_BAR_CHILDREN = {
  [HOME_ATTACHMENTS_SLOT]: { kind: 'single' as const, scope: 'session-maybe' as const },
  [HOME_OVERLAY_SLOT]: { kind: 'list' as const, scope: 'session' as const },
  [TASK_COMPOSER_FOOTER_SLOT]: { kind: 'single' as const, scope: 'session' as const },
}

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface SlotMap {
    /** 超级玩家首页输入条镜像的附件轨子槽别名（单占位）。 */
    'superagent.home.attachments': {
      kind: 'single'
      scope: 'session-maybe'
      owner: ComposerAttachmentsOwnerProps
    }
    /** 超级玩家首页输入条镜像的指令与弹层子槽别名（列表）。 */
    'superagent.home.overlay': {
      kind: 'list'
      scope: 'session'
    }
    /** 超级玩家任务页输入框下方状态与字数 footer（严格 session 作用域）。 */
    'superagent.composer.footer': {
      kind: 'single'
      scope: 'session'
    }
  }
}

export type HomeSlotsRegistry = Pick<
  ClientContext['slots'],
  'entries' | 'subscribe' | 'inject' | 'register'
>

export interface HomeRegistrationOptions {
  startChat?: () => Promise<void> | void
  useSetupLocked?: () => boolean
  onBootstrapError?: (error: unknown) => void
  usePlaceholder?: (
    sessionId: ComposerBarProps['sessionId'],
    variant: ComposerBarProps['variant'],
  ) => string | undefined
}

export type ComposerDispatcherState = 'ready' | 'missing' | 'unknown'

export interface ComposerConfirmationState {
  readonly hasProposal: boolean
  readonly dispatcher: ComposerDispatcherState
  readonly menuOpen: boolean
}

export interface ComposerPlaceholderContext {
  variant?: ComposerBarProps['variant'] | undefined
  hasProposal?: boolean | undefined
  dispatcher?: ComposerDispatcherState | undefined
  menuOpen?: boolean | undefined
  hasConfirmedTask?: boolean | undefined
  taskPhaseCode?: string | undefined
}

export interface ComposerHintRegistry {
  setConfirmationState: (
    sessionId: string,
    state: ComposerConfirmationState | null | undefined,
  ) => void
  usePlaceholder: (
    sessionId: ComposerBarProps['sessionId'],
    variant: ComposerBarProps['variant'],
  ) => string
  dispose: () => void
}

type SessionCardsFeedHandle = ReturnType<SessionCardsFeedRegistry['forSession']>
type SessionCardsFeedSnapshot = ReturnType<SessionCardsFeedHandle['getSnapshot']>

const NOOP_UNSUBSCRIBE = () => {}

const EMPTY_CONFIRMATION_STATE: ComposerConfirmationState = Object.freeze({
  hasProposal: false,
  dispatcher: 'unknown',
  menuOpen: false,
})

const EMPTY_FEED_SNAPSHOT: SessionCardsFeedSnapshot = Object.freeze({
  rows: Object.freeze([]),
  self: null,
  dispatcherLabel: '',
  project: '',
  selfFailed: false,
  rowsFailed: false,
})

const EMPTY_FEED: Pick<SessionCardsFeedHandle, 'getSnapshot' | 'subscribe'> = {
  getSnapshot: () => EMPTY_FEED_SNAPSHOT,
  subscribe: () => NOOP_UNSUBSCRIBE,
}

/**
 * 按首页、普通对话与任务对话各阶段状态解析输入框占位提示文案。
 */
export function resolveComposerPlaceholder(context: ComposerPlaceholderContext): string {
  if (context.variant === 'hero') {
    return HOME_HERO_PLACEHOLDER
  }
  if (context.taskPhaseCode !== undefined) {
    if (context.taskPhaseCode === 'plan_ready' || context.taskPhaseCode === 'plan_waiting') {
      return TASK_PLAN_PLACEHOLDER
    }
    if (context.taskPhaseCode === 'review' || context.taskPhaseCode === 'merge_failed') {
      return TASK_ACCEPT_PLACEHOLDER
    }
    if (
      context.taskPhaseCode === 'blocked'
      || context.taskPhaseCode === 'interrupted'
      || context.taskPhaseCode === 'start_failed'
      || context.taskPhaseCode === 'plan_failed'
      || context.taskPhaseCode === 'copy_missing'
    ) {
      return TASK_STOP_PLACEHOLDER
    }
    return TASK_RUNNING_PLACEHOLDER
  }
  if (context.hasProposal) {
    if (!context.menuOpen && context.dispatcher === 'ready') {
      return HOME_PROPOSAL_READY_PLACEHOLDER
    }
    return HOME_REPLY_PLACEHOLDER
  }
  if (context.hasConfirmedTask) {
    return HOME_CONFIRMED_CHAT_PLACEHOLDER
  }
  return HOME_REPLY_PLACEHOLDER
}

function hasConfirmedChildTask(snapshot: SessionCardsFeedSnapshot): boolean {
  const selfId = snapshot.self?.id
  if (selfId === undefined) return snapshot.rows.length > 0
  return snapshot.rows.some((row) => row.id !== selfId)
}

/**
 * 创建插件 apply 作用域内的输入框提示状态注册表，复用共享任务卡 feed 并维护每会话确认卡快照。
 */
export function createComposerHintRegistry(
  feedRegistry: SessionCardsFeedRegistry,
): ComposerHintRegistry {
  let disposed = false
  const states = new Map<string, ComposerConfirmationState>()
  const listeners = new Map<string, Set<() => void>>()

  const notify = (sessionId: string): void => {
    const sessionListeners = listeners.get(sessionId)
    if (!sessionListeners) return
    for (const listener of [...sessionListeners]) {
      listener()
    }
  }

  const getConfirmationSnapshot = (sessionId: string | undefined): ComposerConfirmationState => {
    if (disposed || !sessionId) return EMPTY_CONFIRMATION_STATE
    return states.get(sessionId) ?? EMPTY_CONFIRMATION_STATE
  }

  const subscribeConfirmation = (
    sessionId: string | undefined,
    listener: () => void,
  ): (() => void) => {
    if (disposed || !sessionId) return NOOP_UNSUBSCRIBE
    let sessionListeners = listeners.get(sessionId)
    if (!sessionListeners) {
      sessionListeners = new Set()
      listeners.set(sessionId, sessionListeners)
    }
    sessionListeners.add(listener)
    return () => {
      const current = listeners.get(sessionId)
      if (!current) return
      current.delete(listener)
      if (current.size === 0) {
        listeners.delete(sessionId)
      }
    }
  }

  const setConfirmationState: ComposerHintRegistry['setConfirmationState'] = (sessionId, next) => {
    if (disposed || !sessionId) return
    const normalized: ComposerConfirmationState = next
      ? {
          hasProposal: Boolean(next.hasProposal),
          dispatcher: next.dispatcher,
          menuOpen: Boolean(next.menuOpen),
        }
      : EMPTY_CONFIRMATION_STATE
    const prev = states.get(sessionId) ?? EMPTY_CONFIRMATION_STATE
    if (
      prev.hasProposal === normalized.hasProposal
      && prev.dispatcher === normalized.dispatcher
      && prev.menuOpen === normalized.menuOpen
    ) {
      return
    }
    if (
      !normalized.hasProposal
      && normalized.dispatcher === 'unknown'
      && !normalized.menuOpen
    ) {
      states.delete(sessionId)
    } else {
      states.set(sessionId, Object.freeze(normalized))
    }
    notify(sessionId)
  }

  const usePlaceholder: ComposerHintRegistry['usePlaceholder'] = (sessionId, variant) => {
    const activeSessionId =
      !disposed && typeof sessionId === 'string' && sessionId.length > 0
        ? sessionId
        : undefined

    const subscribeHint = useCallback(
      (listener: () => void) => subscribeConfirmation(activeSessionId, listener),
      [activeSessionId],
    )
    const getHintSnapshot = useCallback(
      () => getConfirmationSnapshot(activeSessionId),
      [activeSessionId],
    )
    const hint = useSyncExternalStore(subscribeHint, getHintSnapshot)

    const feed: Pick<SessionCardsFeedHandle, 'getSnapshot' | 'subscribe'> =
      activeSessionId ? feedRegistry.forSession(activeSessionId) : EMPTY_FEED
    const subscribeFeed = useCallback(
      (listener: () => void) => feed.subscribe(listener),
      [feed],
    )
    const getFeedSnapshot = useCallback(
      () => feed.getSnapshot(),
      [feed],
    )
    const feedSnapshot = useSyncExternalStore(subscribeFeed, getFeedSnapshot)

    return resolveComposerPlaceholder({
      variant,
      hasProposal: hint.hasProposal,
      dispatcher: hint.dispatcher,
      menuOpen: hint.menuOpen,
      hasConfirmedTask: hasConfirmedChildTask(feedSnapshot),
      taskPhaseCode: feedSnapshot.self?.phaseCode,
    })
  }

  const dispose = (): void => {
    if (disposed) return
    disposed = true
    const affectedSessions = [...listeners.keys()]
    states.clear()
    for (const sid of affectedSessions) {
      notify(sid)
    }
    listeners.clear()
  }

  return {
    setConfirmationState,
    usePlaceholder,
    dispose,
  }
}

export type HomeComposerBarProps =
  PropsRuntime<'conversation.composer.bar'>
  & PropsRenderSlots<typeof HOME_ATTACHMENTS_SLOT | typeof HOME_OVERLAY_SLOT | typeof TASK_COMPOSER_FOOTER_SLOT>
  & InjectFace<ComposerBarInjected>
  & PropsLocale<'conversation'>

export type SuperagentHeroFrameProps = PropsWithChildren<HeroFrameOwnerProps>

/**
 * 隔离公开 Slot 接口的最小镜像操作适配，避免核心业务 props 丢失类型约束。
 */
interface IsolatedSlotMirrorApi {
  entries(name: string): readonly StoredEntry[]
  subscribe(name: string, cb: () => void): () => void
  inject(name: string, factory: () => () => void): () => void
  register(options: Record<string, unknown>, component: unknown): () => void
}

/**
 * 首页根框 HOC：各阶段保持相同外层节点，不重建内部编辑器；仅在 hero=true 时附加隐藏样式类。
 */
export function SuperagentHeroFrame({ hero, children }: SuperagentHeroFrameProps) {
  return (
    <div className={hero ? `${css.frame} ${css.hero}` : css.frame}>
      {children}
    </div>
  )
}

/**
 * 首页模式选择器空占位组件：隐藏界面入口，后台模式服务保持运行。
 */
export function SuperagentEmptyHeroAgentPreset(_props: HeroAgentPresetOwnerProps) {
  return null
}

/**
 * 首页工作区选择器空占位组件：隐藏原工作区触发器，默认聊天/项目逻辑保持原样。
 */
export function SuperagentEmptyHeroWorkspace(_props: EmptyWorkspaceOwnerProps) {
  return null
}

interface ColdStartBootstrapProps {
  variant?: ComposerBarProps['variant'] | undefined
  occurrence?: ComposerBarProps['occurrence'] | undefined
  sessionId?: ComposerBarProps['sessionId'] | undefined
  useSessions?: ComposerBarProps['useSessions'] | undefined
  startChat?: HomeRegistrationOptions['startChat'] | undefined
  useSetupLocked?: HomeRegistrationOptions['useSetupLocked'] | undefined
  onBootstrapError?: HomeRegistrationOptions['onBootstrapError'] | undefined
}

function reportBootstrapError(
  error: unknown,
  onBootstrapError?: (error: unknown) => void,
): void {
  if (onBootstrapError) {
    try {
      onBootstrapError(error)
    } catch (reportError: unknown) {
      console.warn('超级助手冷启动默认对话错误提示回调失败:', reportError)
    }
    return
  }
  console.warn('超级助手冷启动默认对话创建失败:', error)
}

/**
 * 冷启动无选中会话时，在主 hero 视图、会话目录就绪且未锁定时自动补建一次默认聊天。
 */
function useColdStartBootstrap({
  variant,
  occurrence,
  sessionId,
  useSessions,
  startChat,
  useSetupLocked,
  onBootstrapError,
}: ColdStartBootstrapProps): void {
  const attemptedForNoSessionRef = useRef(false)
  const hasSession = Boolean(sessionId)
  const isHero = variant === 'hero'
  const isMainOccurrence = occurrence === 'main'
  const sessionCatalogReady = useSessions
    ? useSessions((sessions) => sessions?.phase === 'ready')
    : false
  const setupLocked = useSetupLocked ? useSetupLocked() : false
  const canBootstrap =
    startChat !== undefined
    && isHero
    && isMainOccurrence
    && !hasSession
    && sessionCatalogReady
    && !setupLocked

  useEffect(() => {
    if (hasSession) {
      attemptedForNoSessionRef.current = false
      return
    }
    if (!canBootstrap || attemptedForNoSessionRef.current || !startChat) {
      return
    }
    attemptedForNoSessionRef.current = true
    try {
      Promise.resolve(startChat()).catch((error: unknown) => {
        reportBootstrapError(error, onBootstrapError)
      })
    } catch (error: unknown) {
      reportBootstrapError(error, onBootstrapError)
    }
  }, [hasSession, canBootstrap, startChat, onBootstrapError])
}

/**
 * 构造复用原 DSH InputBar 的包装组件，仅转发两个新别名子槽并按首页条件覆盖提示文案。
 */
function createHomeComposerBar(
  OriginalBar: ComponentType<ComposerBarProps>,
  options?: HomeRegistrationOptions,
): ComponentType<HomeComposerBarProps> {
  const canAcceptAttachmentOwner = (owner: ComposerAttachmentsOwnerProps | null) =>
    owner !== null && Boolean(owner.onAddFiles) && owner.canAcceptDrop !== false
  const useHintPlaceholder = options?.usePlaceholder

  function HomeComposerBar(props: HomeComposerBarProps) {
    // __renders 仅为编译期可选幻象见证，剥离后避免新旧槽位别名不兼容
    const { renderSlot, __renders: _omittedRenders, ...restProps } = props
    void _omittedRenders
    const wrapperRef = useRef<HTMLDivElement>(null)
    const menuRef = useRef<HTMLDivElement>(null)
    const optionRef = useRef<HTMLButtonElement>(null)
    const fileInputRef = useRef<HTMLInputElement>(null)
    const attachmentOwnerRef = useRef<ComposerAttachmentsOwnerProps | null>(null)
    const previousActiveRef = useRef<HTMLElement | null>(null)
    const ignoreNextWrapperClickRef = useRef(false)
    const aliveRef = useRef(false)
    const pickerTokenRef = useRef(0)
    const activePickerTokenRef = useRef(0)
    const sessionId = restProps.sessionId
    const sessionRef = useRef(sessionId)
    const [menuOpen, setMenuOpen] = useState(false)
    const [canAcceptAttachments, setCanAcceptAttachments] = useState(false)
    const syncedCanAcceptRef = useRef(false)

    useColdStartBootstrap({
      variant: restProps.variant,
      occurrence: restProps.occurrence,
      sessionId,
      useSessions: restProps.useSessions,
      startChat: options?.startChat,
      useSetupLocked: options?.useSetupLocked,
      onBootstrapError: options?.onBootstrapError,
    })

    // 保持 Hook 调用顺序稳定，即使阻塞或禁用状态也同步调用注入的占位提示 Hook
    const customPlaceholder = useHintPlaceholder
      ? useHintPlaceholder(sessionId, restProps.variant)
      : undefined

    // 会话切换时在原输入条渲染新 owner 前立即作废旧选文件令牌并清空旧 owner
    if (sessionRef.current !== sessionId) {
      sessionRef.current = sessionId
      pickerTokenRef.current += 1
      attachmentOwnerRef.current = null
      if (menuOpen) setMenuOpen(false)
    }

    useEffect(() => {
      aliveRef.current = true
      return () => {
        aliveRef.current = false
        pickerTokenRef.current += 1
      }
    }, [])

    const closeMenu = useCallback((restoreFocus: boolean) => {
      const prev = previousActiveRef.current
      previousActiveRef.current = null
      setMenuOpen(false)
      if (restoreFocus && prev && prev.isConnected) {
        focusWithoutRing(prev)
      }
    }, [])

    useLayoutEffect(() => {
      const nextCanAccept = canAcceptAttachmentOwner(attachmentOwnerRef.current)
      if (nextCanAccept === syncedCanAcceptRef.current) return
      syncedCanAcceptRef.current = nextCanAccept
      setCanAcceptAttachments(nextCanAccept)
    })

    useLayoutEffect(() => {
      if (!menuOpen || !optionRef.current) return
      focusWithoutRing(optionRef.current)
    }, [menuOpen])

    useEffect(() => {
      if (!menuOpen) return
      const handlePointerDown = (event: PointerEvent) => {
        const target = event.target
        if (target instanceof Node && wrapperRef.current?.contains(target)) return
        closeMenu(true)
      }
      const handleKeyDown = (event: globalThis.KeyboardEvent) => {
        if (event.key !== 'Escape') return
        event.preventDefault()
        closeMenu(true)
      }
      document.addEventListener('pointerdown', handlePointerDown)
      document.addEventListener('keydown', handleKeyDown)
      return () => {
        document.removeEventListener('pointerdown', handlePointerDown)
        document.removeEventListener('keydown', handleKeyDown)
      }
    }, [menuOpen, closeMenu])

    const originalToggleCommandMenu = restProps.toggleCommandMenu
    const originalUseMenuLauncher = restProps.useMenuLauncher
    const originalMenuSource = originalUseMenuLauncher?.((source) => source) ?? null

    const adaptedToggleCommandMenu = useCallback<
      NonNullable<ComposerBarProps['toggleCommandMenu']>
    >(
      (selection) => {
        // 消费同一轮冒泡到外层容器的 click，避免点击 + 时当场关闭或重开
        ignoreNextWrapperClickRef.current = true
        if (menuOpen) {
          closeMenu(true)
          return
        }
        if (originalMenuSource === 'command') {
          originalToggleCommandMenu?.(selection)
        }
        previousActiveRef.current =
          document.activeElement instanceof HTMLElement ? document.activeElement : null
        setMenuOpen(true)
      },
      [menuOpen, closeMenu, originalMenuSource, originalToggleCommandMenu],
    )

    const adaptedUseMenuLauncher = useCallback<NonNullable<ComposerBarProps['useMenuLauncher']>>(
      (selector) => {
        if (!originalUseMenuLauncher) {
          return selector(menuOpen ? 'command' : null)
        }
        return originalUseMenuLauncher((source) => selector(menuOpen ? 'command' : source))
      },
      [originalUseMenuLauncher, menuOpen],
    )

    const handleMenuKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      closeMenu(true)
    }, [closeMenu])

    const handleWrapperClick = useCallback((event: MouseEvent<HTMLDivElement>) => {
      if (ignoreNextWrapperClickRef.current) {
        ignoreNextWrapperClickRef.current = false
        return
      }
      if (!menuOpen) return
      const target = event.target
      if (target instanceof Node && menuRef.current?.contains(target)) return
      const active = document.activeElement
      const shouldRestoreFocus =
        active === null
        || active === document.body
        || (menuRef.current?.contains(active) ?? false)
      closeMenu(shouldRestoreFocus)
    }, [menuOpen, closeMenu])

    const handleSelectAttachment = useCallback(() => {
      closeMenu(true)
      const owner = attachmentOwnerRef.current
      if (!aliveRef.current || !owner?.onAddFiles || owner.canAcceptDrop === false) return
      const input = fileInputRef.current
      if (!input) return
      const nextToken = pickerTokenRef.current + 1
      pickerTokenRef.current = nextToken
      activePickerTokenRef.current = nextToken
      input.value = ''
      input.click()
    }, [closeMenu])

    const handleFileInputChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
      const input = event.currentTarget
      // 必须在清空 input.value 前拷贝动态 FileList，避免实时集合被立即置空
      const files = input.files ? Array.from(input.files) : []
      input.value = ''
      if (
        !aliveRef.current
        || activePickerTokenRef.current === 0
        || activePickerTokenRef.current !== pickerTokenRef.current
      ) {
        return
      }
      activePickerTokenRef.current = 0
      if (files.length === 0) return
      const owner = attachmentOwnerRef.current
      if (!owner?.onAddFiles || owner.canAcceptDrop === false) return
      void owner.onAddFiles(files)
    }, [])

    const adaptedRenderSlot = useCallback<ComposerBarProps['renderSlot']>(
      (slotName, owner, options) => {
        if (slotName === 'conversation.input.attachments') {
          const typedOwner = owner as unknown as ComposerAttachmentsOwnerProps
          attachmentOwnerRef.current = typedOwner ?? null
          const nextCanAccept = canAcceptAttachmentOwner(attachmentOwnerRef.current)
          if (nextCanAccept !== syncedCanAcceptRef.current) {
            queueMicrotask(() => {
              if (!aliveRef.current) return
              const latestCanAccept = canAcceptAttachmentOwner(attachmentOwnerRef.current)
              if (latestCanAccept === syncedCanAcceptRef.current) return
              syncedCanAcceptRef.current = latestCanAccept
              setCanAcceptAttachments(latestCanAccept)
            })
          }
          return options === undefined
            ? renderSlot(HOME_ATTACHMENTS_SLOT, typedOwner)
            : renderSlot(HOME_ATTACHMENTS_SLOT, typedOwner, options as never)
        }
        if (slotName === 'conversation.input.overlay') {
          if (menuOpen) return null
          const typedOwner = owner as Record<string, never>
          return options === undefined
            ? renderSlot(HOME_OVERLAY_SLOT, typedOwner)
            : renderSlot(HOME_OVERLAY_SLOT, typedOwner, options as never)
        }
        // 其余旧输入区子槽（权限、计划、模型、工具栏左右扩展、dock）不渲染
        return null
      },
      [renderSlot, menuOpen],
    )

    // 在既未阻塞也未禁用时按注入提示或 hero/普通分支覆盖占位文案，阻塞或禁用保留原真实原因
    const placeholder =
      restProps.blocked === undefined && !restProps.disabled
        ? (customPlaceholder ?? resolveComposerPlaceholder({ variant: restProps.variant }))
        : restProps.placeholder

    const originalProps: ComposerBarProps = {
      ...restProps,
      ...(placeholder !== undefined ? { placeholder } : {}),
      renderSlot: adaptedRenderSlot,
      ...(originalToggleCommandMenu !== undefined
        ? { toggleCommandMenu: adaptedToggleCommandMenu }
        : {}),
      ...(originalUseMenuLauncher !== undefined
        ? { useMenuLauncher: adaptedUseMenuLauncher }
        : {}),
    }
    return (
      <div ref={wrapperRef} className={css.composerWrap} onClick={handleWrapperClick}>
        {createElement(OriginalBar, originalProps)}
        {restProps.sessionId !== undefined && restProps.variant === 'composer'
          ? renderSlot(TASK_COMPOSER_FOOTER_SLOT, {})
          : null}
        {menuOpen ? (
          <MenuSurface
            ref={menuRef}
            compact
            role="listbox"
            aria-label="添加附件"
            className={css.attachmentMenu}
            onKeyDown={handleMenuKeyDown}
          >
            <Button
              ref={optionRef}
              type="button"
              variant="ghost"
              size="sm"
              role="option"
              aria-selected={false}
              disabled={!canAcceptAttachments}
              className={css.attachmentOption}
              onClick={handleSelectAttachment}
            >
              附件 / 截图
            </Button>
          </MenuSurface>
        ) : null}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          tabIndex={-1}
          aria-hidden="true"
          className={css.hiddenFileInput}
          onChange={handleFileInputChange}
        />
      </div>
    )
  }
  return HomeComposerBar
}

/**
 * 从多个 StoredEntry 中选出优先级数值最小的有效主条目。
 */
function pickLowestPriority(entries: readonly StoredEntry[]): StoredEntry | undefined {
  let best: StoredEntry | undefined
  let bestPriority = Infinity
  for (const entry of entries) {
    const priority = entry.options?.priority ?? 0
    if (best === undefined || priority < bestPriority) {
      best = entry
      bestPriority = priority
    }
  }
  return best
}

/**
 * 仅保留允许名单内的 overlay 条目，并按 id 选出各自优先级最高（数值最小）的一项。
 */
function pickAllowedOverlays(entries: readonly StoredEntry[]): Map<string, StoredEntry> {
  const grouped = new Map<string, StoredEntry[]>()
  for (const entry of entries) {
    const id = entry.options?.id
    if (id === undefined || !ALLOWED_OVERLAY_IDS.has(id)) continue
    const list = grouped.get(id)
    if (list === undefined) {
      grouped.set(id, [entry])
    } else {
      list.push(entry)
    }
  }
  const picked = new Map<string, StoredEntry>()
  for (const [id, list] of grouped.entries()) {
    const winner = pickLowestPriority(list)
    if (winner !== undefined) picked.set(id, winner)
  }
  return picked
}

/**
 * 复制公开 StoredEntry 的注册元数据，仅改写目标槽位名称及显式覆盖字段。
 */
function buildClonedRegisterOptions(
  name: string,
  source: StoredEntry,
  overrides?: {
    priority?: number
    children?: Record<string, unknown>
  },
): Record<string, unknown> {
  const cloned: Record<string, unknown> = { name }
  const opts = source.options
  if (opts !== undefined) {
    if (opts.id !== undefined) cloned.id = opts.id
    if (opts.order !== undefined) cloned.order = opts.order
    if (opts.label !== undefined) cloned.label = opts.label
    if (opts.key !== undefined) cloned.key = opts.key
    if (opts.priority !== undefined) cloned.priority = opts.priority
  }
  if (overrides?.priority !== undefined) {
    cloned.priority = overrides.priority
  }
  if (overrides?.children !== undefined) {
    cloned.children = overrides.children
  }
  if (source.inject !== undefined) {
    cloned.inject = source.inject
  }
  if (source.locale !== undefined) {
    cloned.locale = source.locale
  }
  return cloned
}

/**
 * 通过 inject 等待目标槽位就绪后再注册，同时保证已取消的等待回调不会产生残留注册。
 */
function injectSlotRegistration(
  api: IsolatedSlotMirrorApi,
  slotName: string,
  options: Record<string, unknown>,
  component: unknown,
): () => void {
  let cancelled = false
  let disposeEntry: (() => void) | undefined
  const disposeInject = api.inject(slotName, () => {
    if (cancelled) return () => {}
    const cleanup = api.register(options, component)
    disposeEntry = cleanup
    return cleanup
  })
  return () => {
    if (cancelled) return
    cancelled = true
    disposeInject?.()
    disposeEntry?.()
    disposeEntry = undefined
  }
}

/**
 * 注册超级玩家 1-4 首页精简所需全部公共 slot，并返回统一清理函数。
 */
export function registerSuperagentHomeSlots(
  slots: HomeSlotsRegistry,
  options?: HomeRegistrationOptions,
): () => void {
  const api = slots as unknown as IsolatedSlotMirrorApi

  const disposeFrame = slots.inject('conversation.hero.frame', () => slots.register(
    { name: 'conversation.hero.frame', id: 'superagent-home-frame', order: 0, priority: -100 },
    SuperagentHeroFrame,
  ))
  const disposePreset = slots.inject('conversation.hero.agentPreset', () => slots.register(
    { name: 'conversation.hero.agentPreset', priority: -100 },
    SuperagentEmptyHeroAgentPreset,
  ))
  const disposeWorkspace = slots.inject('conversation.hero.workspace', () => slots.register(
    { name: 'conversation.hero.workspace', priority: -100 },
    SuperagentEmptyHeroWorkspace,
  ))

  let disposed = false
  let currentBarSource: StoredEntry | undefined
  let disposeBar: (() => void) | undefined
  let currentAttachmentSource: StoredEntry | undefined
  let disposeAttachment: (() => void) | undefined
  const overlayMirrors = new Map<string, { source: StoredEntry; dispose: () => void }>()
  const ownBarComponents = new Set<unknown>()

  const clearMirrors = (): void => {
    currentAttachmentSource = undefined
    if (disposeAttachment !== undefined) {
      const cleanup = disposeAttachment
      disposeAttachment = undefined
      cleanup()
    }
    for (const mirror of overlayMirrors.values()) {
      mirror.dispose()
    }
    overlayMirrors.clear()
  }

  const syncAttachments = (): void => {
    if (disposed || disposeBar === undefined) return
    const nextSource = pickLowestPriority(api.entries('conversation.input.attachments'))
    if (nextSource === currentAttachmentSource) return
    if (disposeAttachment !== undefined) {
      const cleanup = disposeAttachment
      disposeAttachment = undefined
      cleanup()
    }
    currentAttachmentSource = nextSource
    if (nextSource === undefined) return
    disposeAttachment = injectSlotRegistration(
      api,
      HOME_ATTACHMENTS_SLOT,
      buildClonedRegisterOptions(HOME_ATTACHMENTS_SLOT, nextSource),
      nextSource.component,
    )
  }

  const syncOverlays = (): void => {
    if (disposed || disposeBar === undefined) return
    const nextById = pickAllowedOverlays(api.entries('conversation.input.overlay'))
    for (const [id, existing] of [...overlayMirrors.entries()]) {
      const nextSource = nextById.get(id)
      if (nextSource === undefined || nextSource !== existing.source) {
        overlayMirrors.delete(id)
        existing.dispose()
      }
    }
    for (const [id, nextSource] of nextById.entries()) {
      if (overlayMirrors.has(id)) continue
      const dispose = injectSlotRegistration(
        api,
        HOME_OVERLAY_SLOT,
        buildClonedRegisterOptions(HOME_OVERLAY_SLOT, nextSource),
        nextSource.component,
      )
      overlayMirrors.set(id, { source: nextSource, dispose })
    }
  }

  const syncBar = (): void => {
    if (disposed) return
    const candidates = api
      .entries('conversation.composer.bar')
      .filter((entry) => !ownBarComponents.has(entry.component))
    const nextSource = pickLowestPriority(candidates)
    if (nextSource === currentBarSource) return

    clearMirrors()
    if (disposeBar !== undefined) {
      const cleanup = disposeBar
      disposeBar = undefined
      cleanup()
    }

    // 先更新目标引用并记录自有组件，防止同步注册触发 subscribe 重入死循环
    currentBarSource = nextSource
    if (nextSource === undefined) return

    const wrapperComponent = createHomeComposerBar(
      nextSource.component as ComponentType<ComposerBarProps>,
      options,
    )
    ownBarComponents.add(wrapperComponent)
    const sourcePriority = nextSource.options?.priority ?? 0
    const barOptions = buildClonedRegisterOptions('conversation.composer.bar', nextSource, {
      priority: Math.min(sourcePriority, 0) - 100,
      children: HOME_BAR_CHILDREN,
    })
    disposeBar = injectSlotRegistration(
      api,
      'conversation.composer.bar',
      barOptions,
      wrapperComponent,
    )

    syncAttachments()
    syncOverlays()
  }

  const unsubBar = api.subscribe('conversation.composer.bar', syncBar)
  const unsubAttachments = api.subscribe('conversation.input.attachments', syncAttachments)
  const unsubOverlays = api.subscribe('conversation.input.overlay', syncOverlays)

  syncBar()

  return () => {
    if (disposed) return
    disposed = true
    unsubBar()
    unsubAttachments()
    unsubOverlays()
    clearMirrors()
    currentBarSource = undefined
    if (disposeBar !== undefined) {
      const cleanup = disposeBar
      disposeBar = undefined
      cleanup()
    }
    disposeFrame()
    disposePreset()
    disposeWorkspace()
  }
}
