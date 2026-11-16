/** 确认卡 → 建卡字段（纯逻辑，好测）。问号没答的按建议写进“要做什么”，并标“按建议”。 */
import type { Proposal } from './types.ts'

export interface CardFields {
  readonly title: string
  readonly do: string
  readonly not_do: string
  readonly done_when: string
}

export function cardFieldsFromProposal(p: Proposal): CardFields {
  const answers = p.questions
    .filter(q => q.question.trim())
    .map(q => `- ${q.question.trim()}：${q.suggestion.trim() || '（没给建议，写方案时再问）'}（按建议）`)
  const doText = answers.length ? `${p.do}\n\n拍板（问号没答的按建议）：\n${answers.join('\n')}` : p.do
  return { title: p.title, do: doText, not_do: p.notDo, done_when: p.doneWhen }
}
