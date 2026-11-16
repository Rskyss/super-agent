/**
 * 第 15 项 Host 端实现：读取需求卡验收标准，使用任务对话当前模型流式拆分逐条修改。
 * 严格使用 DSH 公开接口（ctx.agents.get / ctx.llm.stream / createUserMessage），
 * 不自建 HTTP、不读取密钥、不改动会话模式或写入聊天历史。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import type { AgentRegistry } from '@deepseek-ai/dsh-agent'
import { createUserMessage, type LlmRuntime } from '@deepseek-ai/dsh-llm'
import type { SessionId } from '@deepseek-ai/dsh-session'
import { validateImagePayloads } from './card-attachments.ts'
import {
  buildCriteriaSummary,
  buildRevisionPrompt,
  isGeneratedAttachmentMarkdownLine,
  MAX_REVISION_OUTPUT_CHARS,
  parseRevisionModelOutput,
  REVISION_TIMEOUT_MS,
  splitAcceptanceCriteria,
} from './revision.ts'
import { cardOfTaskSession, taskSessionOf } from './task-session.ts'
import {
  FINISHED_CARD_STATES,
  FINISHED_PHASE_CODES,
  type CardActionOptions,
  type CardActionResult,
  type CardViewPayload,
  type ImagePayload,
  type PendingChange,
  type PrepareRevisionResult,
  type RequestChangeOptions,
} from './types.ts'

declare module '@deepseek-ai/dsh-llm' {
  interface MessageSourceMap {
    'superagent-revision': { kind: 'superagent-revision' }
  }
}

const REVISION_ALLOWED_PHASES = new Set(['review', 'blocked'])
const SUPERSEDED_REASON = '__superagent_revision_superseded__'
const TIMEOUT_REASON = '__superagent_revision_timeout__'
const OVERFLOW_REASON = '__superagent_revision_overflow__'

interface RevisionHostState {
  readonly seqByCard: Map<string, number>
  readonly inflightByCard: Map<string, AbortController>
}

const revisionStateMap = new WeakMap<object, RevisionHostState>()

function getRevisionHostState(host: object): RevisionHostState {
  let state = revisionStateMap.get(host)
  if (!state) {
    state = {
      seqByCard: new Map(),
      inflightByCard: new Map(),
    }
    revisionStateMap.set(host, state)
  }
  return state
}

/** 从需求卡读取验收标准（优先“验收标准”，兼容“怎么算做完”）与历史追加要求。 */
export function readCardAcceptanceCriteria(
  project: string,
  card: string,
): { criteria: string[]; changeNotes: string[] } {
  if (!project) return { criteria: [], changeNotes: [] }
  let content = ''
  try {
    content = readFileSync(join(project, '.collab', 'cards', card, '需求卡.md'), 'utf8')
  } catch {
    return { criteria: [], changeNotes: [] }
  }
  if (!content) return { criteria: [], changeNotes: [] }

  const sections = content.split(/^##\s+/m).slice(1)
  let explicitCriteria: string[] = []
  let fallbackCriteria: string[] = []
  const changeNotes: string[] = []

  for (const section of sections) {
    const newlineIdx = section.indexOf('\n')
    const heading = (newlineIdx === -1 ? section : section.slice(0, newlineIdx)).trim()
    const body = (newlineIdx === -1 ? '' : section.slice(newlineIdx + 1)).trim()
    if (!body) continue
    if (heading === '验收标准' && explicitCriteria.length === 0) {
      explicitCriteria = splitAcceptanceCriteria(body)
      continue
    }
    if (heading === '怎么算做完' && fallbackCriteria.length === 0) {
      fallbackCriteria = splitAcceptanceCriteria(body)
      continue
    }
    if (heading.startsWith('追加要求')) {
      for (const line of body.split(/\r?\n/)) {
        const text = line.trim().replace(/^[-*•]\s*/, '').trim()
        if (text && text !== '（待填）' && !isGeneratedAttachmentMarkdownLine(text)) {
          changeNotes.push(text)
        }
      }
    }
  }

  return {
    criteria: explicitCriteria.length > 0 ? explicitCriteria : fallbackCriteria,
    changeNotes,
  }
}

export interface RevisionGatewayHost {
  readonly hostKey?: object
  readonly projectPath: string
  isSubmitting(card: string): boolean
  view(card: string): Promise<string>
  cardAction(card: string, action: string, text: string, options?: CardActionOptions): Promise<CardActionResult>
  requestChange(card: string, summary: string, text: string, options?: RequestChangeOptions): PendingChange
}

function resolveAgentsService(ctx: Context | undefined): AgentRegistry | undefined {
  if (!ctx) return undefined
  try {
    if (typeof ctx.get === 'function') {
      const found = ctx.get('agents')
      if (found) return found
    }
  } catch {
    // 忽略未注入时 Cordis 的 getter 异常，继续检查直接属性（兼容轻量单测）。
  }
  try {
    return (ctx as unknown as { agents?: AgentRegistry }).agents
  } catch {
    return undefined
  }
}

function resolveLlmService(ctx: Context | undefined): LlmRuntime | undefined {
  if (!ctx) return undefined
  try {
    if (typeof ctx.get === 'function') {
      const found = ctx.get('llm')
      if (found) return found
    }
  } catch {
    // 忽略未注入时 Cordis 的 getter 异常，继续检查直接属性（兼容轻量单测）。
  }
  try {
    return (ctx as unknown as { llm?: LlmRuntime }).llm
  } catch {
    return undefined
  }
}

function parseViewSnapshot(
  rawView: string,
  project: string,
  card: string,
): { ok: true; phaseCode: string; cardState: string; actions: string[]; criteria: string[] } | { ok: false; message: string } {
  let payload: CardViewPayload
  try {
    payload = JSON.parse(rawView) as CardViewPayload
  } catch {
    return { ok: false, message: '读取卡片当前状态失败，请稍后重试' }
  }
  const phaseCode = String(payload.card?.phase?.code ?? '')
  const cardState = String(payload.card?.state ?? '')
  const actions = Array.isArray(payload.card?.phase?.actions) ? payload.card.phase.actions.map(String) : []
  const viewCriteria = Array.isArray(payload.accept?.criteria)
    ? payload.accept.criteria.map(item => String(item).trim()).filter(Boolean)
    : []
  const fileCriteria = readCardAcceptanceCriteria(project, card).criteria
  const criteria = fileCriteria.length > 0 ? fileCriteria : viewCriteria
  return { ok: true, phaseCode, cardState, actions, criteria }
}

function nextChunkWithSignal<T>(
  iterator: AsyncIterator<T>,
  signal: AbortSignal,
): Promise<IteratorResult<T>> {
  if (signal.aborted) {
    return Promise.reject(new Error(typeof signal.reason === 'string' ? signal.reason : 'aborted'))
  }
  return new Promise<IteratorResult<T>>((resolve, reject) => {
    const onAbort = () => {
      reject(new Error(typeof signal.reason === 'string' ? signal.reason : 'aborted'))
    }
    signal.addEventListener('abort', onAbort, { once: true })
    iterator.next().then(
      (res) => {
        signal.removeEventListener('abort', onAbort)
        resolve(res)
      },
      (err) => {
        signal.removeEventListener('abort', onAbort)
        reject(err)
      },
    )
  })
}

function sameCriteriaList(left: readonly string[], right: readonly string[]): boolean {
  if (left.length !== right.length) return false
  for (let i = 0; i < left.length; i += 1) {
    if (left[i] !== right[i]) return false
  }
  return true
}

function formatRevisionLlmFailure(error: unknown, signal: AbortSignal): string {
  if (signal.aborted) {
    const reason = signal.reason
    if (reason === TIMEOUT_REASON) return '拆分修改要求超时，请重试'
    if (reason === OVERFLOW_REASON) return '模型返回内容过长，请把修改意见写更具体后再试'
    if (reason === SUPERSEDED_REASON) return '已有更新的修改请求，这次拆分已取消'
    return '拆分修改要求已取消，请重试'
  }
  const raw = error instanceof Error ? error.message : String(error ?? '')
  const cleaned = raw.trim()
  if (!cleaned) return '拆分修改要求失败，请重试'
  if (/aborted|timeout|timed out/i.test(cleaned)) return '拆分修改要求超时，请重试'
  return `拆分修改要求失败：${cleaned.slice(0, 120)}`
}

/** 在 Host 侧调用任务对话当前模型，把 review / blocked 的修改意见拆成结构化条目或直接追加。 */
export async function prepareRevisionOnHost(
  host: RevisionGatewayHost,
  ctx: Context | undefined,
  params: {
    readonly card: string
    readonly sessionId: string
    readonly text: string
    readonly sourceDraft?: string | undefined
    readonly attachments?: readonly ImagePayload[] | undefined
    readonly sourceAttachmentIds?: readonly string[] | undefined
  },
): Promise<PrepareRevisionResult> {
  const { card, sessionId, text, sourceDraft, attachments, sourceAttachmentIds } = params
  const cleanSessionId = String(sessionId ?? '').trim()
  const words = String(text ?? '').trim()
  if (!words) {
    return { ok: false, pending: false, message: '请先在输入框里写清楚怎么改' }
  }
  if (!cleanSessionId) {
    return { ok: false, pending: false, message: '找不到当前任务对话，无法拆分修改要求' }
  }

  let validatedAttachments: ImagePayload[] = []
  if (attachments !== undefined) {
    try {
      validatedAttachments = validateImagePayloads(attachments)
    } catch (error: unknown) {
      const msg = error instanceof Error && error.message ? error.message : '截图格式或大小不合规'
      return { ok: false, pending: false, message: msg }
    }
  }
  const normalizedSourceIds = sourceAttachmentIds !== undefined
    ? sourceAttachmentIds.map(id => String(id ?? '')).filter(Boolean)
    : undefined

  const project = host.projectPath
  const ownerCard = cardOfTaskSession(project, cleanSessionId)
  const cardBoundSession = taskSessionOf(project, card)
  if (ownerCard !== card || (cardBoundSession && cardBoundSession !== cleanSessionId)) {
    return { ok: false, pending: false, message: '当前对话没有绑定这张任务卡，不能拆分修改' }
  }

  if (host.isSubmitting(card)) {
    return { ok: false, pending: false, message: '上一条改要求正在提交，请稍等' }
  }

  let rawView = ''
  try {
    rawView = await host.view(card)
  } catch (error: unknown) {
    const msg = error instanceof Error ? error.message : String(error ?? '')
    return { ok: false, pending: false, message: `读不到卡片当前状态：${msg || '请重试'}` }
  }
  const snap = parseViewSnapshot(rawView, project, card)
  if (!snap.ok) return { ok: false, pending: false, message: snap.message }

  if (FINISHED_PHASE_CODES.has(snap.phaseCode) || FINISHED_CARD_STATES.has(snap.cardState)) {
    return { ok: false, pending: false, message: '这张卡已结束，要改请新建一张' }
  }
  if (!REVISION_ALLOWED_PHASES.has(snap.phaseCode) || !snap.actions.includes('revise')) {
    return { ok: false, pending: false, message: '当前阶段不支持逐条拆分修改' }
  }

  const agents = resolveAgentsService(ctx)
  const llm = resolveLlmService(ctx)
  if (!agents || typeof agents.get !== 'function' || !llm || typeof llm.stream !== 'function') {
    return { ok: false, pending: false, message: '当前任务对话模型服务未就绪，请稍后再试' }
  }

  const agent = agents.get(cleanSessionId as SessionId)
  const selection = agent?.model?.current?.()
  if (!selection || !selection.provider || !selection.model) {
    return { ok: false, pending: false, message: '当前任务对话还没有配置可用模型，请先检查模型设置' }
  }

  const criteriaBase = snap.criteria
  const { system, user } = buildRevisionPrompt(criteriaBase, words, validatedAttachments.length)

  const state = getRevisionHostState(host.hostKey ?? host)
  const previousController = state.inflightByCard.get(card)
  if (previousController) {
    previousController.abort(SUPERSEDED_REASON)
  }
  const mySeq = (state.seqByCard.get(card) ?? 0) + 1
  state.seqByCard.set(card, mySeq)
  const controller = new AbortController()
  state.inflightByCard.set(card, controller)

  const timeoutHandle = setTimeout(() => {
    controller.abort(TIMEOUT_REASON)
  }, REVISION_TIMEOUT_MS)

  let rawOutput = ''
  let iterator: AsyncIterator<Awaited<ReturnType<typeof llm.stream>> extends AsyncIterable<infer C> ? C : never> | undefined
  let streamFinished = false
  try {
    const stream = llm.stream({
      provider: selection.provider,
      model: selection.model,
      ...(selection.reasoningEffort !== undefined ? { reasoningEffort: selection.reasoningEffort } : {}),
      system,
      messages: [
        createUserMessage({
          content: [{ type: 'text', text: user }],
          source: { kind: 'superagent-revision' },
        }),
      ],
      maxTokens: 1024,
      signal: controller.signal,
    })
    iterator = stream[Symbol.asyncIterator]()

    let terminalError: string | null = null
    for (;;) {
      const step = await nextChunkWithSignal(iterator, controller.signal)
      if (step.done) {
        streamFinished = true
        break
      }
      const chunk = step.value
      if (state.seqByCard.get(card) !== mySeq) {
        return { ok: false, pending: false, message: '已有更新的修改请求，这次拆分已取消' }
      }
      if (chunk.type === 'text-delta') {
        rawOutput += chunk.text
        if (rawOutput.length > MAX_REVISION_OUTPUT_CHARS) {
          controller.abort(OVERFLOW_REASON)
          return { ok: false, pending: false, message: '模型返回内容过长，请把修改意见写更具体后再试' }
        }
      } else if (chunk.type === 'block-end' && chunk.block.type === 'text' && !rawOutput) {
        rawOutput = chunk.block.text
        if (rawOutput.length > MAX_REVISION_OUTPUT_CHARS) {
          controller.abort(OVERFLOW_REASON)
          return { ok: false, pending: false, message: '模型返回内容过长，请把修改意见写更具体后再试' }
        }
      } else if (chunk.type === 'finish') {
        if (chunk.reason.kind === 'error' || chunk.reason.kind === 'aborted') {
          const reasonMsg = chunk.reason.failure?.message?.trim()
          terminalError = controller.signal.aborted
            ? formatRevisionLlmFailure(null, controller.signal)
            : (reasonMsg ? `拆分修改要求失败：${reasonMsg.slice(0, 120)}` : '拆分修改要求失败，请重试')
        } else if (chunk.reason.kind === 'max-tokens') {
          terminalError = '模型输出超出长度限制，未能完整返回拆分结果，请把修改意见写简短些再试'
        }
      }
    }

    if (terminalError) {
      return { ok: false, pending: false, message: terminalError }
    }
    if (controller.signal.aborted) {
      return { ok: false, pending: false, message: formatRevisionLlmFailure(null, controller.signal) }
    }
  } catch (error: unknown) {
    if (state.seqByCard.get(card) !== mySeq || controller.signal.reason === SUPERSEDED_REASON) {
      return { ok: false, pending: false, message: '已有更新的修改请求，这次拆分已取消' }
    }
    return { ok: false, pending: false, message: formatRevisionLlmFailure(error, controller.signal) }
  } finally {
    clearTimeout(timeoutHandle)
    if (!streamFinished && iterator?.return) {
      void Promise.resolve(iterator.return()).catch(() => {})
    }
    if (state.inflightByCard.get(card) === controller) {
      state.inflightByCard.delete(card)
    }
  }

  if (state.seqByCard.get(card) !== mySeq) {
    return { ok: false, pending: false, message: '已有更新的修改请求，这次拆分已取消' }
  }

  const parsed = parseRevisionModelOutput(rawOutput, criteriaBase)
  if (!parsed.ok) {
    return { ok: false, pending: false, message: parsed.message }
  }

  // 返回结果前二次核验：阶段和验收标准都没有在分析期间发生变化。
  let latestRawView = ''
  try {
    latestRawView = await host.view(card)
  } catch (error: unknown) {
    const msg = error instanceof Error ? error.message : String(error ?? '')
    return { ok: false, pending: false, message: `确认卡片状态失败：${msg || '请重试'}` }
  }
  if (state.seqByCard.get(card) !== mySeq) {
    return { ok: false, pending: false, message: '已有更新的修改请求，这次拆分已取消' }
  }
  const latestSnap = parseViewSnapshot(latestRawView, project, card)
  if (!latestSnap.ok) return { ok: false, pending: false, message: latestSnap.message }
  if (FINISHED_PHASE_CODES.has(latestSnap.phaseCode) || FINISHED_CARD_STATES.has(latestSnap.cardState)) {
    return { ok: false, pending: false, message: '这张卡已结束，要改请新建一张' }
  }
  if (!REVISION_ALLOWED_PHASES.has(latestSnap.phaseCode) || !latestSnap.actions.includes('revise')) {
    return { ok: false, pending: false, message: '卡片状态刚刚变化，当前不能再提交修改' }
  }

  if (parsed.changes.length === 0) {
    let actionRes: CardActionResult
    try {
      actionRes = validatedAttachments.length > 0
        ? await host.cardAction(card, 'revise', words, { attachments: validatedAttachments })
        : await host.cardAction(card, 'revise', words)
    } catch (error: unknown) {
      const msg = error instanceof Error && error.message ? error.message : '没交上，请再试一次'
      return { ok: false, pending: false, message: msg }
    }
    if (!actionRes.ok) {
      return { ok: false, pending: false, message: actionRes.message || '没交上，请再试一次' }
    }
    return { ok: true, pending: false, message: actionRes.message || '' }
  }

  if (!sameCriteriaList(criteriaBase, latestSnap.criteria)) {
    return { ok: false, pending: false, message: '验收标准刚刚发生变化，请再点一次重试' }
  }

  const summary = buildCriteriaSummary(criteriaBase, parsed.changes)
  try {
    host.requestChange(card, summary, words, {
      criteriaBase,
      criteriaChanges: parsed.changes,
      sourceDraft: sourceDraft ?? text,
      ...(validatedAttachments.length > 0 ? { attachments: validatedAttachments } : {}),
      ...(normalizedSourceIds !== undefined ? { sourceAttachmentIds: normalizedSourceIds } : {}),
    })
  } catch (error: unknown) {
    const msg = error instanceof Error && error.message ? error.message : '生成确认条失败，请再试一次'
    return { ok: false, pending: false, message: msg }
  }
  return { ok: true, pending: true, message: '' }
}
