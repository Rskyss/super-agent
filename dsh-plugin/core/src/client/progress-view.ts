/**
 * 客户端兼容入口：从共享纯计算模块 re-export 七步进度解析与文案工具。
 */
import {
  formatStepElapsed,
  parseProgressView,
  sanitizeProgressText,
  summarizePlainText,
} from '../progress-model.ts'

export {
  formatStepElapsed,
  parseProgressView,
  sanitizeProgressText,
  summarizePlainText,
}

/** 头部与进度面板共用的任务状态展示入参。 */
export interface TaskDisplayStatusInput {
  readonly phaseCode?: string | undefined
  readonly phaseLabel?: string | undefined
  readonly state?: string | undefined
  readonly isRunningNow?: boolean | undefined
}

/**
 * 归一化任务展示状态文案：优先映射等待/阻断阶段，再根据真实运行态区分运行中与暂停。
 */
export function formatTaskDisplayStatus(input: TaskDisplayStatusInput | null | undefined): string {
  if (!input) return ''
  const phaseCode = input.phaseCode?.trim() ?? ''
  if (phaseCode === 'plan_ready') return '等你批方案'
  if (phaseCode === 'review') return '等你验收'
  if (phaseCode === 'blocked') return '停下了 · 需要你决定'
  if (input.isRunningNow === true) return '运行中'
  if ((phaseCode === 'running' || phaseCode === 'ready') && input.isRunningNow === false) {
    return '暂停'
  }
  const rawFallback = input.phaseLabel?.trim() || input.state?.trim() || ''
  return rawFallback ? sanitizeProgressText(rawFallback).trim() : ''
}
