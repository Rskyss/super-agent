import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'
import { buildRevisionPrompt } from '../src/revision.ts'

it('新版与旧版实际发送的拆分规矩一致，包括有图和无图的分支', () => {
  const repo = [process.cwd(), resolve(process.cwd(), '../super-agent')].find(p => existsSync(resolve(p, 'tests/test_revision_all_entries.py')))
  expect(repo, '找不到本项目旧版后台，无法核对两边真实拆分规矩').toBeTruthy()
  const inputs = [0, 1].map(attachment_count => ({ criteria: ['标题黑色', '单击保存'], text: '按要求改第二条', attachment_count }))
  const result = spawnSync('python3', ['-c', 'import sys,json;from desktop.clerk.revision import build_revision_prompt;items=json.load(sys.stdin);print(json.dumps([build_revision_prompt(v["criteria"],v["text"],v["attachment_count"]) for v in items],ensure_ascii=False))'], { cwd: repo, input: JSON.stringify(inputs), encoding: 'utf8', timeout: 10000 })
  expect(result.status, result.stderr).toBe(0)
  expect(JSON.parse(result.stdout)).toEqual(inputs.map(v => buildRevisionPrompt(v.criteria, v.text, v.attachment_count)))
})
