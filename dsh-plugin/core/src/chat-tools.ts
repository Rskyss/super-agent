/**
 * 超级助手 · 聊天模式的工具：出确认卡（只出示，不建卡）、查任务列表。
 * 只挂在“超级助手 · 聊天”工作模式里。
 */
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type {} from './index.ts'
import type { DispatcherTool, ProposalQuestion } from './types.ts'

export const name = 'superagent-chat-tools'
export const inject = ['tools', 'superagentGateway']

const PROPOSE = [
  '把用户要干的一件活整理成一张确认卡，贴在输入框上面给用户确认。只是出示，不建卡：用户点“确认，建任务”才建。',
  '四项都要有：标题、要做什么、不做什么、验收标准。拿不准的写进 questions，每条带建议答案（用户不答也能直接确认、按建议走）。',
  '一次只出一张：再调用会替换掉旧的那张。只是聊聊想法、问进度时不要调用。',
].join('')

/** 解析“问题｜建议答案”一行一条的写法。 */
export function parseQuestions(text: string): ProposalQuestion[] {
  return text.split('\n').map(line => line.trim()).filter(Boolean).slice(0, 5).map((line) => {
    const [question, ...rest] = line.split(/[｜|]/)
    // 模型常把“建议：”也写进来，界面会显示成“建议：建议：…”，去掉开头的。
    return { question: (question ?? '').trim(), suggestion: rest.join('｜').trim().replace(/^建议[:：]\s*/, '') }
  }).filter(q => q.question.length > 0)
}

export function apply(ctx: Context): void {
  ctx.tools.register(defineTool({
    name: 'propose_task',
    description: PROPOSE,
    parameters: {
      title: { type: 'string', required: true, description: '卡片标题，十几个字以内' },
      do: { type: 'string', required: true, description: '要做什么，一两句大白话' },
      not_do: { type: 'string', required: true, description: '明确不做什么' },
      done_when: { type: 'string', required: true, description: '验收标准，分条写，用 ① ② ③ 开头' },
      questions: { type: 'string', description: '要用户拍板的问题，一行一条，写成“问题｜建议答案”' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args, exec) {
      const fields = [args.title, args.do, args.not_do, args.done_when].map(v => String(v ?? '').trim())
      if (fields.some(v => v.length === 0)) return '还缺标题、要做什么、不做什么或验收标准，先问用户。'
      const sessionId = String(exec.agent?.id ?? '')
      if (!sessionId) return '找不到当前对话，没能出确认卡。'
      ctx.superagentGateway.propose(sessionId, {
        title: fields[0]!, do: fields[1]!, notDo: fields[2]!, doneWhen: fields[3]!,
        questions: parseQuestions(String(args.questions ?? '')),
      })
      return '确认卡已贴在输入框上面，等用户点“确认，建任务”。还没建卡。跟用户只说一句：点确认就建任务卡，之后调度员写方案给你批。'
    },
  }))

  ctx.tools.register(defineTool({
    name: 'list_tasks',
    description: '查当前项目所有任务卡：编号、标题、现在到哪一步、是不是在等用户处理。用户问进度、问有哪些任务时先查，不要凭记忆答。',
    parameters: {},
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute() {
      const cards = await ctx.superagentGateway.cards()
      if (cards.length === 0) return '还没有任务卡。'
      return cards.map(c => `${c.id}　${c.title}　${c.phaseLabel}${c.needsYou ? '（等用户处理）' : ''}`).join('\n')
    },
  }))

  ctx.tools.register(defineTool({
    name: 'propose_dispatcher',
    description: [
      '把“接一个订阅调度员（仅支持 Codex 或 agy）”整理成一张接入确认卡，贴在输入框上面让用户确认。',
      '只是出示确认卡（讲清是什么、怎么接、需要用户做什么、影响范围），绝不直接替用户确认、保存角色、安装软件或跑终端命令。',
    ].join(''),
    parameters: {
      tool: { type: 'string', description: '订阅工具名，只允许 codex 或 agy（默认 codex）' },
      card: { type: 'string', description: '可选：待写方案的任务卡号（接通后只启动这张卡写方案）' },
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
      const card = String(args.card ?? '').trim()
      const proposal = ctx.superagentGateway.proposeDispatcher(sessionId, rawTool as DispatcherTool, card)
      return `已向用户出示「${proposal.title}」确认卡（贴在输入框上方）。请告诉用户先看卡里的说明，点确认后才开始检测和保存，目前还没接通。`
    },
  }))
}
