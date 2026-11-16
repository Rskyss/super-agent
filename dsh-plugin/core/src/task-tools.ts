/**
 * 超级助手 · 任务模式的工具：查这张卡的真实进度、提出改要求（先出确认条，用户点了才转给调度员）。
 * 只挂在“超级助手 · 任务”工作模式里；任务卡对话的会话名是 task-<卡号>。
 */
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type {} from './index.ts'
import { cardOfTaskSession } from './task-session.ts'
import { parseProgressView, sanitizeProgressText } from './progress-model.ts'
import { buildCriteriaSummary, validateCriteriaChanges } from './revision.ts'
import { readCardAcceptanceCriteria } from './revision-host.ts'
import { FINISHED_CARD_STATES, FINISHED_PHASE_CODES, type CardViewPayload, type DispatcherTool } from './types.ts'

export { cardOfSession } from './task-session.ts'

export const name = 'superagent-task-tools'
export const inject = ['tools', 'superagentGateway']

/** 把后台的进度按共享七步进度模型整理成大白话，给模型看。 */
export function describeView(raw: string, plan: string): string {
  const payload = JSON.parse(raw) as CardViewPayload
  const progress = parseProgressView(raw, '')
  const phaseDetail = sanitizeProgressText(String(payload.card?.phase?.detail ?? '')).trim()
  const lines = [
    `卡片：${progress.title}`,
    `现在：${progress.phaseLabel}${phaseDetail ? `（${phaseDetail}）` : ''}`,
  ]
  for (const step of progress.steps) {
    const who = step.actor ? `（${step.actor}）` : ''
    const elapsed = step.elapsed ? `，${step.elapsed} 秒` : ''
    const summary = step.summary ? `；${step.summary}` : ''
    lines.push(`- ${step.label}${who}：${step.state}${elapsed}${summary}`)
  }
  const planText = plan ? (JSON.parse(plan) as { plan?: string }).plan : ''
  if (planText) lines.push('', '方案：', planText.slice(0, 3000))
  return lines.join('\n')
}

export function apply(ctx: Context): void {
  ctx.tools.register(defineTool({
    name: 'card_status',
    description: '读这张任务卡的真实进度：现在到哪一步、每一步谁在做、交回了什么、方案写了什么。用户问进度、问细节时先调用，不要凭记忆答。',
    parameters: {
      card: { type: 'string', description: '卡号；在任务卡对话里可以不填，自动用这张卡' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args, exec) {
      const card = String(args.card ?? '').trim() || cardOfTaskSession(ctx.superagentGateway.projectPath, String(exec.agent?.id ?? ''))
      if (!card) return '这不是任务卡对话，所以要写明卡号：用户提到了卡号（比如 CARD-001）就把它填进 card 再调用一次；没提到就问用户是哪张卡。'
      const view = await ctx.superagentGateway.view(card)
      let plan = '{}'
      let planNote = ''
      try {
        plan = await ctx.superagentGateway.plan(card)
      } catch (error: unknown) {
        // 还没写方案时后台回 400/404，属正常；别的错误要照实告诉模型（复查第 2 轮中等 3）。
        const status = (error as { details?: { status?: number } }).details?.status
        if (status !== 400 && status !== 404) planNote = `\n（方案没读到：${error instanceof Error ? error.message : String(error)}）`
      }
      return describeView(view, plan) + planNote
    },
  }))

  ctx.tools.register(defineTool({
    name: 'card_change',
    description: [
      '用户要改这张卡的要求（改验收标准、改做法、补一条）时调用：在输入框上面出一条确认条，用户点“确认改”才转给调度员。',
      'summary 用一句话写“旧 → 新”，text 是交给调度员的完整说明。如能拆出逐条修改，可同时传 criteria_changes JSON 数组字符串（replaceNth/text 或 add）。不要自己去改代码，也不要说已经改好了。',
    ].join(''),
    parameters: {
      summary: { type: 'string', required: true, description: '一句话：改什么，旧 → 新' },
      text: { type: 'string', required: true, description: '交给调度员的完整说明' },
      card: { type: 'string', description: '卡号；在任务卡对话里可以不填' },
      criteria_changes: {
        type: 'string',
        description: '可选：结构化逐条修改的 JSON 数组字符串，例如 [{"replaceNth":2,"text":"双击保存"},{"add":"成功提示"}]',
      },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args, exec) {
      const card = String(args.card ?? '').trim() || cardOfTaskSession(ctx.superagentGateway.projectPath, String(exec.agent?.id ?? ''))
      if (!card) return '这不是任务卡对话，所以要写明卡号：用户提到了卡号（比如 CARD-001）就把它填进 card 再调用一次；没提到就问用户是哪张卡。'
      const summary = String(args.summary ?? '').trim()
      const text = String(args.text ?? '').trim()
      if (!summary || !text) return '还缺改要求的摘要或完整说明，先补齐再出确认条。'
      // 出确认条前先查真实状态，已完成或不做了的卡不允许再改。
      let phaseCode = ''
      let cardState = ''
      let viewCriteria: string[] = []
      try {
        const raw = await ctx.superagentGateway.view(card)
        const parsed = JSON.parse(raw) as CardViewPayload
        phaseCode = String(parsed.card?.phase?.code ?? '')
        cardState = String(parsed.card?.state ?? '')
        if (Array.isArray(parsed.accept?.criteria)) {
          viewCriteria = parsed.accept.criteria.map(item => String(item).trim()).filter(Boolean)
        }
      } catch (error: unknown) {
        return `查不到 ${card} 的当前状态，先不出确认条：${error instanceof Error ? error.message : String(error)}`
      }
      if (!phaseCode && !cardState) {
        const row = (await ctx.superagentGateway.cards()).find(c => c.id === card)
        phaseCode = row?.phaseCode ?? ''
        cardState = row?.state ?? ''
      }
      if (FINISHED_PHASE_CODES.has(phaseCode) || FINISHED_CARD_STATES.has(cardState)) {
        return '这张卡已结束，要改请新建一张。'
      }

      const rawCriteriaChanges = args.criteria_changes === undefined ? '' : String(args.criteria_changes).trim()
      if (rawCriteriaChanges) {
        let parsedChanges: unknown
        try {
          parsedChanges = JSON.parse(rawCriteriaChanges) as unknown
        } catch {
          return 'criteria_changes 不是合法 JSON 数组，请修正后再试（或留空按普通修改处理）。'
        }
        if (!Array.isArray(parsedChanges)) {
          return 'criteria_changes 必须是 JSON 数组，例如 [{"replaceNth":2,"text":"双击保存"}]。'
        }
        if (parsedChanges.length > 0) {
          const criteriaBase = viewCriteria.length > 0
            ? viewCriteria
            : readCardAcceptanceCriteria(ctx.superagentGateway.projectPath, card).criteria
          const checked = validateCriteriaChanges(criteriaBase, parsedChanges)
          if (!checked.ok) {
            return `逐条修改格式不对，先不出确认条：${checked.message}`
          }
          const authoritativeSummary = buildCriteriaSummary(checked.base, checked.changes)
          ctx.superagentGateway.requestChange(card, authoritativeSummary, text, {
            criteriaBase: checked.base,
            criteriaChanges: checked.changes,
          })
          return '确认条已贴在输入框上面，等用户点“确认改”或“算了”。还没交给调度员。'
        }
      }

      ctx.superagentGateway.requestChange(card, summary, text)
      return '确认条已贴在输入框上面，等用户点“确认改”或“算了”。还没交给调度员。'
    },
  }))

  ctx.tools.register(defineTool({
    name: 'propose_dispatcher',
    description: [
      '在当前任务对话里出示“接一个订阅调度员（仅支持 Codex 或 agy）”确认卡，贴在输入框上方。',
      '只是出示确认卡，绝不直接替用户确认、保存角色、安装软件或跑命令；用户点确认并真实检测通过后，会自动为这张未写方案的卡启动写方案。',
    ].join(''),
    parameters: {
      tool: { type: 'string', description: '订阅工具名，只允许 codex 或 agy（默认 codex）' },
      card: { type: 'string', description: '卡号；在任务卡对话里可以不填，自动关联当前这张卡' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args, exec) {
      const rawTool = String(args.tool ?? 'codex').trim().toLowerCase()
      if (rawTool !== 'codex' && rawTool !== 'agy') {
        return '调度员只支持订阅工具 Codex 或 agy（不支持其他工具）。请问用户选 Codex 还是 agy。'
      }
      const sessionId = String(exec.agent?.id ?? '')
      if (!sessionId) return '找不到当前对话，没能出示接调度员确认卡。'
      const card = String(args.card ?? '').trim() || cardOfTaskSession(ctx.superagentGateway.projectPath, sessionId)
      const proposal = ctx.superagentGateway.proposeDispatcher(sessionId, rawTool as DispatcherTool, card)
      return `已向用户出示「${proposal.title}」确认卡（贴在输入框上方）。请告诉用户看清卡上的说明，点确认后才会真实检测并保存调度员；接通后会自动为这张未写方案的卡开始写方案。`
    },
  }))
}
