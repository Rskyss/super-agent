/**
 * Host 侧只读工作流信息读取：
 * - 执行段步骤名复用共享 progress-model 的 STEP_LABELS；
 * - 复查轮次上限从 repoRoot/collab/dispatch.py 的字面 MAX_REVIEW_RETURNS + 1 读取，读不到返回 null；
 * - 当前无独立工作流版本源，version 固定返回 null，绝不编造。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { STEP_LABELS } from './progress-model.ts'
import type { ProgressStepKey, WorkflowInfo } from './types.ts'

const EXECUTION_STEP_KEYS: ReadonlySet<ProgressStepKey> = new Set(['test', 'dev', 'check', 'rev', 'accept'])
const MAX_REVIEW_RETURNS_RE = /^[ \t]*MAX_REVIEW_RETURNS[ \t]*=[ \t]*(\d+)/m

function readMaxReviewRounds(repoRoot: string): number | null {
  const cleanRoot = typeof repoRoot === 'string' ? repoRoot.trim() : ''
  if (!cleanRoot) return null
  try {
    const source = readFileSync(join(cleanRoot, 'collab', 'dispatch.py'), 'utf8')
    const match = MAX_REVIEW_RETURNS_RE.exec(source)
    if (!match) return null
    const returnsLimit = Number(match[1])
    if (!Number.isInteger(returnsLimit) || returnsLimit < 0) return null
    return returnsLimit + 1
  } catch {
    return null
  }
}

/**
 * 读取当前默认开发工作流真实摘要（纯 Host 只读，不写文件、不编造版本与轮次）。
 * @param repoRoot - 超级助手仓库根目录。
 * @returns 工作流名称、执行段步骤、真实复查轮次上限及版本（当前为 null）。
 */
export function readCurrentWorkFlow(repoRoot: string): WorkflowInfo {
  const steps = STEP_LABELS
    .filter(spec => EXECUTION_STEP_KEYS.has(spec.key))
    .map(spec => spec.label)

  return {
    name: '开发软件',
    steps,
    maxReviewRounds: readMaxReviewRounds(repoRoot),
    version: null,
  }
}
