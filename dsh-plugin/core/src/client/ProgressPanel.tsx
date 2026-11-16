/**
 * 右栏七步进度面板：每 2 秒读取真实进度，按固定七步展示工具、模型、用时与大白话说明。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { Button, StateDot, Tag, type StateDotState } from '@deepseek-ai/dsh-client-ui-primitives'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { ParsedCardProgress, ProgressStepKey, ProgressStepState } from '../types.ts'
import { formatWorkDuration } from '../work-duration.ts'
import { formatStepElapsed, parseProgressView, sanitizeProgressText } from './progress-view.ts'
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

  return (
    <div className={css.panel} aria-label={`任务进度 ${card}`}>
      <div className={css.header}>
        <div className={css.headerTop}>
          <span className={css.cardId}>{progress?.cardId ?? card}</span>
          {progress?.phaseLabel ? <Tag>{progress.phaseLabel}</Tag> : null}
        </div>
        {progress?.title ? <div className={css.cardTitle}>{progress.title}</div> : null}
        <div className={css.phaseRow}>
          <span>每 2 秒自动刷新</span>
          {progress ? <span>干活用时 {formatWorkDuration(progress.workElapsed)}</span> : null}
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
            const stepClass = step.current ? `${css.step} ${css.stepCurrent}` : css.step
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
                    {step.actor ? <Tag>{step.actor}</Tag> : null}
                    {step.model ? <span className={css.meta}>{step.model}</span> : null}
                    {elapsedLabel ? <span className={css.meta}>{elapsedLabel}</span> : null}
                  </div>
                  {step.summary ? <div className={css.summary}>{step.summary}</div> : null}
                  {step.rawDetail ? (
                    <>
                      <Button
                        size="sm"
                        variant="ghost"
                        className={css.detailToggle}
                        aria-expanded={isOpen}
                        onClick={() => { toggleExpanded(step.key) }}
                      >
                        {isOpen ? '收起详情' : '查看详情'}
                      </Button>
                      {isOpen ? <div className={css.rawDetail}>{step.rawDetail}</div> : null}
                    </>
                  ) : null}
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
