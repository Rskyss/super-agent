/**
 * 超级助手 · Host 半侧（第 1 项：正式接口 + 看管后台服务）。
 *
 * - 启动时拉起现有超级助手的本机服务 desktop/server.py（随机端口 + 一次性口令），
 *   插件卸载时关掉它；取代原来 Swift 窗口外壳做的这件事。
 * - 通过 DSH 正式 Remote 接口（namespace `superagent`）把卡片列表、进度、方案、
 *   卡片操作交给浏览器 / 桌面版界面。干活一律在现有后台，这里只做转发。
 */
import { randomUUID } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { lstat, open, realpath, stat } from 'node:fs/promises'
import { homedir } from 'node:os'
import { join, resolve, sep } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import { resolveDshHome } from '@deepseek-ai/dsh-home-paths'
import type { JobId, JobRegistry, JobView } from '@deepseek-ai/dsh-jobs'
import type { SessionId } from '@deepseek-ai/dsh-session'
import z from '@deepseek-ai/schemastery'
import { Remote, RemoteError, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { BackendProcess, type Backend } from './backend.ts'
import { readCardAttachmentOnHost, validateImagePayloads } from './card-attachments.ts'
import { registerPreviewJob, type PreviewJobsPort } from './preview-jobs.ts'
import { cardFieldsFromProposal } from './proposal.ts'
import { bindTaskSession, cardOfTaskSession, cardsFromConversation, readTaskSessions, taskSessionIdFor, taskSessionOf } from './task-session.ts'
import { readCardTrajectory, sanitizeTrajectoryText } from './trajectory.ts'
import { FINISHED_CARD_STATES, FINISHED_PHASE_CODES, type BackendStatus, type CardActionOptions, type CardActionResult, type CardKind, type CardRow, type CardTrajectory, type CardViewPayload, type CreatedCard, type CriteriaChangeItem, type DispatcherConnectOptions, type DispatcherConnectResult, type DispatcherProposal, type DispatcherState, type DispatcherTool, type ImagePayload, type OnboardingModelItem, type OnboardingSetupPayload, type OnboardingSetupSummary, type OnboardingState, type PendingChange, type PrepareRevisionResult, type PreviewResult, type PreviewStatus, type Proposal, type RequestChangeOptions, type TaskSessionBind, type TaskSessionInfo } from './types.ts'
import { formatSafeOnboardingError } from './onboarding.ts'
import { completeOnboardingOnHost, discoverOnboardingModelsOnHost, readOnboardingStateOnHost, resolveLegacyOnboardingRequestOnHost } from './onboarding-host.ts'
import { readLegacyOptions, type LegacyKeyOption } from './legacy-keys.ts'
import { buildCriteriaSummary, hasCriteriaRevisionInFlow, isGeneratedAttachmentMarkdownLine, validateCriteriaChanges } from './revision.ts'
import { prepareRevisionOnHost, readCardAcceptanceCriteria } from './revision-host.ts'
import {
  computeElapsedFromFlow,
  computeWorkElapsedFromFlowText,
  isCardRunningNow,
  parseFlowEvents,
  parseStrictDateParts,
  type ParsedFlowEvent,
} from './work-duration.ts'

export type {
  BackendStatus, CardActionOptions, CardActionResult, CardKind, CardPlanPayload, CardRow, CardTrajectory, CardViewPayload, CreatedCard, CriteriaChangeItem,
  DispatcherConnectOptions, DispatcherConnectResult, DispatcherProposal, DispatcherState, DispatcherTool,
  ImageMediaType, ImagePayload, OnboardingModelItem, OnboardingSetupPayload, OnboardingSetupSummary, OnboardingState,
  ParsedCardProgress, PendingChange, PrepareRevisionResult, PreviewResult, PreviewStatus, ProgressStep, ProgressStepKey,
  ProgressStepState, Proposal, ProposalQuestion, RequestChangeOptions, TaskSessionBind, TaskSessionInfo, TrajectoryAttachment, TrajectoryDistributionItem,
  TrajectoryRow, ViewAcceptInfo, ViewCard, ViewPhase, ViewStation,
} from './types.ts'

declare module '@deepseek-ai/cordis' {
  interface Context {
    superagentGateway: SuperagentGateway
  }
}

declare module '@deepseek-ai/dsh-typert-protocol' {
  interface RemoteErrorDetailsMap {
    /** 后台拒绝了这次请求（参数不对、卡片不存在、当前不能做这个操作）。 */
    'superagent/rejected': { readonly status: number; readonly message: string }
  }
}

/** 插件配置。 */
export interface Config {
  /** 现有超级助手代码仓库位置（里面有 desktop/server.py）。 */
  repoRoot: string
  /** 当前项目文件夹（第一段先固定一个，后面按会话的工作区走）。 */
  project: string
  /** 启动 Python 用的命令。 */
  python: string
  /** 等后台报出端口的最长时间。 */
  startTimeoutMs: number
  /** DSH 隔离积木库目录（优先级：配置/SUPERAGENT_DSH_BLOCKS_HOME > DSH_HOME 下独立目录，绝不继承旧库）。 */
  blocksHome: string
}

const DEFAULT_REPO = join(homedir(), 'Desktop', 'project', 'super-agent')

function defaultBlocksHome(): string {
  const override = (process.env.SUPERAGENT_DSH_BLOCKS_HOME ?? '').trim()
  return override || join(resolveDshHome(), 'superagent-blocks')
}

/** 看管现有后台服务并对外提供正式接口。 */
export default class SuperagentGateway extends TypertRemoteService {
  static inject = ['jobs', 'typert']

  static Config: z<Partial<Config>, Config> = z.object({
    repoRoot: z.string().default(DEFAULT_REPO),
    project: z.string().default(process.env.SUPERAGENT_PROJECT ?? ''),
    python: z.string().default('python3'),
    startTimeoutMs: z.natural().min(1000).max(60000).default(15000),
    blocksHome: z.string().default(defaultBlocksHome()),
  })

  private readonly process: BackendProcess
  /** 正在提交的改要求，防止连点。 */
  private readonly submitting = new Set<string>()
  /** 每个会话最多一张等你确认的确认卡（一次只出一张卡）。 */
  private readonly proposals = new Map<string, Proposal>()
  /** 每张任务卡最多一条等你确认的改要求。 */
  private readonly changes = new Map<string, PendingChange>()

  constructor(ctx: Context, private readonly config: Config) {
    super(ctx, 'superagentGateway', { namespace: 'superagent' })
    const effectiveBlocksHome = (config.blocksHome ?? '').trim() || defaultBlocksHome()
    this.process = new BackendProcess({
      python: config.python,
      script: join(config.repoRoot, 'desktop', 'server.py'),
      cwd: config.repoRoot,
      startTimeoutMs: config.startTimeoutMs,
      env: { SUPER_AGENT_BLOCKS_HOME: effectiveBlocksHome },
    })
    const previewState = createGatewayPreviewState()
    ;(this as unknown as PreviewStateCarrier)[PREVIEW_STATE] = previewState
    ;(this as unknown as DispatcherStateCarrier)[DISPATCHER_STATE] = createGatewayDispatcherState()

    let detachController: (() => void) | undefined
    try {
      const jobs = ctx.jobs
      if (jobs && typeof jobs.start === 'function') {
        previewState.jobs = jobs
        if (typeof jobs.attachController === 'function') {
          detachController = jobs.attachController('superagent-preview')
        }
      }
    } catch {
      // 保护性捕获，避免未注入环境阻塞构造；实际使用时在 resolveGatewayJobs 再检查并回滚。
    }

    ctx.effect(() => () => {
      void this.cleanupAllPreviewJobs()
      detachController?.()
      this.process.stop()
    })
    // 随 Host 启动就拉起后台（复查第 1 轮中等 4）；起不来不挡 Host，用到时会再试。
    if (config.project) void this.ensure().catch(() => {})
  }

  /** @returns 后台服务是否在跑、端口、当前项目。 */
  @Remote('status')
  async status(): Promise<BackendStatus> {
    try {
      const backend = await this.ensure()
      return { running: true, port: backend.port, project: this.config.project }
    } catch (_error) {
      return { running: false, port: 0, project: this.config.project }
    }
  }

  /** @returns 当前项目的全部任务卡（含阶段、真实流转统计与归档标记，未知不编造）。 */
  @Remote('cards')
  async cards(): Promise<CardRow[]> {
    const raw = await this.get('/api/cards', {})
    if (!Array.isArray(raw)) return []
    const project = this.config?.project ?? ''
    return raw.map((item: Record<string, unknown>) => {
      const phase = (item.phase ?? {}) as Record<string, unknown>
      const id = String(item.id ?? '')
      const state = sanitizeTrajectoryText(String(item.state ?? ''))
      const phaseCode = String(phase.code ?? '')
      const phaseLabel = sanitizeTrajectoryText(String(phase.label ?? ''))
      const needsYou = phase.needs_you === true
      const actions = Array.isArray(phase.actions) ? phase.actions.map(String) : []
      const updated = String(item.updated ?? '')
      const meta = readCardOverviewMeta(project, id, state, phaseCode, phase.tone, needsYou, item)
      return {
        id,
        title: sanitizeTrajectoryText(String(item.title ?? '')),
        state,
        phaseCode,
        phaseLabel,
        needsYou,
        actions,
        updated,
        ...meta,
      }
    })
  }

  /**
   * @param card - 卡片编号。
   * @returns 这张卡的进度（JSON 文字，界面自己解析；补充真实流转计算的 workElapsed 及验收标准和追加要求）。
   */
  @Remote('view')
  async view(card: string): Promise<string> {
    const safeCard = checkCard(card)
    const raw = await this.get('/api/view', { card: safeCard })
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
      const payload = raw as Record<string, unknown>
      const project = this.config?.project ?? ''
      const flowText = readSafeMetaText(project, safeCard, '流转.jsonl')
      const cardObj = payload.card && typeof payload.card === 'object' && !Array.isArray(payload.card)
        ? (payload.card as Record<string, unknown>)
        : null
      const phaseObj = cardObj?.phase && typeof cardObj.phase === 'object' && !Array.isArray(cardObj.phase)
        ? (cardObj.phase as Record<string, unknown>)
        : null
      const state = sanitizeTrajectoryText(String(cardObj?.state ?? ''))
      const phaseCode = String(phaseObj?.code ?? '')
      const phaseTone = phaseObj?.tone
      const needsYou = typeof phaseObj?.needs_you === 'boolean'
        ? phaseObj.needs_you
        : VIEW_NEEDS_YOU_PHASES.has(phaseCode)
      const isRunningNow = isCardRunningNow(state, phaseCode, phaseTone, needsYou)
      const computedWorkElapsed = computeWorkElapsedFromFlowText(flowText, isRunningNow, Date.now())
      const explicitWorkElapsed = typeof payload.workElapsed === 'number' && Number.isFinite(payload.workElapsed) && payload.workElapsed >= 0
        ? payload.workElapsed
        : null
      const workElapsed = computedWorkElapsed ?? explicitWorkElapsed
      const nextPayload: Record<string, unknown> = { ...payload, workElapsed }

      if (payload.accept && typeof payload.accept === 'object' && !Array.isArray(payload.accept)) {
        const accept = { ...(payload.accept as Record<string, unknown>) }
        const parsed = readCardAcceptanceCriteria(project, safeCard)
        if ((!Array.isArray(accept.criteria) || accept.criteria.length === 0) && parsed.criteria.length > 0) {
          accept.criteria = parsed.criteria
        }
        if (Array.isArray(accept.changeNotes) && accept.changeNotes.length > 0) {
          accept.changeNotes = accept.changeNotes
            .map(item => String(item ?? ''))
            .filter(item => item.trim() !== '' && !isGeneratedAttachmentMarkdownLine(item))
        }
        if ((!Array.isArray(accept.changeNotes) || accept.changeNotes.length === 0) && parsed.changeNotes.length > 0) {
          accept.changeNotes = parsed.changeNotes
        }
        if (accept.criteriaUpdated !== true && hasCriteriaRevisionInFlow(flowText)) {
          accept.criteriaUpdated = true
        }
        nextPayload.accept = accept
      }
      return JSON.stringify(nextPayload)
    }
    return JSON.stringify(raw)
  }

  /**
   * @param card - 卡片编号。
   * @returns 这张卡的方案（JSON 文字，附带流转计数得到的方案版本号）。
   */
  @Remote('plan')
  async plan(card: string): Promise<string> {
    const safeCard = checkCard(card)
    const raw = await this.get('/api/plan', { card: safeCard })
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
      const payload = raw as Record<string, unknown>
      const hasServerVersion = typeof payload.version === 'number' && Number.isFinite(payload.version) && payload.version >= 1
      const version = hasServerVersion
        ? payload.version as number
        : readPlanVersion(this.config.project, safeCard, typeof payload.plan === 'string' ? payload.plan : '')
      return JSON.stringify({ ...payload, version })
    }
    return JSON.stringify(raw)
  }

  /**
   * @param card - 卡片编号。
   * @returns 这张卡的流转轨迹与明确耗时分布。
   */
  @Remote('trajectory')
  async trajectory(card: string): Promise<CardTrajectory> {
    const safeCard = checkCard(card)
    const project = this.config?.project ?? ''
    if (!project) return { rows: [], distribution: [] }
    return readCardTrajectory(project, safeCard)
  }

  /**
   * 安全读取指定任务卡流转中已登记的一张截图原图（供轨迹看图）。
   * 仅限当前配置项目下本卡附件目录内的时间命名图片，拒绝软链与目录逃逸。
   * @param card - 卡片编号。
   * @param name - 附件文件名（如 `202611161030000001.png`）。
   * @returns 图片媒体类型与原始 base64。
   */
  @Remote('cardAttachment')
  async cardAttachment(card: string, name: string): Promise<ImagePayload> {
    const safeCard = checkCard(card)
    try {
      return await readCardAttachmentOnHost(this.config?.project ?? '', safeCard, name)
    } catch (error: unknown) {
      if (error instanceof RemoteError) throw error
      const msg = error instanceof Error && error.message ? error.message : '读取截图失败'
      throw new RemoteError('superagent/rejected', msg, { status: 400, message: msg })
    }
  }

  /**
   * 卡片操作：批准方案、按我说的改、先不做、放进项目、接受剩下的问题、再跑一次、停下。
   * @param card - 卡片编号。
   * @param action - 操作名，和现有后台一致。
   * @param text - “按我说的改”时写的话，其他操作传空字符串。
   * @returns 是否成功和给人看的一句话。
   */
  @Remote('cardAction')
  async cardAction(
    card: string,
    action: string,
    text: string,
    options?: CardActionOptions,
  ): Promise<CardActionResult> {
    if (!CARD_ACTIONS.has(action)) return { ok: false, message: '不认识的操作' }
    const safeCard = checkCard(card)
    const body: Record<string, unknown> = { card: safeCard, action, text }
    if (options?.attachments !== undefined) {
      let validatedAttachments: ImagePayload[]
      try {
        validatedAttachments = validateImagePayloads(options.attachments)
      } catch (error: unknown) {
        const msg = error instanceof Error && error.message ? error.message : '截图格式或大小不合规'
        throw new RemoteError('superagent/rejected', msg, { status: 400, message: msg })
      }
      if (validatedAttachments.length > 0) {
        if (action !== 'revise') {
          throw new RemoteError('superagent/rejected', '只有修改操作支持附带截图', { status: 400, message: '只有修改操作支持附带截图' })
        }
        body.attachments = validatedAttachments
      }
    }
    if (action === 'revise' && options && (options.criteriaBase !== undefined || options.criteriaChanges !== undefined)) {
      if (options.criteriaBase !== undefined) body.criteria_base = options.criteriaBase
      if (options.criteriaChanges !== undefined) body.criteria_changes = options.criteriaChanges
    }
    const value = await this.post('/api/clerk/card', body) as Record<string, unknown>
    const ok = value.ok === true
    if (ok && PREVIEW_CLEANUP_ACTIONS.has(action)) {
      await this.cleanupCardPreviewJob(safeCard)
    }
    return { ok, message: String(value.error ?? '') }
  }

  /**
   * 启动或停止当前任务卡的预览。启动后台最多等 45 秒，这里留出 60 秒超时；普通请求仍维持 20 秒。
   * 启动网页预览成功后登记到 DSH `ctx.jobs`；若无法登记（无服务、未绑定会话或登记报错），立即停止刚起的预览并返回失败原因。
   * @param card - 卡片编号。
   * @param action - 预览动作，仅允许 start 或 stop。
   * @returns 预览结果，包含是否成功、模式、本机网页地址或失败原因。
   */
  @Remote('preview')
  async preview(card: string, action: string): Promise<PreviewResult> {
    const safeCard = checkCard(card)
    if (action !== 'start' && action !== 'stop') return { ok: false, reason: '预览操作不对' }

    const state = getGatewayPreviewState(this)
    if (action === 'stop') {
      if (state.startingCard !== null) {
        return { ok: false, reason: '预览正在启动，请稍候再试' }
      }
      if (state.stoppingCard !== null) {
        return { ok: false, reason: '预览正在停止，请稍候再试' }
      }
      state.stoppingCard = safeCard
      try {
        return await this.stopPreviewWithTracking(safeCard)
      } finally {
        state.stoppingCard = null
      }
    }

    if (state.startingCard !== null) {
      return {
        ok: false,
        reason: state.startingCard === safeCard
          ? '预览正在启动，请勿重复点击'
          : `任务 ${state.startingCard} 的预览正在启动，请稍候`,
      }
    }
    if (state.stoppingCard !== null) {
      return { ok: false, reason: '预览正在停止，请稍候再启动' }
    }

    state.startingCard = safeCard
    try {
      // 后端全局同一时间只有一个预览进程；先停止并确认清理已有预览，再启动新预览，避免误杀新进程。
      if (state.tracked.size > 0) {
        const existingCards = Array.from(state.tracked.keys())
        for (const oldCard of existingCards) {
          const stopRes = await this.stopPreviewWithTracking(oldCard)
          if (!stopRes.ok) {
            return { ok: false, reason: stopRes.reason ?? '上一个预览还没关掉，暂时不能启动新预览' }
          }
        }
      }

      let value: Record<string, unknown>
      try {
        const raw = await this.request('/api/clerk/preview', {}, { card: safeCard, action: 'start' }, PREVIEW_START_TIMEOUT_MS)
        value = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
      } catch (error: unknown) {
        const reason = error instanceof Error ? error.message : '预览请求失败'
        return { ok: false, reason: reason || '预览请求失败' }
      }

      if (value.ok !== true) {
        return { ok: false, reason: String(value.reason ?? value.error ?? '打不开预览') }
      }

      const mode = value.mode === 'web' || value.mode === 'folder' ? value.mode : undefined
      const rawUrl = typeof value.url === 'string' ? value.url : undefined
      if (mode === 'web') {
        if (!rawUrl || !isLocalHttpUrl(rawUrl)) {
          return {
            ok: false,
            reason: await this.stopAndFormatRollbackReason(safeCard, '预览地址不是本机网页地址'),
          }
        }
        const jobs = resolveGatewayJobs(this, state)
        if (!jobs || typeof jobs.start !== 'function') {
          return {
            ok: false,
            reason: await this.stopAndFormatRollbackReason(safeCard, '后台任务服务未启用，无法登记预览任务'),
          }
        }
        const project = this.config?.project ?? ''
        const owner = project ? taskSessionOf(project, safeCard) : ''
        if (!owner) {
          return {
            ok: false,
            reason: await this.stopAndFormatRollbackReason(safeCard, '任务尚未绑定会话，无法登记后台预览任务'),
          }
        }

        let record: TrackedPreviewJob | null = null
        let registered: ReturnType<typeof registerPreviewJob>
        try {
          registered = registerPreviewJob({
            jobs,
            card: safeCard,
            owner: owner as SessionId,
            url: rawUrl,
            label: readCardPreviewLabel(project, safeCard),
            stop: () => this.stopBackendPreview(safeCard),
            status: () => this.previewStatus(safeCard),
            readOutput: () => readCardPreviewLog(project, safeCard),
            onSettled: () => {
              if (!record || state.tracked.get(safeCard) === record) {
                state.tracked.delete(safeCard)
              }
            },
          })
        } catch (error: unknown) {
          const detail = error instanceof Error && error.message ? error.message : '登记后台任务失败'
          return {
            ok: false,
            reason: await this.stopAndFormatRollbackReason(safeCard, `后台任务登记失败（${detail}）`),
          }
        }

        record = {
          card: safeCard,
          jobId: registered,
          owner: owner as SessionId,
          url: rawUrl,
        }
        state.tracked.set(safeCard, record)
        return { ok: true, mode, url: rawUrl }
      }

      const path = typeof value.path === 'string' ? value.path : undefined
      return { ok: true, ...(mode ? { mode } : {}), ...(path ? { path } : {}) }
    } finally {
      state.startingCard = null
    }
  }

  private async stopAndFormatRollbackReason(safeCard: string, prefix: string): Promise<string> {
    const stopped = await this.stopBackendPreview(safeCard)
    if (stopped.ok) return `${prefix}，已停止预览`
    const stopDetail = stopped.reason?.trim() || '停止预览失败'
    return `${prefix}，且停止预览失败：${stopDetail}`
  }

  private async stopBackendPreview(safeCard: string): Promise<{ ok: boolean; reason?: string }> {
    try {
      const raw = await this.request('/api/clerk/preview', {}, { card: safeCard, action: 'stop' }, DEFAULT_REQUEST_TIMEOUT_MS)
      const value = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
      if (value.ok === true) return { ok: true }
      return { ok: false, reason: String(value.reason ?? value.error ?? '停止预览失败') }
    } catch (error: unknown) {
      const reason = error instanceof Error && error.message ? error.message : '停止预览请求失败'
      return { ok: false, reason }
    }
  }

  private async stopPreviewWithTracking(safeCard: string): Promise<PreviewResult> {
    const state = getGatewayPreviewState(this)
    const tracked = state.tracked.get(safeCard)
    const jobs = resolveGatewayJobs(this, state)
    if (tracked && jobs && typeof jobs.kill === 'function') {
      let killStatus: 'requested' | 'already-finished' | null = null
      try {
        killStatus = jobs.kill(tracked.jobId as JobId, tracked.owner, '停止预览')
      } catch {
        killStatus = null
      }
      if (killStatus === 'requested') {
        if (typeof jobs.wait === 'function') {
          let view: JobView
          try {
            view = await jobs.wait(tracked.jobId as JobId, DEFAULT_REQUEST_TIMEOUT_MS, tracked.owner)
          } catch (error: unknown) {
            const reason = error instanceof Error && error.message ? error.message : '等待停止预览失败'
            return { ok: false, reason }
          }
          if (view.status === 'running' || view.status === 'stopping') {
            return { ok: false, reason: '停止预览超时，后台仍在停止中，请稍后重试' }
          }
          if (view.status === 'failed') {
            return { ok: false, reason: view.detail?.trim() || '停止预览失败' }
          }
          state.tracked.delete(safeCard)
          return { ok: true }
        }
        const stopped = await this.stopBackendPreview(safeCard)
        if (stopped.ok) {
          state.tracked.delete(safeCard)
          return { ok: true }
        }
        return { ok: false, reason: stopped.reason ?? '停止预览失败' }
      }
    }

    const stopped = await this.stopBackendPreview(safeCard)
    if (stopped.ok) {
      state.tracked.delete(safeCard)
      return { ok: true }
    }
    return { ok: false, reason: stopped.reason ?? '停止预览失败' }
  }

  private async cleanupCardPreviewJob(safeCard: string): Promise<void> {
    const state = getGatewayPreviewState(this)
    const tracked = state.tracked.get(safeCard)
    if (!tracked) return
    try {
      await this.stopPreviewWithTracking(safeCard)
    } catch {
      await this.stopBackendPreview(safeCard).catch(() => {})
    }
  }

  private async cleanupAllPreviewJobs(): Promise<void> {
    const state = getGatewayPreviewState(this)
    const cards = Array.from(state.tracked.keys())
    await Promise.allSettled(cards.map(card => this.cleanupCardPreviewJob(card)))
  }

  /**
   * 查询指定任务卡当前的预览进程状态（留给后续工作区复用）。
   * 后台查询异常时不吞错返回 running:false，由轮询方保留既有运行状态。
   * @param card - 卡片编号。
   * @returns 当前是否有预览在运行及可信的本机网页地址（没有可用预览时为空字符串）。
   */
  @Remote('previewStatus')
  async previewStatus(card: string): Promise<PreviewStatus> {
    const safeCard = checkCard(card)
    const raw = await this.get('/api/clerk/preview', { card: safeCard })
    const value = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
    const rawUrl = typeof value.url === 'string' ? value.url : ''
    return {
      running: value.running === true,
      url: isLocalHttpUrl(rawUrl) ? rawUrl : '',
    }
  }

  // ---------------------------------------------------------------- 确认卡、改要求（工具写入，界面读取）

  /**
   * 超级助手出一张确认卡；同一会话里旧的那张作废（一次只出一张卡）。
   * 只给工具用，不对界面开放。
   */
  propose(session: string, draft: Omit<Proposal, 'id' | 'session' | 'createdAt'>): Proposal {
    // 同一会话同一时刻只留一张卡（互斥“接调度员”确认卡）。
    getGatewayDispatcherState(this).proposals.delete(session)
    const proposal: Proposal = { ...draft, id: randomUUID(), session, createdAt: Date.now() }
    this.proposals.set(session, proposal)
    return proposal
  }

  /**
   * @param sessionId - 会话编号。
   * @returns 这个会话里等你确认的确认卡；没有时返回 null。
   */
  @Remote('proposal')
  async proposal(sessionId: string): Promise<Proposal | null> {
    return this.proposals.get(sessionId) ?? null
  }

  /**
   * 你点了“先不做”：撤掉这张确认卡。（“确认，建任务”在第 3 项接上。）
   * @param sessionId - 会话编号。
   * @param id - 确认卡编号。
   * @returns 是否撤掉了。
   */
  @Remote('dismissProposal')
  async dismissProposal(sessionId: string, id: string): Promise<boolean> {
    if (this.proposals.get(sessionId)?.id !== id || this.submitting.has(id)) return false
    this.proposals.delete(sessionId)
    return true
  }

  /**
   * 你点了“确认，建任务”：建卡并开始写方案。问号没答的按建议写进卡里。
   * 成功才撤掉确认卡；失败时留着，可以再点。
   * @param sessionId - 会话编号。
   * @param id - 确认卡编号。
   * @returns 建出的卡号，或失败原因。
   */
  @Remote('confirmProposal')
  async confirmProposal(sessionId: string, id: string): Promise<CreatedCard> {
    const proposal = this.proposals.get(sessionId)
    if (proposal?.id !== id) return { ok: false, card: '', planning: false, message: '这张确认卡已经处理过了' }
    if (this.submitting.has(id)) return { ok: false, card: '', planning: false, message: '正在建卡，请稍等' }
    this.submitting.add(id)
    try {
      const dispatcher = await this.dispatcherStatus()
      const deferPlan = dispatcher !== 'ready'
      // 带上确认卡编号：网络断了再点一次，后台认得是同一个请求，只建一张（复查第 3 项中等 1）。
      const value = await this.post('/api/cards/new', {
        ...cardFieldsFromProposal(proposal),
        conversation: sessionId,
        request_id: id,
        ...(deferPlan ? { defer_plan: true } : {}),
      }) as Record<string, unknown>
      if (this.proposals.get(sessionId)?.id === id) this.proposals.delete(sessionId)
      const planning = value.planning === true
      const rawMessage = String(value.message ?? '')
      const message = rawMessage || (deferPlan && !planning ? '卡已建好，接好调度员后开始写方案' : '')
      return { ok: true, card: String(value.card ?? ''), planning, message }
    } catch (error: unknown) {
      return { ok: false, card: '', planning: false, message: error instanceof Error ? error.message : String(error) }
    } finally {
      this.submitting.delete(id)
    }
  }

  /**
   * @returns 当前项目有没有调度员：ready 有、missing 没有（确认卡上多一行提醒，03c）、
   * unknown 这次没查到（不提醒，过一会儿再查；复查第 3 项中等 3）。
   */
  @Remote('dispatcherStatus')
  async dispatcherStatus(): Promise<'ready' | 'missing' | 'unknown'> {
    try {
      const value = await this.get('/api/dsh/dispatcher/status', {}) as { status?: string; verified?: boolean }
      if (value.status === 'ready' && value.verified === true) return 'ready'
      return 'missing'
    } catch (_error) {
      return 'unknown'
    }
  }

  /** 读取调度员完整真实状态（只看配置与缓存指纹，不触发 CLI）。 */
  @Remote('dispatcherState')
  async dispatcherState(): Promise<DispatcherState> {
    try {
      const value = await this.get('/api/dsh/dispatcher/status', {}) as Record<string, unknown>
      const status = value.status === 'ready' && value.verified === true ? 'ready' : 'missing'
      return {
        status,
        tool: String(value.tool ?? ''),
        label: String(value.label ?? ''),
        model: String(value.model ?? ''),
        reason: String(value.reason ?? ''),
        verified: value.verified === true,
      }
    } catch (error: unknown) {
      return {
        status: 'unknown',
        tool: '',
        label: '',
        model: '',
        reason: error instanceof Error ? error.message : String(error),
        verified: false,
      }
    }
  }

  /** 生成或替换当前会话“接调度员”确认卡（供工具调用；一次只留一张）。 */
  proposeDispatcher(session: string, tool: DispatcherTool, card: string = ''): DispatcherProposal {
    // 同一会话同一时刻只留一张卡（互斥任务创建确认卡）。
    this.proposals.delete(session)
    const state = getGatewayDispatcherState(this)
    const proposal: DispatcherProposal = {
      ...staticDispatcherDraft(tool, card),
      id: randomUUID(),
      session,
      createdAt: Date.now(),
    }
    state.proposals.set(session, proposal)
    return proposal
  }

  /** 读取当前会话里等你确认的“接调度员”确认卡。 */
  @Remote('dispatcherProposal')
  async dispatcherProposal(sessionId: string): Promise<DispatcherProposal | null> {
    return getGatewayDispatcherState(this).proposals.get(sessionId) ?? null
  }

  /** 撤掉当前会话的“接调度员”确认卡（先不做）。 */
  @Remote('dismissDispatcherProposal')
  async dismissDispatcherProposal(sessionId: string, id: string): Promise<boolean> {
    const state = getGatewayDispatcherState(this)
    if (state.proposals.get(sessionId)?.id !== id || state.connecting.has(id)) return false
    state.proposals.delete(sessionId)
    return true
  }

  /** 生成指定工具的接调度员确认卡说明（不落会话表，用于任务卡首卡自动提示）。 */
  @Remote('dispatcherDraft')
  async dispatcherDraft(tool: DispatcherTool, card?: string): Promise<DispatcherProposal> {
    const cleanCard = card ?? ''
    const safeTool: DispatcherTool = tool === 'agy' ? 'agy' : 'codex'
    try {
      const value = await this.post('/api/dsh/dispatcher/propose', { tool: safeTool, card: cleanCard }) as { proposal?: Record<string, unknown> }
      const p = value.proposal
      if (p && typeof p === 'object') {
        return {
          id: `draft-${safeTool}`,
          session: '',
          tool: p.tool === 'agy' ? 'agy' : 'codex',
          label: String(p.label ?? (safeTool === 'agy' ? 'agy' : 'Codex')),
          card: String(p.card ?? cleanCard),
          title: String(p.title ?? ''),
          what: String(p.what ?? ''),
          how: String(p.how ?? ''),
          needYou: String(p.needYou ?? ''),
          scope: String(p.scope ?? ''),
          createdAt: Date.now(),
        }
      }
    } catch {
      // 后台暂未回应时使用同一套大白话草案，避免界面空白。
    }
    return {
      ...staticDispatcherDraft(safeTool, cleanCard),
      id: `draft-${safeTool}`,
      session: '',
      createdAt: Date.now(),
    }
  }

  /**
   * 用户点确认接调度员：只在明确确认时跑一个检测小任务（通过后台 job 轮询，同项目同目标并发幂等）。
   * - 传 proposalId 时校验确认卡必须存在且 id 匹配；未传 proposalId 时只允许合法任务卡直接发起；
   * - 检测通过：保存新版积木库 + 保存项目调度员角色 + 仅启动提出接入的未写方案卡；清掉匹配的确认卡。
   * - 检测失败：不保存角色、保留确认卡、保留原状态与真实失败原因。
   */
  @Remote('connectDispatcher')
  async connectDispatcher(
    tool: DispatcherTool,
    options?: DispatcherConnectOptions,
  ): Promise<DispatcherConnectResult> {
    const cleanTool = String(tool ?? '').trim().toLowerCase()
    const rawCard = options?.card ?? ''
    if (cleanTool !== 'codex' && cleanTool !== 'agy') {
      return {
        ok: false, status: 'missing', tool: cleanTool, label: cleanTool, model: '',
        reason: '调度员只支持订阅工具 Codex 或 agy', startedPlan: false, planNote: '', card: rawCard,
      }
    }
    const label = cleanTool === 'agy' ? 'agy' : 'Codex'
    const state = getGatewayDispatcherState(this)
    const hasProposalId = options?.proposalId !== undefined
    if (hasProposalId) {
      const existing = options.sessionId ? state.proposals.get(options.sessionId) : undefined
      if (!existing || existing.id !== options.proposalId) {
        return {
          ok: false, status: 'missing', tool: cleanTool, label, model: '',
          reason: '这张接入确认卡已失效或已更换，请重新发起确认', startedPlan: false, planNote: '', card: rawCard,
        }
      }
    }

    let targetCard = ''
    try {
      targetCard = rawCard ? checkCard(rawCard) : ''
    } catch (error: unknown) {
      return {
        ok: false, status: 'missing', tool: cleanTool, label, model: '',
        reason: error instanceof Error ? error.message : String(error), startedPlan: false, planNote: '', card: rawCard,
      }
    }
    if (!hasProposalId && !targetCard) {
      return {
        ok: false, status: 'missing', tool: cleanTool, label, model: '',
        reason: '需要指定有效的任务卡后才能直接接入调度员', startedPlan: false, planNote: '', card: rawCard,
      }
    }

    const lockKey = options?.proposalId || `tool:${cleanTool}:${targetCard}`
    const inflightKey = `${this.config.project}:${cleanTool}:${targetCard}`
    const runConnect = async (): Promise<DispatcherConnectResult> => {
      const started = await this.post('/api/dsh/dispatcher/connect', {
        tool: cleanTool,
        card: targetCard,
        async: true,
      }) as Record<string, unknown>

      let payload = started
      const jobId = typeof started.job === 'string' ? started.job : ''
      if (jobId) {
        let completed = false
        const deadline = Date.now() + DISPATCHER_CONNECT_TIMEOUT_MS
        while (Date.now() < deadline) {
          await new Promise(resolve => setTimeout(resolve, 400))
          const polled = await this.get('/api/dsh/dispatcher/job', { job: jobId }) as Record<string, unknown>
          if (polled.done === true) {
            if (polled.error) {
              return {
                ok: false, status: 'missing', tool: cleanTool, label, model: '',
                reason: String(polled.error), startedPlan: false, planNote: '', card: targetCard,
              }
            }
            payload = (polled.result && typeof polled.result === 'object' ? polled.result : {}) as Record<string, unknown>
            completed = true
            break
          }
        }
        if (!completed) {
          return {
            ok: false, status: 'missing', tool: cleanTool, label, model: '',
            reason: '检测还在后台继续，请稍等几秒再查看状态或再点一次接着等结果', startedPlan: false, planNote: '', card: targetCard,
          }
        }
      }

      const ok = payload.ok === true && payload.status === 'ready'
      return {
        ok,
        status: ok ? 'ready' : 'missing',
        tool: String(payload.tool ?? cleanTool),
        label: String(payload.label ?? label),
        model: String(payload.model ?? ''),
        reason: String(payload.reason ?? ''),
        startedPlan: payload.started_plan === true,
        planNote: String(payload.plan_note ?? ''),
        card: String(payload.card ?? targetCard),
      }
    }

    state.connecting.add(lockKey)
    let flight = state.inFlightConnects.get(inflightKey)
    const createdFlight = !flight
    if (!flight) {
      flight = runConnect().finally(() => {
        state.inFlightConnects.delete(inflightKey)
      })
      state.inFlightConnects.set(inflightKey, flight)
    }

    try {
      const result = await flight
      if (result.ok && options?.sessionId && options.proposalId && state.proposals.get(options.sessionId)?.id === options.proposalId) {
        state.proposals.delete(options.sessionId)
      }
      return result
    } catch (error: unknown) {
      if (createdFlight) state.inFlightConnects.delete(inflightKey)
      return {
        ok: false,
        status: 'missing',
        tool: cleanTool,
        label,
        model: '',
        reason: error instanceof Error ? error.message : String(error),
        startedPlan: false,
        planNote: '',
        card: targetCard,
      }
    } finally {
      state.connecting.delete(lockKey)
    }
  }

  // ---------------------------------------------------------------- 任务卡 = 会话（第 4 项）

  /** 当前项目文件夹（工具用）。 */
  get projectPath(): string {
    return this.config.project
  }

  /**
   * 读取当前项目里各任务卡已绑定的 DSH 会话映射（只读，使用既有读写校验且不覆盖已有记录）。
   * @returns 任务卡号到会话编号的映射表。
   */
  @Remote('taskSessions')
  async taskSessions(): Promise<Record<string, string>> {
    if (!this.config.project) return {}
    try {
      return readTaskSessions(this.config.project)
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error)
      throw new RemoteError('superagent/rejected', message, { status: 500, message })
    }
  }

  /**
   * @param card - 卡片编号。
   * @returns 这张卡的任务对话：已记下的会话名（没有则空）、建议用的会话名、项目文件夹、卡标题。
   */
  @Remote('taskSession')
  async taskSession(card: string): Promise<TaskSessionInfo> {
    const id = checkCard(card)
    const row = (await this.cards()).find(c => c.id === id)
    return {
      sessionId: taskSessionOf(this.config.project, id),
      preferred: taskSessionIdFor(this.config.project, id),
      project: this.config.project,
      title: row ? `${id} ${row.title}` : id,
    }
  }

  /**
   * 记下“这张卡的任务对话是哪个会话”（DSH 建好会话后由界面调用）。先到先得：
   * 这张卡已经绑过就返回原来那个，界面改用它（两处同时建时收敛到同一个对话）。
   * @param card - 卡片编号。
   * @param sessionId - DSH 实际给的会话名。
   * @returns 最终这张卡用的会话名；绑不上时 ok=false 带原因。
   */
  @Remote('bindTaskSession')
  async bindTaskSession(card: string, sessionId: string): Promise<TaskSessionBind> {
    const result = bindTaskSession(this.config.project, checkCard(card), sessionId)
    return result.ok ? { ok: true, sessionId: result.sessionId, existed: result.existed, reason: '' } : { ok: false, sessionId: '', existed: false, reason: result.reason }
  }

  /**
   * @param sessionId - 会话编号。
   * @returns 这个会话若是任务卡对话，返回那张卡；否则返回 null。
   */
  @Remote('cardOfSession')
  async cardOfSession(sessionId: string): Promise<CardRow | null> {
    const card = cardOfTaskSession(this.config.project, sessionId)
    if (!card) return null
    return (await this.cards()).find(c => c.id === card) ?? null
  }

  /**
   * @param sessionId - 聊天会话编号。
   * @returns 在这个聊天里点确认建出的卡（新的在前），给聊天里的任务卡小条用（03b）。
   */
  @Remote('sessionCards')
  async sessionCards(sessionId: string): Promise<CardRow[]> {
    const rows = await this.cards()
    const mine = new Set(cardsFromConversation(this.config.project, sessionId, rows.map(r => r.id)))
    return rows.filter(r => mine.has(r.id)).reverse()
  }

  /** 任务对话里提出改要求；同一张卡旧的那条作废。图片仅存私有内存，绝不写进 PendingChange 返回前端。 */
  requestChange(
    card: string,
    summary: string,
    text: string,
    options?: RequestChangeOptions,
  ): PendingChange {
    const safeCard = checkCard(card)
    let validatedAttachments: ImagePayload[] = []
    if (options?.attachments !== undefined) {
      try {
        validatedAttachments = validateImagePayloads(options.attachments)
      } catch (error: unknown) {
        const msg = error instanceof Error && error.message ? error.message : '截图格式或大小不合规'
        throw new RemoteError('superagent/rejected', msg, { status: 400, message: msg })
      }
    }

    let effectiveSummary = summary
    let validatedBase: readonly string[] | undefined = options?.criteriaBase
    let validatedChanges: readonly CriteriaChangeItem[] | undefined = options?.criteriaChanges
    if (options?.criteriaChanges !== undefined && options.criteriaChanges.length > 0) {
      const checked = validateCriteriaChanges(options.criteriaBase ?? [], options.criteriaChanges)
      if (!checked.ok) {
        throw new RemoteError('superagent/rejected', checked.message, { status: 400, message: checked.message })
      }
      validatedBase = checked.base
      validatedChanges = checked.changes
      effectiveSummary = buildCriteriaSummary(checked.base, checked.changes)
    }

    const sourceAttachmentIds = options?.sourceAttachmentIds !== undefined
      ? options.sourceAttachmentIds.map(id => String(id ?? '')).filter(Boolean)
      : undefined

    const change: PendingChange = {
      id: randomUUID(),
      card: safeCard,
      summary: effectiveSummary,
      text,
      createdAt: Date.now(),
      ...(validatedBase === undefined ? {} : { criteriaBase: validatedBase }),
      ...(validatedChanges === undefined ? {} : { criteriaChanges: validatedChanges }),
      ...(options?.sourceDraft === undefined ? {} : { sourceDraft: options.sourceDraft }),
      ...(validatedAttachments.length > 0 ? { attachmentCount: validatedAttachments.length } : {}),
      ...(sourceAttachmentIds === undefined ? {} : { sourceAttachmentIds }),
    }
    const previous = this.changes.get(change.card)
    const attachmentsMap = getGatewayChangeAttachments(this.changes, this)
    if (previous) {
      attachmentsMap.delete(previous.id)
    }
    if (validatedAttachments.length > 0) {
      attachmentsMap.set(change.id, validatedAttachments)
    }
    this.changes.set(change.card, change)
    return change
  }

  /**
   * 验收卡“继续改”与停下卡“按我说的改”前，用任务对话当前模型拆分逐条修改。
   * 拆出结构化修改时进 PendingChange 等确认；合法空拆分直接追加；失败不写且不降级。
   */
  @Remote('prepareRevision')
  async prepareRevision(
    card: string,
    sessionId: string,
    text: string,
    sourceDraft?: string,
    attachments?: readonly ImagePayload[],
    sourceAttachmentIds?: readonly string[],
  ): Promise<PrepareRevisionResult> {
    const safeCard = checkCard(card)
    return prepareRevisionOnHost(
      {
        hostKey: this,
        projectPath: this.projectPath,
        isSubmitting: (targetCard) => {
          const pending = this.changes.get(targetCard)
          return Boolean(pending && this.submitting.has(pending.id))
        },
        view: targetCard => this.view(targetCard),
        cardAction: (targetCard, action, words, options) => this.cardAction(targetCard, action, words, options),
        requestChange: (targetCard, summary, words, options) => this.requestChange(targetCard, summary, words, options),
      },
      this.ctx,
      {
        card: safeCard,
        sessionId,
        text,
        ...(sourceDraft === undefined ? {} : { sourceDraft }),
        ...(attachments === undefined ? {} : { attachments }),
        ...(sourceAttachmentIds === undefined ? {} : { sourceAttachmentIds }),
      },
    )
  }

  /**
   * @param card - 卡片编号。
   * @returns 这张卡等你确认的改要求；没有时返回 null。
   */
  @Remote('pendingChange')
  async pendingChange(card: string): Promise<PendingChange | null> {
    return this.changes.get(checkCard(card)) ?? null
  }

  /**
   * 你对改要求点了“确认改”或“算了”。确认时转给调度员（现有“按我说的改”）。
   * @param card - 卡片编号。
   * @param id - 改要求编号。
   * @param confirm - true 确认改，false 算了。
   * @returns 结果。
   */
  @Remote('decideChange')
  async decideChange(card: string, id: string, confirm: boolean): Promise<CardActionResult> {
    const change = this.changes.get(checkCard(card))
    if (change?.id !== id) return { ok: false, message: '这条改要求已经处理过了' }
    // 提交中不许“算了”，也不许再点“确认改”（复查第 2 轮中等 1）。
    if (this.submitting.has(change.id)) return { ok: false, message: '正在提交，请稍等' }
    const attachmentsMap = getGatewayChangeAttachments(this.changes, this)
    if (!confirm) {
      this.changes.delete(change.card)
      attachmentsMap.delete(change.id)
      return { ok: true, message: '' }
    }
    const expectedAttachmentCount = typeof change.attachmentCount === 'number' ? change.attachmentCount : 0
    const savedAttachments = attachmentsMap.get(change.id)
    if (expectedAttachmentCount > 0 && (!savedAttachments || savedAttachments.length !== expectedAttachmentCount)) {
      return { ok: false, message: '原截图已失效或不可用，不能降级只提交文字，请点算了后重新附带截图再试' }
    }
    this.submitting.add(change.id)
    try {
      // 确认前重新查真实终态，防止出确认条之后卡已经结束或被放弃。
      const raw = await this.view(change.card)
      const parsed = JSON.parse(raw) as CardViewPayload
      const phaseCode = String(parsed.card?.phase?.code ?? '')
      const cardState = String(parsed.card?.state ?? '')
      if (FINISHED_PHASE_CODES.has(phaseCode) || FINISHED_CARD_STATES.has(cardState)) {
        if (this.changes.get(change.card)?.id === change.id) this.changes.delete(change.card)
        attachmentsMap.delete(change.id)
        return { ok: false, message: '这张卡已结束，要改请新建一张' }
      }
      const hasStructured = change.criteriaBase !== undefined || change.criteriaChanges !== undefined
      const hasAttachments = savedAttachments !== undefined && savedAttachments.length > 0
      if (expectedAttachmentCount > 0 && (!hasAttachments || savedAttachments.length !== expectedAttachmentCount)) {
        return { ok: false, message: '原截图已失效或不可用，不能降级只提交文字，请点算了后重新附带截图再试' }
      }
      const result = (hasStructured || hasAttachments)
        ? await this.cardAction(change.card, 'revise', change.text, {
          ...(change.criteriaBase === undefined ? {} : { criteriaBase: change.criteriaBase }),
          ...(change.criteriaChanges === undefined ? {} : { criteriaChanges: change.criteriaChanges }),
          ...(hasAttachments ? { attachments: savedAttachments } : {}),
        })
        : await this.cardAction(change.card, 'revise', change.text)
      // 只有成功才撤掉确认条与内存图；失败时留着，可以再点一次（复查第 1 轮中等 3）。
      if (result.ok) {
        if (this.changes.get(change.card)?.id === change.id) this.changes.delete(change.card)
        attachmentsMap.delete(change.id)
      }
      return result
    } catch (error: unknown) {
      return { ok: false, message: error instanceof Error ? error.message : String(error) }
    } finally {
      this.submitting.delete(change.id)
    }
  }

  /**
   * 探测超级助手首次配置 / 模型就绪状态（仅查自有专用 ref，绝不枚举系统钥匙串）。
   * @returns 当前引导需要性与不含密钥的状态摘要。
   */
  @Remote('onboardingState')
  async onboardingState(): Promise<OnboardingState> {
    return readOnboardingStateOnHost(this.ctx)
  }

  /**
   * 第 17 项：旧版复制过来、可直接选用的模型 Key（只有条目名、服务商和地址，不含密钥，不读钥匙串）。
   * @returns 可选列表；没有清单时为空。
   */
  @Remote('legacyKeyOptions')
  async legacyKeyOptions(): Promise<LegacyKeyOption[]> {
    return readLegacyOptions()
  }

  /**
   * 用临时填写的 API Key 实时拉取可用聊天模型列表（不写入持久化）。
   * @param request - 服务商、接口地址与本次输入的密钥。
   * @returns 可选聊天模型列表；任何失败已擦除密钥。
   */
  @Remote('discoverOnboardingModels')
  async discoverOnboardingModels(
    request: { readonly provider: string; readonly baseUrl: string; readonly key: string; readonly legacyAccount?: string | undefined },
    signal?: AbortSignal,
  ): Promise<OnboardingModelItem[]> {
    let resolved = request
    try {
      resolved = await resolveLegacyOnboardingRequestOnHost(this.ctx, request)
      return await discoverOnboardingModelsOnHost(this.ctx, resolved, signal)
    } catch (error: unknown) {
      const safe = formatSafeOnboardingError(error, resolved?.key || request?.key)
      throw new RemoteError('superagent/rejected', safe, { status: 400, message: safe })
    }
  }

  /**
   * 执行首次使用 / 更换模型完整流程（先发一句小回复确认可用，再写入钥匙串与模型设置）。
   * @param request - 服务商、接口地址、密钥与明确选中的模型。
   * @returns 不含密钥的配置摘要。
   */
  @Remote('completeOnboarding')
  async completeOnboarding(
    request: OnboardingSetupPayload,
    signal?: AbortSignal,
  ): Promise<OnboardingSetupSummary> {
    let resolved = request
    try {
      resolved = await resolveLegacyOnboardingRequestOnHost(this.ctx, request)
      return await completeOnboardingOnHost(this.ctx, resolved, signal)
    } catch (error: unknown) {
      const safe = formatSafeOnboardingError(error, resolved?.key || request?.key)
      throw new RemoteError('superagent/rejected', safe, { status: 400, message: safe })
    }
  }

  // ---------------------------------------------------------------- 后台服务（进程看管在 backend.ts）

  private ensure(): Promise<Backend> {
    return this.process.ensure()
  }

  private async request(
    path: string,
    query: Record<string, string>,
    body?: Record<string, unknown>,
    timeoutMs: number = DEFAULT_REQUEST_TIMEOUT_MS,
  ): Promise<unknown> {
    const backend = await this.ensure()
    const url = new URL(path, `http://127.0.0.1:${backend.port}`)
    url.searchParams.set('project', this.config.project)
    for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value)
    let response: Response
    try {
      response = await fetch(url, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { 'X-Chat-Token': backend.token, ...body === undefined ? {} : { 'content-type': 'application/json' } },
        ...body === undefined ? {} : { body: JSON.stringify({ ...body, project: this.config.project }) },
        signal: AbortSignal.timeout(timeoutMs),
      })
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error)
      throw new RemoteError('superagent/backend-unavailable', message, { reason: 'fetch' }, { cause: error })
    }
    let text: string
    try {
      text = await response.text()
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error)
      throw new RemoteError('superagent/backend-unavailable', message, { reason: 'read' }, { cause: error })
    }
    let value: unknown
    try {
      value = JSON.parse(text)
    } catch (_error) {
      // 成功的回话必须是 JSON，否则当作后台出错，不能悄悄当成空列表（复查第 1 轮中等 2）。
      if (response.ok) throw new RemoteError('superagent/backend-unavailable', '后台回的不是 JSON', { reason: 'bad-response' })
      value = { error: text.slice(0, 300) }
    }
    if (!response.ok) {
      const message = String((value as Record<string, unknown> | null)?.error ?? response.statusText)
      throw new RemoteError('superagent/rejected', message, { status: response.status, message })
    }
    return value
  }

  private get(path: string, query: Record<string, string>): Promise<unknown> {
    return this.request(path, query)
  }

  private post(path: string, body: Record<string, unknown>): Promise<unknown> {
    return this.request(path, {}, body)
  }
}

const DEFAULT_REQUEST_TIMEOUT_MS = 20000
const DISPATCHER_CONNECT_TIMEOUT_MS = 210000
const PREVIEW_START_TIMEOUT_MS = 60000
const CARD_ID = /^[A-Za-z0-9][A-Za-z0-9-]{0,63}$/
const CARD_ACTIONS = new Set(['approve', 'revise', 'discard', 'replan', 'accept', 'accept_issues', 'resume', 'stop'])
const PREVIEW_CLEANUP_ACTIONS = new Set(['accept', 'accept_issues', 'revise', 'discard'])
const LOCAL_PREVIEW_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]'])
const VIEW_NEEDS_YOU_PHASES = new Set([
  'plan_ready', 'plan_waiting', 'plan_failed',
  'review', 'merge_failed', 'blocked', 'interrupted', 'start_failed', 'copy_missing',
])

interface TrackedPreviewJob {
  readonly card: string
  readonly jobId: JobId | { readonly id: string }
  readonly owner: SessionId
  readonly url: string
}

interface GatewayPreviewState {
  jobs?: JobRegistry | PreviewJobsPort | undefined
  startingCard: string | null
  stoppingCard: string | null
  readonly tracked: Map<string, TrackedPreviewJob>
}

const PREVIEW_STATE: unique symbol = Symbol('superagent.previewState')

interface PreviewStateCarrier {
  [PREVIEW_STATE]?: GatewayPreviewState
}

function createGatewayPreviewState(): GatewayPreviewState {
  return {
    startingCard: null,
    stoppingCard: null,
    tracked: new Map<string, TrackedPreviewJob>(),
  }
}

/**
 * 读取或延迟初始化当前服务实例根上的共享预览状态。
 * - Cordis `Service[Service.extend]` 派生视图与多次 `Object.create(gateway)` 视图
 *   都会沿着原型链继承同一份 `[PREVIEW_STATE]`，不再因 `this` 身份不同丢失锁或记录；
 * - 绝不挂载到 `SuperagentGateway.prototype`，避免跨实例串台。
 */
function getGatewayPreviewState(gateway: SuperagentGateway): GatewayPreviewState {
  const carrier = gateway as unknown as PreviewStateCarrier
  if (carrier[PREVIEW_STATE]) return carrier[PREVIEW_STATE]

  let rootInstance: object = gateway
  let proto = Object.getPrototypeOf(rootInstance) as object | null
  while (proto && proto !== SuperagentGateway.prototype && proto !== Object.prototype) {
    const inherited = (proto as PreviewStateCarrier)[PREVIEW_STATE]
    if (inherited) return inherited
    rootInstance = proto
    proto = Object.getPrototypeOf(rootInstance) as object | null
  }

  const created = createGatewayPreviewState()
  ;(rootInstance as PreviewStateCarrier)[PREVIEW_STATE] = created
  return created
}

const gatewayChangeAttachmentsMap = new WeakMap<object, Map<string, readonly ImagePayload[]>>()

/**
 * 以共享的 `this.changes` Map 作为键存储待确认改要求的私有截图内存，
 * 确保 Cordis 不同调用作用域代理（Proxy）访问同一网关数据时原图不会因 `this` 身份不同而丢失，
 * 同时绝不把 base64 挂到公开的 PendingChange 或原型上。
 */
function getGatewayChangeAttachments(
  changes: Map<string, PendingChange> | undefined,
  fallbackGateway: SuperagentGateway,
): Map<string, readonly ImagePayload[]> {
  const key: object = changes && typeof changes === 'object' ? changes : fallbackGateway
  let map = gatewayChangeAttachmentsMap.get(key)
  if (!map) {
    map = new Map<string, readonly ImagePayload[]>()
    gatewayChangeAttachmentsMap.set(key, map)
  }
  return map
}

interface GatewayDispatcherState {
  readonly proposals: Map<string, DispatcherProposal>
  readonly connecting: Set<string>
  readonly inFlightConnects: Map<string, Promise<DispatcherConnectResult>>
}

const DISPATCHER_STATE: unique symbol = Symbol('superagent.dispatcherState')

interface DispatcherStateCarrier {
  [DISPATCHER_STATE]?: GatewayDispatcherState
}

function createGatewayDispatcherState(): GatewayDispatcherState {
  return {
    proposals: new Map<string, DispatcherProposal>(),
    connecting: new Set<string>(),
    inFlightConnects: new Map<string, Promise<DispatcherConnectResult>>(),
  }
}

/** 沿服务实例根读取共享的调度员确认卡与锁，防止 Cordis 派生视图丢失状态。 */
function getGatewayDispatcherState(gateway: SuperagentGateway): GatewayDispatcherState {
  const carrier = gateway as unknown as DispatcherStateCarrier
  if (carrier[DISPATCHER_STATE]) return carrier[DISPATCHER_STATE]

  let rootInstance: object = gateway
  let proto = Object.getPrototypeOf(rootInstance) as object | null
  while (proto && proto !== SuperagentGateway.prototype && proto !== Object.prototype) {
    const inherited = (proto as DispatcherStateCarrier)[DISPATCHER_STATE]
    if (inherited) return inherited
    rootInstance = proto
    proto = Object.getPrototypeOf(rootInstance) as object | null
  }

  const created = createGatewayDispatcherState()
  ;(rootInstance as DispatcherStateCarrier)[DISPATCHER_STATE] = created
  return created
}

function staticDispatcherDraft(tool: DispatcherTool, card: string = ''): Omit<DispatcherProposal, 'id' | 'session' | 'createdAt'> {
  const safeTool: DispatcherTool = tool === 'agy' ? 'agy' : 'codex'
  const label = safeTool === 'agy' ? 'agy' : 'Codex'
  return {
    tool: safeTool,
    label,
    card,
    title: `接一个调度员：${label}`,
    what: `把电脑里已装好并登录过的 ${label} 接进来当调度员，用来写方案、拆步骤和安排执行与复查。`,
    how: `在新版的积木库里套用 ${label} 预置接法，并做一次很短的连通检测，确认它真能回话（不改项目代码）。`,
    needYou: `如果你还没装好或没登录 ${label}，请先自己完成安装与登录，再点确认；我不会替你自动安装软件。`,
    scope: `只在当前项目设置与新版的积木库保存这份接法，把调度员设置为 ${label}；不碰你原有的积木库，也不读其他钥匙串密钥。`,
  }
}

function resolveGatewayJobs(gateway: SuperagentGateway, state: GatewayPreviewState): PreviewJobsPort | undefined {
  if (state.jobs && typeof state.jobs.start === 'function') return state.jobs
  try {
    const ctxJobs = (gateway as unknown as { ctx?: { jobs?: PreviewJobsPort } }).ctx?.jobs
    if (ctxJobs && typeof ctxJobs.start === 'function') {
      return ctxJobs
    }
  } catch {
    // 拦截 Cordis 未注入保护或其他 getter 异常，交由调用方进入真实回滚 stop。
  }
  return undefined
}

function readCardPreviewLabel(project: string, card: string): string {
  const md = readCardFileText(project, card, '需求卡.md')
  if (md) {
    for (const line of md.split(/\r?\n/)) {
      const m = line.match(/^#\s+(.+)$/)
      if (m && m[1]?.trim()) {
        const cleaned = sanitizeTrajectoryText(m[1].trim())
        return `预览：${cleaned}`
      }
    }
  }
  return `预览：${card}`
}

const PREVIEW_LOG_FILENAME = '预览输出.log'
const MAX_PREVIEW_LOG_TAIL_BYTES = 64 * 1024

function isInsideRealDir(baseRealDir: string, targetRealPath: string): boolean {
  return targetRealPath === baseRealDir || targetRealPath.startsWith(baseRealDir + sep)
}

/**
 * 异步安全读取本卡目录内 `预览输出.log` 的最近尾窗（最大 64KB）。
 * 严格限制在任务卡真实目录内，拒绝越界软链接，避免主线程读取大文件，并过滤旧厂商名称。
 */
async function readCardPreviewLog(project: string, card: string): Promise<string> {
  const cleanProject = typeof project === 'string' ? project.trim() : ''
  if (!cleanProject) return ''
  const safeCard = checkCard(card)

  let realCardsRoot: string
  let realCardDir: string
  try {
    realCardsRoot = await realpath(resolve(cleanProject, '.collab', 'cards'))
    const cardDir = join(realCardsRoot, safeCard)
    const cardLinkStat = await lstat(cardDir)
    realCardDir = await realpath(cardDir)
    if (!isInsideRealDir(realCardsRoot, realCardDir)) return ''
    if (cardLinkStat.isSymbolicLink() && !isInsideRealDir(realCardsRoot, realCardDir)) return ''
    if (!(await stat(realCardDir)).isDirectory()) return ''
  } catch {
    return ''
  }

  const logPath = join(realCardDir, PREVIEW_LOG_FILENAME)
  let realLogPath: string
  let logSize: number
  try {
    const linkStat = await lstat(logPath)
    realLogPath = await realpath(logPath)
    if (!isInsideRealDir(realCardDir, realLogPath)) return ''
    if (linkStat.isSymbolicLink() && !isInsideRealDir(realCardDir, realLogPath)) return ''
    const fileStat = await stat(realLogPath)
    if (!fileStat.isFile() || fileStat.size <= 0) return ''
    logSize = fileStat.size
  } catch {
    return ''
  }

  const readBytes = Math.min(logSize, MAX_PREVIEW_LOG_TAIL_BYTES)
  const offset = Math.max(0, logSize - readBytes)
  const buffer = Buffer.allocUnsafe(readBytes)

  let handle: Awaited<ReturnType<typeof open>> | undefined
  try {
    handle = await open(realLogPath, 'r')
    let totalRead = 0
    while (totalRead < readBytes) {
      const { bytesRead } = await handle.read(buffer, totalRead, readBytes - totalRead, offset + totalRead)
      if (bytesRead <= 0) break
      totalRead += bytesRead
    }
    const rawText = buffer.subarray(0, totalRead).toString('utf-8')
    const cleaned = offset > 0 ? rawText.replace(/^\uFFFD+/, '') : rawText
    return sanitizeTrajectoryText(cleaned)
  } catch {
    return ''
  } finally {
    await handle?.close().catch(() => {})
  }
}

function checkCard(card: string): string {
  if (!CARD_ID.test(card)) throw new RemoteError('superagent/rejected', '卡片编号不对', { status: 400, message: '卡片编号不对' })
  return card
}

function isLocalHttpUrl(rawUrl: unknown): rawUrl is string {
  if (typeof rawUrl !== 'string' || !rawUrl.trim()) return false
  try {
    const parsed = new URL(rawUrl)
    return parsed.protocol === 'http:' && LOCAL_PREVIEW_HOSTS.has(parsed.hostname) && !parsed.username && !parsed.password
  } catch (_error) {
    return false
  }
}

function readCardFileText(project: string, card: string, filename: string): string | null {
  if (!project) return null
  try {
    return readFileSync(join(project, '.collab', 'cards', card, filename), 'utf8')
  } catch (_error) {
    return null
  }
}

function readSafeMetaText(project: string, card: string, filename: string): string | null {
  if (!project || !CARD_ID.test(card)) return null
  return readCardFileText(project, card, filename)
}

function parseCardKindValue(raw: unknown): CardKind | null {
  if (typeof raw !== 'string') return null
  const val = raw.trim().toLowerCase()
  if (val === 'work' || val === '干活' || val === '开发软件' || val === '修一个问题') return 'work'
  if (val === 'ability' || val === '造能力') return 'ability'
  if (val === 'connect' || val === '接零件') return 'connect'
  return null
}

function defaultFlowForKind(kind: CardKind | null): string | null {
  if (kind === 'work') return '开发软件'
  if (kind === 'ability') return '造能力'
  if (kind === 'connect') return '接零件'
  return null
}

function readSectionFirstLine(markdown: string, headingName: string): string {
  const sections = markdown.split(/^##\s+/m).slice(1)
  for (const section of sections) {
    const newlineIdx = section.indexOf('\n')
    const heading = (newlineIdx === -1 ? section : section.slice(0, newlineIdx)).trim()
    if (heading !== headingName) continue
    const body = (newlineIdx === -1 ? '' : section.slice(newlineIdx + 1)).trim()
    const first = body.split(/\r?\n/)[0]?.trim() ?? ''
    if (first && first !== '（待填）') return first
  }
  return ''
}

interface CardOverviewMeta {
  readonly kind: CardKind | null
  readonly flow: string | null
  readonly reviewRounds: number | null
  readonly elapsed: number | null
  readonly completedAt: string | null
  readonly firstPass: boolean | null
  readonly archived: boolean
}

function computeFirstPassFromFlow(events: readonly ParsedFlowEvent[]): boolean | null {
  const firstDeliveryIdx = events.findIndex(ev => ev.kind === '结束' && ev.recState === '待验收')
  let mergeIdx = -1
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i]?.kind === '合并') {
      mergeIdx = i
      break
    }
  }
  if (firstDeliveryIdx === -1 || mergeIdx <= firstDeliveryIdx) {
    return null
  }

  for (let i = firstDeliveryIdx + 1; i < mergeIdx; i++) {
    const ev = events[i]
    if (!ev) continue
    // 只有首次交付（结束/待验收）之后到合入之前的用户退回/追加改动/重启施工才算首次验收未通过；
    // 交付前的内部测试返工、代码复查多轮或交付前追加要求均不影响“用户首次验收直接合入”。
    if (
      ev.kind === '追加要求'
      || ev.kind === '退回'
      || ev.kind === '开工'
      || ev.kind === '开跑'
      || ev.kind === '写方案'
      || ev.kind === '批准'
      || (ev.kind === '结束' && ev.recState === '待验收')
    ) {
      return false
    }
  }
  return true
}

/**
 * 从 `/api/cards` 返回与本卡真实 `状态.json`、`需求卡.md`、`流转.jsonl`、`已归档.json` 汇总任务总览元数据。
 * - 无法从真实记录确认的一律返回 null，绝不用 updated 编造完成时间，也不叠加并行步骤 seconds。
 * - 用时按每轮“开工→结束/停下/转人工”的墙钟施工区间累加；进行中任务含当前正在跑这一轮的耗时。
 * - 首次验收只看首个“结束(待验收)”之后至“合并”之间是否有用户退回/追加要求/重开施工。
 */
function readCardOverviewMeta(
  project: string,
  cardId: string,
  state: string,
  phaseCode: string,
  phaseTone: unknown,
  needsYou: boolean,
  item: Record<string, unknown>,
): CardOverviewMeta {
  const isDiscarded = state === '已放弃' || phaseCode === 'dropped'
  const isFinished = !needsYou && (phaseTone === 'done' || FINISHED_PHASE_CODES.has(phaseCode) || FINISHED_CARD_STATES.has(state))
  const isRunningNow = isCardRunningNow(state, phaseCode, phaseTone, needsYou)

  // 已归档仅认 ARCHIVE_MARK（已归档.json）且已结束，绝不把流转 kind="归档" 当成隐藏卡
  let archived = false
  if (typeof item.archived === 'boolean') {
    archived = item.archived
  } else if (project && CARD_ID.test(cardId) && isFinished) {
    archived = existsSync(join(project, '.collab', 'cards', cardId, '已归档.json'))
  }

  let stateJson: Record<string, unknown> | null = null
  const rawStateJson = readSafeMetaText(project, cardId, '状态.json')
  if (rawStateJson) {
    try {
      const parsed = JSON.parse(rawStateJson) as unknown
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        stateJson = parsed as Record<string, unknown>
      }
    } catch {
      stateJson = null
    }
  }

  const reqMd = readSafeMetaText(project, cardId, '需求卡.md')
  const flowText = readSafeMetaText(project, cardId, '流转.jsonl')

  let dispatchReviewCount = 0
  let replyReviewCount = 0
  let lastMergeTime: string | null = null
  let hasWorkWorkflowEvent = false
  const parsedEvents = parseFlowEvents(flowText)

  for (const ev of parsedEvents) {
    if (ev.kind === '开工' || ev.kind === '开跑' || ev.kind === '写方案') {
      hasWorkWorkflowEvent = true
    }
    if (ev.kind === '派活' && (ev.dst === '复查员' || ev.step === '复查')) {
      dispatchReviewCount += 1
    }
    if (ev.kind === '回复' && (ev.src === '复查员' || ev.step === '复查')) {
      replyReviewCount += 1
    }
    // 只有 kind === "合并" 表示真正放进项目；"合并开始"/"合并失败"/"结束" 都不算完成合入
    // 且日期必须为合法完整的真实日期，损坏时间绝不能记作完成时间
    if (ev.kind === '合并') {
      lastMergeTime = ev.timeStr && parseStrictDateParts(ev.timeStr) !== null ? ev.timeStr : null
    }
  }

  let cardKind: CardKind | null = parseCardKindValue(item.kind) ?? parseCardKindValue(stateJson?.kind)
  let cardFlow: string | null = typeof item.flow === 'string' && item.flow.trim()
    ? sanitizeTrajectoryText(item.flow.trim())
    : (typeof stateJson?.flow === 'string' && stateJson.flow.trim()
      ? sanitizeTrajectoryText(stateJson.flow.trim())
      : null)

  if (reqMd) {
    const mdType = readSectionFirstLine(reqMd, '类型')
    const mdFlow = readSectionFirstLine(reqMd, '流程')
    if (!cardKind && mdType) cardKind = parseCardKindValue(mdType)
    if (!cardFlow && mdFlow) cardFlow = sanitizeTrajectoryText(mdFlow)
    if (!cardKind && (reqMd.includes('## 允许改动的范围') || hasWorkWorkflowEvent)) {
      cardKind = 'work'
    }
  } else if (!cardKind && hasWorkWorkflowEvent) {
    cardKind = 'work'
  }

  if (!cardFlow && cardKind) {
    cardFlow = defaultFlowForKind(cardKind)
  }

  const explicitReviewRounds = typeof item.reviewRounds === 'number' && Number.isFinite(item.reviewRounds) && item.reviewRounds >= 0
    ? Math.round(item.reviewRounds)
    : null
  const countedReviewRounds = Math.max(dispatchReviewCount, replyReviewCount)
  const reviewRounds = explicitReviewRounds ?? (countedReviewRounds > 0 ? countedReviewRounds : null)

  const explicitElapsed = typeof item.elapsed === 'number' && Number.isFinite(item.elapsed) && item.elapsed >= 0
    ? item.elapsed
    : null
  const elapsed = explicitElapsed ?? computeElapsedFromFlow(parsedEvents, isRunningNow, Date.now())

  const isMergedDone = !isDiscarded && !needsYou && state === '已合并'
  let completedAt: string | null = null
  if (!isDiscarded && !needsYou) {
    if (typeof item.completedAt === 'string' && item.completedAt.trim()) {
      const trimmedCompleted = item.completedAt.trim()
      if (parseStrictDateParts(trimmedCompleted) !== null) {
        completedAt = trimmedCompleted
      }
    } else if (isMergedDone && lastMergeTime !== null) {
      completedAt = lastMergeTime
    }
  }

  let firstPass: boolean | null = null
  if (typeof item.firstPass === 'boolean') {
    firstPass = item.firstPass
  } else if (isMergedDone && completedAt !== null && parsedEvents.length > 0) {
    firstPass = computeFirstPassFromFlow(parsedEvents)
  }

  return {
    kind: cardKind,
    flow: cardFlow,
    reviewRounds,
    elapsed,
    completedAt,
    firstPass,
    archived,
  }
}

function readPlanVersion(project: string, card: string, planText: string): number {
  const flowText = readCardFileText(project, card, '流转.jsonl')
  let count = 0
  if (flowText) {
    for (const rawLine of flowText.split(/\r?\n/)) {
      const line = rawLine.trim()
      if (!line) continue
      try {
        const entry = JSON.parse(line) as Record<string, unknown>
        if (entry?.kind === '方案') count += 1
      } catch (_error) {
        // 跳过损坏行，不影响其余记录计数。
      }
    }
  }
  if (count >= 1) return count
  return planText.trim().length > 0 ? 1 : 0
}
