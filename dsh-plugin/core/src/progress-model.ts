/**
 * 将后台视图文本解析为界面与主机共用的七步进度模型，并统一替换旧称和内部技术词汇。
 * 纯数据计算模块，不依赖 React 或任何客户端界面模块。
 */
import type {
  CardViewPayload,
  ParsedCardProgress,
  ProgressStep,
  ProgressStepKey,
  ProgressStepState,
  ViewStation,
} from './types.ts'

export const STEP_LABELS: ReadonlyArray<{ readonly key: ProgressStepKey; readonly label: string; readonly defaultActor: string }> = [
  { key: 'disp', label: '调度员写方案', defaultActor: '调度员' },
  { key: 'approve', label: '你批方案', defaultActor: '你' },
  { key: 'test', label: '写测试', defaultActor: '' },
  { key: 'dev', label: '写代码', defaultActor: '' },
  { key: 'check', label: '跑全部测试', defaultActor: '程序' },
  { key: 'rev', label: '复查', defaultActor: '' },
  { key: 'accept', label: '你验收', defaultActor: '你' },
]

const STATION_KEY_ALIASES: Readonly<Record<string, string>> = {
  review: 'rev',
}

const PRE_START_PHASES = new Set(['planning', 'plan_ready', 'plan_waiting', 'plan_failed'])
const ACTIVE_EXECUTION_PHASES = new Set(['ready', 'running', 'merging'])
const TERMINAL_PHASES = new Set(['done', 'dropped'])
const STOPPED_DECISION_PHASES = new Set(['blocked', 'interrupted', 'start_failed', 'plan_failed', 'copy_missing'])
const STOPPED_PHASES = new Set([
  'done', 'dropped', 'interrupted', 'blocked',
  'plan_failed', 'start_failed', 'merge_failed', 'copy_missing',
  'plan_ready', 'plan_waiting', 'review',
])

/**
 * 清洗后台原文里的旧称与技术词，确保只出现产品经理能看懂的大白话。
 * @param text - 原始文本。
 * @returns 清洗后的文本。
 */
export function sanitizeProgressText(text: string): string {
  return text
    .replace(/需\s*Claude\s*介入/gi, '需调度员介入')
    .replace(/Claude\s*Code/gi, '调度员')
    .replace(/Claude/gi, '调度员')
    .replace(/Anthropic/gi, '后台')
    .replace(/\bJSON\b/gi, '数据')
    .replace(/\bAPI\b/gi, '接口')
}

/**
 * 将清洗后的文本整理为最多 3 行的大白话摘要。
 * @param text - 已经清洗过的文本。
 * @returns 最多 3 行的简短摘要。
 */
export function summarizePlainText(text: string): string {
  const clean = text.trim()
  if (!clean) return ''
  const lines = clean.split(/\r?\n/).map(line => line.trim()).filter(Boolean)
  const trimmed = lines.slice(0, 3).map(line => (line.length > 90 ? `${line.slice(0, 89)}…` : line))
  const joined = trimmed.join('\n')
  if (lines.length > 3 && !joined.endsWith('…')) return `${joined}…`
  return joined
}

/**
 * 格式化用时秒数，便于直观阅读。
 * @param seconds - 用时秒数；未开始或未记录时为 null。
 * @returns 格式化后的用时文本，空字符串表示不展示。
 */
export function formatStepElapsed(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return ''
  const whole = Math.floor(seconds)
  if (whole < 60) return `${whole} 秒`
  const minutes = Math.floor(whole / 60)
  const rest = whole % 60
  return rest === 0 ? `${minutes} 分` : `${minutes} 分 ${rest} 秒`
}

function mapStationState(
  station: ViewStation | undefined,
  phaseCode: string,
  isStopped: boolean,
  isTerminal: boolean,
  isRunningNow: boolean,
): ProgressStepState {
  const raw = station?.state ?? 'idle'
  if (raw === 'done') return 'done'
  if (raw === 'fail') return 'fail'
  if (raw === 'wait') return 'wait'
  if (raw === 'active') {
    if (phaseCode === 'done') return 'done'
    if (!isRunningNow || isTerminal || isStopped || station?.live === false) {
      return phaseCode === 'interrupted' ? 'wait' : 'idle'
    }
    return 'active'
  }
  return 'idle'
}

/**
 * 将后台视图文本解析为七步进度模型。
 * @param raw - 后台返回的结构化文本。
 * @param fallbackCardId - 兜底卡片编号。
 * @returns 七步进度视图模型。
 */
export function parseProgressView(raw: string, fallbackCardId: string): ParsedCardProgress {
  const payload = JSON.parse(raw) as CardViewPayload
  const card = payload.card ?? null
  const cardId = String(card?.id ?? fallbackCardId)
  const title = sanitizeProgressText(String(card?.title ?? ''))
  const stateText = sanitizeProgressText(String(card?.state ?? ''))
  const phaseCode = String(card?.phase?.code ?? '')
  const rawPhaseLabel = sanitizeProgressText(String(card?.phase?.label ?? stateText))
  const phaseLabel = STOPPED_DECISION_PHASES.has(phaseCode) ? '停下了' : rawPhaseLabel
  const elapsed = typeof payload.elapsed === 'number' && Number.isFinite(payload.elapsed) ? payload.elapsed : 0
  const workElapsed = typeof payload.workElapsed === 'number' && Number.isFinite(payload.workElapsed) && payload.workElapsed >= 0
    ? payload.workElapsed
    : null
  const isRunningNow = payload.isRunningNow === true
  const needsYou = card?.phase?.needs_you === true

  const stations = Array.isArray(payload.stations) ? payload.stations : []
  const byKey = new Map<string, ViewStation>()
  for (const station of stations) {
    const normalized = STATION_KEY_ALIASES[station.key] ?? station.key
    if (normalized === station.key || !byKey.has(normalized)) {
      byKey.set(normalized, station)
    }
  }
  const dispStation = byKey.get('disp')

  const isTerminal = TERMINAL_PHASES.has(phaseCode) || stateText === '已合并' || stateText === '已放弃'
  const isStopped = STOPPED_PHASES.has(phaseCode)
    || isTerminal
    || needsYou
    || stateText === '待验收'
    || stateText === '需调度员介入'

  let dispState: ProgressStepState
  let approveState: ProgressStepState
  if (phaseCode === 'planning') {
    dispState = isRunningNow ? 'active' : 'wait'
    approveState = 'idle'
  } else if (phaseCode === 'plan_waiting') {
    dispState = 'wait'
    approveState = 'idle'
  } else if (phaseCode === 'plan_ready') {
    dispState = 'done'
    approveState = 'wait'
  } else if (phaseCode === 'plan_failed') {
    dispState = 'fail'
    approveState = 'idle'
  } else if (!phaseCode && stateText === '待对齐') {
    dispState = dispStation?.state === 'active'
      ? (isRunningNow ? 'active' : 'wait')
      : dispStation?.state === 'fail'
        ? 'fail'
        : 'done'
    approveState = dispState === 'done' ? 'wait' : 'idle'
  } else {
    dispState = 'done'
    approveState = 'done'
  }

  const beforeStart = PRE_START_PHASES.has(phaseCode) || (!phaseCode && stateText === '待对齐')
  const phaseFailDetail = sanitizeProgressText(String(card?.phase?.detail ?? '')).trim()
  const planning = payload.planning
  const planningActor = sanitizeProgressText(String(planning?.actor ?? '')).trim()
  const planningModel = sanitizeProgressText(String(planning?.model ?? '')).trim()
  const planningDetail = sanitizeProgressText(String(planning?.detail ?? '')).trim()
  const planningElapsed = typeof planning?.elapsed === 'number' && Number.isFinite(planning.elapsed) && planning.elapsed >= 0
    ? planning.elapsed
    : null
  const isPlanningPhase = phaseCode === 'planning'
    || phaseCode === 'plan_waiting'
    || phaseCode === 'plan_failed'
    || phaseCode === 'plan_ready'
    || (!phaseCode && stateText === '待对齐')

  const rawSteps: Omit<ProgressStep, 'current'>[] = STEP_LABELS.map((spec) => {
    if (spec.key === 'disp') {
      // desktop/view.py 在离开规划阶段后会把 stations.disp 覆盖成当前调度汇总（如复查失败、已向你汇报），
      // 因此已完成/阻断阶段第 1 步只能使用专属 planning 历史来源，缺源省略，绝不拿后续调度状态填充。
      const stationDispDetail = isPlanningPhase ? sanitizeProgressText(String(dispStation?.detail ?? '')) : ''
      const preferredPlanDetail = (phaseCode === 'plan_waiting' || phaseCode === 'plan_failed')
        ? (stationDispDetail || planningDetail)
        : (planningDetail || stationDispDetail)
      const fallbackRaw = phaseCode === 'plan_waiting'
        ? '等待接入调度员后开始写方案'
        : phaseCode === 'plan_failed'
          ? (phaseFailDetail.startsWith('方案没写成')
              ? phaseFailDetail
              : phaseFailDetail ? `方案没写成：${phaseFailDetail}` : '方案没写成')
          : dispState === 'active'
            ? '正在写方案'
            : approveState === 'wait'
              ? '方案已写好'
              : ''
      const rawDetail = preferredPlanDetail || (isPlanningPhase ? fallbackRaw : '')
      const actor = planningActor
        || sanitizeProgressText(String(dispStation?.who ?? ''))
        || spec.defaultActor
      const model = planningModel || sanitizeProgressText(String(dispStation?.model ?? ''))
      const elapsed = planningElapsed ?? (typeof dispStation?.elapsed === 'number' ? dispStation.elapsed : null)
      return {
        key: 'disp',
        label: spec.label,
        state: dispState,
        actor,
        model,
        elapsed,
        summary: summarizePlainText(rawDetail),
        rawDetail,
      }
    }
    if (spec.key === 'approve') {
      // 后台 you 站位表示提需求，不能直接当作“你批方案”。
      const approvedAt = typeof payload.approval?.time === 'string' && /^\d{2}:\d{2}$/.test(payload.approval.time)
        ? payload.approval.time
        : ''
      if (approveState === 'done' && approvedAt) {
        // 对照 04 / 05：第二行“你 · 09:15 批准”，不再另起一行“已批准”。
        return {
          key: 'approve',
          label: spec.label,
          state: approveState,
          actor: spec.defaultActor,
          model: `${approvedAt} 批准`,
          elapsed: null,
          summary: '',
          rawDetail: `${approvedAt} 批准`,
        }
      }
      const rawDetail = approveState === 'wait'
        ? (sanitizeProgressText(String(card?.phase?.detail ?? '')) || '方案已写好，等你批准')
        : approveState === 'done'
          ? '已批准'
          : '等方案写好'
      return {
        key: 'approve',
        label: spec.label,
        state: approveState,
        actor: spec.defaultActor,
        model: '',
        elapsed: null,
        summary: summarizePlainText(rawDetail),
        rawDetail,
      }
    }

    const station = byKey.get(spec.key)
    let state: ProgressStepState = beforeStart
      ? 'idle'
      : mapStationState(station, phaseCode, isStopped, isTerminal, isRunningNow)
    if (spec.key === 'accept') {
      if (phaseCode === 'done' && state !== 'fail') state = 'done'
      else if (phaseCode === 'review' && state === 'idle') state = 'wait'
    }
    const rawDetail = sanitizeProgressText(String(station?.detail ?? ''))
    const label = spec.key === 'rev' && Array.isArray(payload.stopReviews) && payload.stopReviews.length > 0
      ? (phaseCode === 'blocked' && payload.stopReviews.length >= 3
          ? `复查 · ${payload.stopReviews.length} / 3 轮`
          : `复查 · ${payload.stopReviews.length} 轮`)
      : spec.label
    return {
      key: spec.key,
      label,
      state,
      actor: sanitizeProgressText(String(station?.who ?? spec.defaultActor)),
      model: sanitizeProgressText(String(station?.model ?? '')),
      elapsed: typeof station?.elapsed === 'number' ? station.elapsed : null,
      summary: summarizePlainText(rawDetail),
      rawDetail,
    }
  })

  // 后台真正在执行（isRunningNow=true）但各站位处于两步交接空隙（尚无 active/wait）时，把第一个未完成步骤标为准备中，
  // 避免界面看似无事可做；未确认在运行、暂停或已停下/已结束阶段绝不补转圈。
  const isExecuting = isRunningNow && (
    ACTIVE_EXECUTION_PHASES.has(phaseCode)
    || (!phaseCode && (stateText === '开发中' || stateText === '待测试' || stateText === '测试中' || stateText === '待派发'))
  )
  if (isExecuting && !isStopped && !isTerminal && !rawSteps.some(step => step.state === 'active' || step.state === 'wait')) {
    const nextIdx = rawSteps.findIndex(step => step.state !== 'done' && !(step.key === 'rev' && step.actor === '不复查'))
    const target = nextIdx >= 0 ? rawSteps[nextIdx] : undefined
    if (target !== undefined) {
      const preparingRaw = !target.rawDetail || target.rawDetail.startsWith('等')
        ? '准备中…'
        : `准备中：${target.rawDetail}`
      rawSteps[nextIdx] = {
        ...target,
        state: 'active',
        summary: summarizePlainText(preparingRaw),
        rawDetail: preparingRaw,
      }
    }
  }

  const activeKey = rawSteps.find(step => step.state === 'active')?.key
  const waitKey = activeKey === undefined ? rawSteps.find(step => step.state === 'wait')?.key : undefined
  const currentKey = activeKey ?? waitKey

  return {
    cardId,
    title,
    stateText,
    phaseLabel,
    phaseCode,
    elapsed,
    workElapsed,
    isRunningNow,
    steps: rawSteps.map(step => ({ ...step, current: step.key === currentKey })),
    ...(payload.run && Array.isArray(payload.run.rules) ? { run: payload.run } : {}),
  }
}
