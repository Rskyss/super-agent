/**
 * 右栏七步进度面板：每 2 秒读取真实进度，按固定七步展示工具、模型、用时与大白话说明。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react'
import {
  Button,
  IconChevronDownOutlineRegular,
  IconChevronRightOutlineRegular,
  StateDot,
  Tag,
  type StateDotState,
} from '@deepseek-ai/dsh-client-ui-primitives'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { ParsedCardProgress, ProgressStepKey, ProgressStepState } from '../types.ts'
import { formatWorkDuration } from '../work-duration.ts'
import {
  formatStepElapsed,
  formatTaskDisplayStatus,
  parseProgressView,
  sanitizeProgressText,
} from './progress-view.ts'
import css from './ProgressPanel.module.css'

/** ProgressPanel 对外公开属性（与组件测试保持一致）。 */
export interface ProgressPanelProps {
  readonly card: string
  readonly view: (card: string) => Promise<RemoteResult<string>>
}

function toDotState(state: ProgressStepState): StateDotState {
  if (state === 'done') return 'done'
  if (state === 'active') return 'ongoing'
  if (state === 'wait') return 'warning'
  if (state === 'fail') return 'error'
  return 'idle'
}

function resolveCurrentStepNumber(progress: ParsedCardProgress | null): number {
  if (!progress || progress.steps.length === 0) return 1
  const currentIdx = progress.steps.findIndex(step => step.current)
  if (currentIdx >= 0) return currentIdx + 1
  const failIdx = progress.steps.findIndex(step => step.state === 'fail')
  if (failIdx >= 0) return failIdx + 1
  if (progress.steps.every(step => step.state === 'done')) return progress.steps.length
  const pendingIdx = progress.steps.findIndex(step => step.state !== 'done')
  return pendingIdx >= 0 ? pendingIdx + 1 : 1
}

function resolveHeaderBadge(progress: ParsedCardProgress | null): { readonly label: string; readonly waiting: boolean } | null {
  if (!progress) return null
  const statusLabel = formatTaskDisplayStatus(progress)
  const isWaitingPhase = progress.phaseCode === 'plan_ready'
    || progress.phaseCode === 'review'
    || progress.phaseCode === 'blocked'
  const isWaiting = isWaitingPhase || (
    !progress.isRunningNow
    && progress.phaseCode !== 'done'
    && progress.phaseCode !== 'dropped'
    && statusLabel.length > 0
  )
  if (isWaiting) {
    return { label: statusLabel, waiting: true }
  }
  if (progress.workElapsed !== null) {
    return { label: `干活用时 ${formatWorkDuration(progress.workElapsed)}`, waiting: false }
  }
  if (statusLabel) {
    return { label: statusLabel, waiting: false }
  }
  return null
}

/** 按 card 隔离实例，切换任务卡时立刻清掉旧卡的轮询和展开状态。 */
export function ProgressPanel(props: ProgressPanelProps): ReactElement {
  return <ProgressPanelCard key={props.card} {...props} />
}

function ProgressPanelCard({ card, view }: ProgressPanelProps): ReactElement {
  const viewRef = useRef(view)
  viewRef.current = view

  const [progress, setProgress] = useState<ParsedCardProgress | null>(null)
  const [error, setError] = useState('')
  const [expandedKeys, setExpandedKeys] = useState<ReadonlySet<ProgressStepKey>>(new Set())

  useEffect(() => {
    let alive = true
    let seq = 0
    let applied = 0

    const applyResult = (mine: number, result: RemoteResult<string> | undefined, errText?: string) => {
      if (!alive || mine <= applied) return
      applied = mine
      if (result === undefined || !result.ok) {
        const detail = errText ?? (result && !result.ok ? result.error.message : '')
        setError(detail ? `读不到最新进度：${sanitizeProgressText(detail)}` : '读不到最新进度')
        return
      }
      try {
        const parsed = parseProgressView(result.value, card)
        setProgress(parsed)
        setError('')
      } catch (parseError: unknown) {
        const message = parseError instanceof Error ? parseError.message : String(parseError)
        setError(`读不到最新进度：${sanitizeProgressText(message)}`)
      }
    }

    const load = () => {
      const mine = ++seq
      void viewRef.current(card).then(
        (result) => { applyResult(mine, result) },
        (reason: unknown) => {
          const message = reason instanceof Error ? reason.message : String(reason)
          applyResult(mine, undefined, message)
        },
      )
    }

    load()
    const timer = setInterval(load, 2000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [card])

  const toggleExpanded = (key: ProgressStepKey) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const totalSteps = progress?.steps.length ?? 7
  const currentStepNumber = resolveCurrentStepNumber(progress)
  const headerTitle = progress
    ? `进度 · 第 ${currentStepNumber} 步 / 共 ${totalSteps} 步`
    : '进度 · 共 7 步'
  const headerBadge = resolveHeaderBadge(progress)

  return (
    <div className={css.panel} aria-label="任务进度">
      <div className={css.header}>
        <div className={css.headerTop}>
          <span className={css.headerTitle}>{headerTitle}</span>
          {headerBadge ? (
            <Tag tone={headerBadge.waiting ? 'warning' : 'outline'}>
              {headerBadge.label}
            </Tag>
          ) : null}
        </div>
      </div>

      {error ? <div className={css.errorBanner} role="status">{error}</div> : null}

      {progress === null ? (
        error ? null : (
          <div className={css.loading} role="status">
            <StateDot state="ongoing" size={14} />
            <span>正在读取任务进度…</span>
          </div>
        )
      ) : (
        <ol className={css.list}>
          {progress.steps.map((step) => {
            const isOpen = expandedKeys.has(step.key)
            const elapsedLabel = formatStepElapsed(step.elapsed)
            const stepClass = step.current
              ? `${css.step} ${css.stepCurrent}`
              : step.state === 'idle'
                ? `${css.step} ${css.stepIdle}`
                : css.step
            const metaParts = [
              step.state !== 'idle' || step.model || elapsedLabel ? step.actor : '',
              step.model,
              elapsedLabel,
            ].filter(Boolean)
            const metaLine = metaParts.join(' · ')
            return (
              <li
                key={step.key}
                className={stepClass}
                aria-current={step.current ? 'step' : undefined}
              >
                <span className={css.indicator}>
                  <StateDot
                    state={toDotState(step.state)}
                    appearance={step.state === 'active' ? 'dot' : 'step'}
                    size={16}
                  />
                </span>
                <div className={css.body}>
                  <div className={css.stepHead}>
                    <span className={css.stepName}>{step.label}</span>
                    {step.rawDetail ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        className={css.detailToggle}
                        aria-expanded={isOpen}
                        onClick={() => { toggleExpanded(step.key) }}
                      >
                        {isOpen ? '收起' : '详情'}
                        {isOpen
                          ? <IconChevronDownOutlineRegular size={12} aria-hidden />
                          : <IconChevronRightOutlineRegular size={12} aria-hidden />}
                      </Button>
                    ) : null}
                  </div>
                  {metaLine ? <div className={css.meta}>{metaLine}</div> : null}
                  {step.summary ? <div className={css.summary}>{step.summary}</div> : null}
                  {step.rawDetail && isOpen ? <div className={css.rawDetail}>{step.rawDetail}</div> : null}
                </div>
              </li>
            )
          })}
        </ol>
      )}
      {progress?.run ? <RunFooter run={progress.run} /> : null}
    </div>
  )
}

/** 第七批 7-3：右栏底“本次运行 · 改了 N 个文件 · 守住 N 条规矩”，点开列出是哪几项（对照 04 / 05）。 */
function RunFooter(props: { run: NonNullable<ParsedCardProgress['run']> }): ReactElement {
  const [open, setOpen] = useState(false)
  const kept = props.run.rules.filter(rule => rule.ok).length
  const parts = ['本次运行']
  if (props.run.changedFiles !== null) parts.push(`改了 ${props.run.changedFiles} 个文件`)
  if (props.run.rules.length > 0) parts.push(`守住 ${kept} 条规矩`)
  return (
    <div className={css.runFooter}>
      <button type="button" className={css.runToggle} aria-expanded={open} onClick={() => { setOpen(v => !v) }}>
        <span aria-hidden="true">{open ? '▾' : '▸'}</span>
        {parts.join(' · ')}
      </button>
      {open ? (
        <ul className={css.runRules}>
          {props.run.rules.map(rule => (
            <li key={rule.label} className={rule.ok ? css.runRule : `${css.runRule} ${css.runRuleBad}`}>
              <span><span aria-hidden="true">{rule.ok ? '✓ ' : '✗ '}</span><span>{rule.label}</span></span>
              {rule.detail ? <span className={css.runRuleDetail}>{rule.detail}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
