import { describe, expect, it } from 'vitest'
import { cardFieldsFromProposal } from '../src/proposal.ts'

const base = { id: 'p1', session: 's1', title: '加 clear', do: '加 clear 命令', notDo: '不改别的', doneWhen: '① 已完成的清掉', createdAt: 0 }

describe('确认卡转成建卡字段', () => {
  it('没有问号时原样', () => {
    expect(cardFieldsFromProposal({ ...base, questions: [] })).toEqual({ title: '加 clear', do: '加 clear 命令', not_do: '不改别的', done_when: '① 已完成的清掉' })
  })
  it('问号没答的按建议写进“要做什么”，并标“按建议”', () => {
    const got = cardFieldsFromProposal({ ...base, questions: [{ question: '要不要确认', suggestion: '不用' }, { question: '空的建议', suggestion: '' }] })
    expect(got.do).toContain('加 clear 命令')
    expect(got.do).toContain('- 要不要确认：不用（按建议）')
    expect(got.do).toContain('- 空的建议：（没给建议，写方案时再问）（按建议）')
  })
})
