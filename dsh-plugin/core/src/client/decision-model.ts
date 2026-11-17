/**
 * 把任务卡的当前阶段、方案原文和验收信息解析为输入框上方的决定卡模型。
 * 只做纯数据解析与文案脱敏，不直接发请求或操作浏览器对象。
 */
import type { CardPlanPayload, CardViewPayload, StopReviewRow } from '../types.ts'
import { formatWorkDuration } from '../work-duration.ts'
import { sanitizeProgressText, summarizePlainText } from './progress-view.ts'

export type DecisionKind = 'plan' | 'accept' | 'stop'

export interface DecisionAction {
  readonly action: string
  readonly label: string
  readonly primary: boolean
  readonly needsDraft: boolean
}

export interface DecisionRow {
  readonly label: string
  readonly value: string
}

export interface PlanDecisionView {
  readonly kind: 'plan'
  readonly id: string
  readonly card: string
  readonly phaseCode: string
  readonly title: string
  readonly statusLabel: string
  readonly version: number
  readonly rows: readonly DecisionRow[]
  readonly detailSections: readonly DecisionRow[]
  readonly actions: readonly DecisionAction[]
}

export interface AcceptDecisionView {
  readonly kind: 'accept'
  readonly id: string
  readonly card: string
  readonly phaseCode: string
  readonly title: string
  readonly statusLabel: string
  readonly workElapsed: number | null
  readonly result: string
  readonly how: string
  readonly criteria: readonly string[]
  readonly changeNotes: readonly string[]
  readonly criteriaUpdated: boolean
  readonly canPreview: boolean
  readonly copyThere: boolean
  readonly actions: readonly DecisionAction[]
}

export interface StopDecisionView {
  readonly kind: 'stop'
  readonly id: string
  readonly card: string
  readonly phaseCode: string
  readonly title: string
  readonly statusLabel: string
  readonly reason: string
  readonly detailText: string
  readonly stopReviews: readonly StopReviewRow[]
  readonly stopAdvice: string
  readonly actions: readonly DecisionAction[]
}

export type DecisionCardView = PlanDecisionView | AcceptDecisionView | StopDecisionView

const PLAN_PHASES = new Set(['plan_ready', 'plan_waiting'])
const ACCEPT_PHASES = new Set(['review', 'merge_failed'])
const STOP_PHASES = new Set(['blocked', 'interrupted', 'start_failed', 'plan_failed', 'copy_missing'])
const LOCAL_PREVIEW_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]'])

const PLAN_ACTION_META: Record<string, { label: string; primary?: boolean; needsDraft?: boolean }> = {
  approve: { label: '批准，开工', primary: true },
  revise: { label: '我要改', needsDraft: true },
  discard: { label: '先不做' },
}

const ACCEPT_ACTION_META: Record<string, { label: string; primary?: boolean; needsDraft?: boolean }> = {
  accept: { label: '没问题，放进项目', primary: true },
  revise: { label: '继续改', needsDraft: true },
  discard: { label: '先不做' },
  resume: { label: '再跑一次' },
  replan: { label: '重写一次' },
  accept_issues: { label: '接受剩下的问题，去验收' },
}

const STOP_ACTION_ORDER: readonly string[] = ['accept_issues', 'revise', 'resume', 'replan', 'discard']

const STOP_ACTION_META: Record<string, { label: string; primary?: boolean; needsDraft?: boolean }> = {
  accept_issues: { label: '接受剩下的问题，去验收', primary: true },
  revise: { label: '按我说的改', needsDraft: true },
  resume: { label: '再跑一次' },
  discard: { label: '先不做' },
  replan: { label: '重写一次' },
}

const EMPTY_QUESTION_RE = /^(?:无|没有|暂无|不需要|不用确认|无需确认|无问题)[。！!.]*$/
const COMMAND_OR_PATH_RE = /`[^`]+`|(?:^|[\s(])(?:src|tests|docs|collab|desktop|dsh-plugin)\/|\b(?:pytest|vitest|npm|pnpm|yarn|python3?|node|tsc|eslint|git)\b|[A-Za-z0-9_-]+\.(?:tsx?|jsx?|py|json|css|md)\b/i

/**
 * 严格判定当前停下视图是否确由“复查不通过”触发，防止非复查停因被历史复查记录改名。
 */
export function isReviewFailureStopView(view: CardViewPayload | null | undefined): boolean {
  if (!view || typeof view !== 'object') return false
  const phase = view.card?.phase
  const phaseCode = String(phase?.code ?? '')
  const actions = Array.isArray(phase?.actions) ? phase.actions.map(String) : []
  if (actions.length > 0) {
    return actions.includes('accept_issues')
  }
  if (phaseCode && phaseCode !== 'blocked') return false
  const reviews = Array.isArray(view.stopReviews) ? view.stopReviews : []
  if (reviews.length === 0) return false
  const reason = String(phase?.issues ?? phase?.detail ?? view.now?.text ?? '')
  if (reason && !/复查/.test(reason) && /越界|撤回|崩溃|中断|启动|超时/.test(reason)) {
    return false
  }
  return true
}

/**
 * 校验网页预览地址是否为允许在本机打开的 HTTP 地址。
 * @param rawUrl - 后台返回的预览地址。
 * @returns 是否安全可打开。
 */
export function isLocalHttpUrl(rawUrl: unknown): rawUrl is string {
  if (typeof rawUrl !== 'string' || !rawUrl.trim()) return false
  try {
    const parsed = new URL(rawUrl)
    return parsed.protocol === 'http:' && LOCAL_PREVIEW_HOSTS.has(parsed.hostname) && !parsed.username && !parsed.password
  } catch (_error) {
    return false
  }
}

/**
 * 从方案与进度载荷判断当前是否属于需要用户处理的阶段。
 * @param phaseCode - 当前阶段代码。
 * @returns 是否处于方案等待确认阶段。
 */
export function isPlanPhase(phaseCode: string): boolean {
  return PLAN_PHASES.has(phaseCode)
}

/**
 * 解析方案 markdown 的六个常规小节，并清洗旧称与技术词汇。
 * @param markdown - 方案正文 markdown。
 * @returns 各小节正文。
 */
export function parsePlanMarkdown(markdown: string): {
  goal: string
  steps: string
  outcome: string
  notDo: string
  questions: string
  howToCheck: string
} {
  const sections = markdown.split(/^##\s+/m)
  let goal = ''
  let steps = ''
  let outcome = ''
  let notDo = ''
  let questions = ''
  let howToCheck = ''

  for (const section of sections) {
    const trimmed = section.trim()
    if (!trimmed) continue
    const newlineIdx = trimmed.indexOf('\n')
    const heading = (newlineIdx === -1 ? trimmed : trimmed.slice(0, newlineIdx)).trim()
    const body = (newlineIdx === -1 ? '' : trimmed.slice(newlineIdx + 1)).trim()
    if (!body) continue
    if (heading.startsWith('要做什么')) goal = body
    else if (heading.startsWith('拆成几步') || heading.startsWith('分几步')) steps = body
    else if (heading.startsWith('你会看到') || heading.startsWith('做完你会看到')) outcome = body
    else if (heading.startsWith('明确不做什么') || heading.startsWith('不做什么')) notDo = body
    else if (heading.startsWith('需要你确认') || heading.startsWith('要你拍板')) questions = body
    else if (heading.startsWith('做完后怎么看') || heading.startsWith('怎么算做好')) howToCheck = body
  }

  return { goal, steps, outcome, notDo, questions, howToCheck }
}

/**
 * 将后台视图和可选方案组合为唯一一张待用户决定的卡片模型；不需要用户处理时返回 null。
 * @param card - 任务卡编号。
 * @param view - 后台 view 载荷。
 * @param plan - 后台 plan 载荷（仅方案阶段需要）。
 * @returns 决定卡数据或 null。
 */
export function buildDecisionView(
  card: string,
  view: CardViewPayload,
  plan: CardPlanPayload | null,
): DecisionCardView | null {
  const phase = view.card?.phase
  const phaseCode = String(phase?.code ?? '')
  const rawActions = Array.isArray(phase?.actions) ? phase.actions.map(String) : []
  const round = typeof view.card?.round === 'number' ? view.card.round : 0
  const rework = typeof view.card?.rework === 'number' ? view.card.rework : 0

  if (PLAN_PHASES.has(phaseCode)) {
    return buildPlanDecision(card, phaseCode, rawActions, round, rework, plan)
  }
  if (ACCEPT_PHASES.has(phaseCode)) {
    return buildAcceptDecision(card, phaseCode, rawActions, round, rework, view)
  }
  if (STOP_PHASES.has(phaseCode)) {
    return buildStopDecision(card, phaseCode, rawActions, round, rework, view)
  }
  return null
}

function buildPlanDecision(
  card: string,
  phaseCode: string,
  rawActions: readonly string[],
  round: number,
  rework: number,
  plan: CardPlanPayload | null,
): PlanDecisionView | null {
  if (!plan) return null
  const markdown = String(plan.plan ?? '')
  const parsed = parsePlanMarkdown(markdown)
  const allowed = Array.isArray(plan.allowed) ? plan.allowed.filter((item): item is string => typeof item === 'string' && item.trim().length > 0) : []
  const testCommand = typeof plan.test_command === 'string' ? plan.test_command.trim() : ''
  const version = typeof plan.version === 'number' && Number.isFinite(plan.version) && plan.version >= 1 ? plan.version : 1

  const rows: DecisionRow[] = [
    { label: '要做什么', value: cleanPlainSummary(parsed.goal) || '按这张任务卡的需求准备改造方案' },
    { label: '分几步', value: summarizeSteps(parsed.steps) || '按方案步骤依次推进' },
    { label: '做完你会看到', value: cleanPlainSummary(parsed.outcome) || '完成后会给你明确的结果反馈' },
    { label: '不做什么', value: cleanPlainSummary(parsed.notDo) || '不改动无关模块和原有设置' },
    { label: '只动这些地方', value: allowed.length > 0 ? `约定的 ${allowed.length} 处` : '约定的任务范围内' },
    { label: '怎么算做好', value: summarizeCheckRule(parsed.howToCheck, testCommand) },
    { label: '要你拍板的', value: summarizeQuestions(parsed.questions) },
  ]

  const detailSections: DecisionRow[] = []
  if (allowed.length > 0) {
    detailSections.push({ label: '允许改动的具体位置', value: allowed.join('\n') })
  }
  if (testCommand) {
    detailSections.push({ label: '检查方式原文', value: sanitizeProgressText(testCommand) })
  }
  if (markdown.trim()) {
    detailSections.push({ label: '完整方案原文', value: sanitizeProgressText(markdown) })
  }

  // 04a 方案卡按图仅保留“批准，开工 / 我要改”；原先不做（discard）注释保留：
  // const actions = buildActions(rawActions, PLAN_ACTION_META, ['approve'])
  const actions = buildActions(
    rawActions.filter(action => action !== 'discard'),
    PLAN_ACTION_META,
    ['approve'],
  )
  const fingerprint = hashText(`${markdown}\n${allowed.join(',')}\n${testCommand}`)
  return {
    kind: 'plan',
    id: `plan:${phaseCode}:v${version}:r${round}:rw${rework}:${fingerprint}:${actions.map(a => a.action).join(',')}`,
    card,
    phaseCode,
    title: `方案 · 第 ${version} 版`,
    statusLabel: '等你批',
    version,
    rows,
    detailSections,
    actions,
  }
}

function buildAcceptDecision(
  card: string,
  phaseCode: string,
  rawActions: readonly string[],
  round: number,
  rework: number,
  view: CardViewPayload,
): AcceptDecisionView {
  const accept = view.accept
  const result = sanitizeProgressText(String(accept?.result ?? '').trim()) || '暂未读到完成汇报，请先查看预览核对'
  const how = sanitizeProgressText(String(accept?.how ?? '').trim()) || '对照下方验收标准逐条确认。'
  const criteria = Array.isArray(accept?.criteria)
    ? accept.criteria.map(item => sanitizeProgressText(String(item).trim())).filter(Boolean)
    : []
  const changeNotes = Array.isArray(accept?.changeNotes)
    ? accept.changeNotes.map(item => sanitizeProgressText(String(item).trim())).filter(Boolean)
    : []
  const criteriaUpdated = accept?.criteriaUpdated === true
  const canPreview = accept?.can_preview === true
  const copyThere = accept?.copy_there === true
  const workElapsed = typeof view.workElapsed === 'number' && Number.isFinite(view.workElapsed) && view.workElapsed >= 0
    ? view.workElapsed
    : null
  const statusLabel = workElapsed !== null
    ? `等你 · 用时 ${formatWorkDuration(workElapsed)}`
    : phaseCode === 'merge_failed'
      ? '放回项目时冲突'
      : '待验收'
  // 05 验收卡按图仅保留“打开预览窗口 / 没问题，放进项目 / 继续改”；原先不做（discard）注释保留：
  // const actions = buildActions(rawActions, ACCEPT_ACTION_META, ['accept', 'resume', 'accept_issues'])
  const actions = buildActions(
    rawActions.filter(action => action !== 'discard'),
    ACCEPT_ACTION_META,
    ['accept', 'resume', 'accept_issues'],
  )
  const fingerprint = hashText(`${result}|${how}|${criteria.join(';')}|${changeNotes.join(';')}|${String(criteriaUpdated)}|${String(canPreview)}`)

  return {
    kind: 'accept',
    id: `accept:${phaseCode}:r${round}:rw${rework}:${fingerprint}:${actions.map(a => a.action).join(',')}`,
    card,
    phaseCode,
    title: '做好了，请你验收',
    statusLabel,
    workElapsed,
    result,
    how,
    criteria,
    changeNotes,
    criteriaUpdated,
    canPreview,
    copyThere,
    actions,
  }
}

function buildStopDecision(
  card: string,
  phaseCode: string,
  rawActions: readonly string[],
  round: number,
  rework: number,
  view: CardViewPayload,
): StopDecisionView {
  const rawIssues = String(view.card?.phase?.issues ?? '').trim()
  const rawDetail = String(view.card?.phase?.detail ?? '').trim()
  const rawState = String(view.card?.state ?? '').trim()
  const reason = sanitizeProgressText(rawIssues || rawDetail || rawState) || '当前进度停下来了，请你决定下一步怎么做。'
  const detailText = rawIssues && rawDetail && rawIssues !== rawDetail ? sanitizeProgressText(rawDetail) : ''
  const isReviewStop = isReviewFailureStopView(view)
  const stopReviews: StopReviewRow[] = isReviewStop && Array.isArray(view.stopReviews)
    ? view.stopReviews
      .filter((row): row is StopReviewRow => Boolean(row && typeof row === 'object'))
      .map((row, idx) => ({
        round: typeof row.round === 'number' && Number.isFinite(row.round) && row.round >= 1 ? row.round : idx + 1,
        found: sanitizeProgressText(String(row.found ?? '').trim()) || '无记录',
        changed: sanitizeProgressText(String(row.changed ?? '').trim()) || '无修改记录',
      }))
    : []
  const stopAdvice = typeof view.stopAdvice === 'string' && view.stopAdvice.trim()
    ? sanitizeProgressText(view.stopAdvice.trim())
    : ''
  const orderedActions = [
    ...STOP_ACTION_ORDER.filter(name => rawActions.includes(name)),
    ...rawActions.filter(name => !STOP_ACTION_ORDER.includes(name)),
  ]
  const actions = buildActions(orderedActions, STOP_ACTION_META, ['accept_issues', 'resume', 'replan'])
  const fingerprint = hashText(
    `${reason}|${detailText}|${stopAdvice}|${stopReviews.map(r => `${r.round}:${r.found}:${r.changed}`).join(';')}`,
  )
  const rawStopCause = !isReviewStop
    ? sanitizeProgressText(rawIssues || '') || sanitizeProgressText(rawDetail || '')
    : ''
  const stopCause = rawStopCause ? summarizePlainText(rawStopCause) : ''
  const title = isReviewStop && stopReviews.length > 0
    ? `复查 ${stopReviews.length} 轮都没通过，停下了`
    : stopCause || '停下了 · 需要你决定'

  return {
    kind: 'stop',
    id: `stop:${phaseCode}:r${round}:rw${rework}:${fingerprint}:${actions.map(a => a.action).join(',')}`,
    card,
    phaseCode,
    title,
    statusLabel: '需要你',
    reason,
    detailText,
    stopReviews,
    stopAdvice,
    actions,
  }
}

function buildActions(
  rawActions: readonly string[],
  metaMap: Record<string, { label: string; primary?: boolean; needsDraft?: boolean }>,
  primaryPreference: readonly string[],
): DecisionAction[] {
  const result: DecisionAction[] = []
  const seen = new Set<string>()
  for (const action of rawActions) {
    if (seen.has(action)) continue
    const meta = metaMap[action]
    if (!meta) continue
    seen.add(action)
    result.push({
      action,
      label: meta.label,
      primary: false,
      needsDraft: meta.needsDraft === true,
    })
  }
  if (result.length === 0) return result
  const preferred = primaryPreference.find(name => result.some(item => item.action === name)) ?? result[0]?.action
  return result.map(item => ({
    ...item,
    primary: item.action === preferred,
  }))
}

const SHELL_COMMAND_RE = /\b(?:python3?|pytest|vitest|npm|pnpm|yarn|node|tsc|eslint|unittest|uv|pip3?)(?:\s+(?:[-A-Za-z0-9_./:=]+|"[^"]*"|'[^']*'))+/gi
const FILE_OR_PATH_RE = /(?:[A-Za-z0-9_.-]+\/)+[A-Za-z0-9_.-]*[A-Za-z][A-Za-z0-9_.-]*|[A-Za-z0-9_.-]+\.(?:py|tsx?|jsx?|css|scss|json|ya?ml|toml|md|sh|txt|html|sql|rs|go|java|swift|rb)\b/gi

function toPlainPlanText(text: string): string {
  return sanitizeProgressText(text)
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(SHELL_COMMAND_RE, match => (/(?:test|pytest|vitest|unittest)/i.test(match) ? '相关检查命令' : '对应运行命令'))
    .replace(FILE_OR_PATH_RE, (match) => {
      if (/readme/i.test(match) || /\.md$/i.test(match)) return '说明文档'
      if (/(?:test|spec)/i.test(match)) return '相关检查'
      return '相关程序'
    })
    .replace(/([\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])/g, '$1')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

function cleanPlainSummary(text: string): string {
  const cleaned = toPlainPlanText(text)
  return summarizePlainText(cleaned)
}

function summarizeSteps(text: string): string {
  if (!text.trim()) return ''
  const lines = toPlainPlanText(text)
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .map(line => line.replace(/^[-*•]\s*/, '').replace(/^\d+[.、)\]]\s*/, '').trim())
    .filter(Boolean)
  if (lines.length === 0) return ''
  if (lines.length <= 3) return `共 ${lines.length} 步：${lines.join('；')}`
  return `共 ${lines.length} 步：${lines.slice(0, 2).join('；')}等`
}

function summarizeCheckRule(body: string, testCommand: string): string {
  if (testCommand || COMMAND_OR_PATH_RE.test(body)) {
    return '约定检查和原有测试均通过'
  }
  const plain = cleanPlainSummary(body)
  return plain || '约定检查和原有测试均通过'
}

function summarizeQuestions(body: string): string {
  const cleaned = cleanPlainSummary(body)
  if (!cleaned || EMPTY_QUESTION_RE.test(cleaned)) return '没有'
  return cleaned
}

function hashText(text: string): string {
  let hash = 2166136261
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(36)
}
