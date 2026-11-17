/**
 * 第 15 项纯计算逻辑：验收标准拆分、结构化校验、摘要生成与模型输出严格解析。
 * 零 Node / DSH 运行时依赖，供主机网关与任务工具共用。
 */
import { sanitizeProgressText } from './progress-model.ts'
import type { CriteriaChangeItem } from './types.ts'

export const REVISION_TIMEOUT_MS = 60_000
export const MAX_REVISION_OUTPUT_CHARS = 16_384
export const MAX_CRITERIA_CHANGES = 50
export const MAX_CRITERION_LEN = 500

const LIST_PREFIX_RE = /^(?:[-*•]\s+|\d+[.、)\]]\s+)/
const CIRCLE_NUMS_RE = /[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]/g
const GENERATED_ATTACHMENT_MD_RE = /^!\[截图\]\(附件\/\d[\d_-]*\.(?:png|jpg)\)$/

/** 识别后端在“追加要求”段落自动写入的时间命名截图 Markdown 行，避免在验收卡当成普通文字暴露路径。 */
export function isGeneratedAttachmentMarkdownLine(line: string): boolean {
  return GENERATED_ATTACHMENT_MD_RE.test(line.trim())
}

export type CriteriaValidationResult =
  | { readonly ok: true; readonly base: string[]; readonly changes: CriteriaChangeItem[] }
  | { readonly ok: false; readonly message: string }

export type RevisionModelParseResult =
  | { readonly ok: true; readonly changes: CriteriaChangeItem[] }
  | { readonly ok: false; readonly message: string }

function hasControlOrNewline(text: string): boolean {
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i)
    if (code < 32 || code === 0x7f || code === 0x85 || code === 0x2028 || code === 0x2029) {
      return true
    }
  }
  return false
}

function cleanCriterionText(raw: unknown, label: string): { ok: true; text: string } | { ok: false; message: string } {
  if (typeof raw !== 'string') {
    return { ok: false, message: `${label}必须是文字` }
  }
  if (hasControlOrNewline(raw)) {
    return { ok: false, message: `${label}只能写单行文字，不能包含换行或控制字符` }
  }
  const cleaned = raw.trim()
  if (!cleaned) {
    return { ok: false, message: `${label}不能为空` }
  }
  if (cleaned.startsWith('#') || cleaned.includes('##')) {
    return { ok: false, message: `${label}不能包含标题标记` }
  }
  if (cleaned.length > MAX_CRITERION_LEN) {
    return { ok: false, message: `${label}不能超过 ${MAX_CRITERION_LEN} 个字` }
  }
  return { ok: true, text: cleaned }
}

/** 从需求卡的验收标准正文拆出逐条列表（与后台 collab/tasks.py 保持完全一致）。 */
export function splitAcceptanceCriteria(body: string): string[] {
  const rawLines = body.split(/\r?\n/).map(line => line.trim()).filter(Boolean)
  const chunks = rawLines.length > 0 && rawLines.every(line => LIST_PREFIX_RE.test(line))
    ? rawLines
    : body.replace(CIRCLE_NUMS_RE, '\n').split(/[\r\n;；]+/)

  const items: string[] = []
  for (const raw of chunks) {
    const cleaned = raw.trim().replace(LIST_PREFIX_RE, '').trim()
    if (!cleaned || cleaned === '（待填）') continue
    if (!items.includes(cleaned)) items.push(cleaned)
  }
  return items
}

/** 校验结构化修改基线与逐条改动，拒绝越界、布尔序号、多行注入与不支持的操作。 */
export function validateCriteriaChanges(
  criteriaBase: unknown,
  criteriaChanges: unknown,
): CriteriaValidationResult {
  if (!Array.isArray(criteriaBase)) {
    return { ok: false, message: '缺少有效的旧验收标准基线' }
  }
  const base: string[] = []
  for (const item of criteriaBase) {
    if (typeof item !== 'string') {
      return { ok: false, message: '旧验收标准基线格式不对' }
    }
    const trimmed = item.trim()
    if (!trimmed) {
      return { ok: false, message: '旧验收标准基线包含空白条目' }
    }
    base.push(trimmed)
  }

  if (!Array.isArray(criteriaChanges)) {
    return { ok: false, message: '验收标准修改明细格式不对' }
  }
  if (criteriaChanges.length === 0) {
    return { ok: true, base, changes: [] }
  }
  if (criteriaChanges.length > MAX_CRITERIA_CHANGES) {
    return { ok: false, message: `单次最多修改 ${MAX_CRITERIA_CHANGES} 条验收标准` }
  }

  const changes: CriteriaChangeItem[] = []
  for (let i = 0; i < criteriaChanges.length; i += 1) {
    const rawItem = criteriaChanges[i]
    if (!rawItem || typeof rawItem !== 'object' || Array.isArray(rawItem)) {
      return { ok: false, message: `第 ${i + 1} 项修改格式不对` }
    }
    const obj = rawItem as Record<string, unknown>
    const keys = Object.keys(obj)
    if (keys.length === 2 && 'replaceNth' in obj && 'text' in obj) {
      const nth = obj.replaceNth
      if (typeof nth !== 'number' || !Number.isInteger(nth)) {
        return { ok: false, message: '修改的验收标准序号必须是整数' }
      }
      if (nth < 1 || nth > base.length) {
        return { ok: false, message: `第 ${nth} 条验收标准不存在` }
      }
      const checked = cleanCriterionText(obj.text, `第 ${nth} 条验收标准`)
      if (!checked.ok) return checked
      changes.push({ replaceNth: nth, text: checked.text })
    } else if (keys.length === 1 && 'add' in obj) {
      const checked = cleanCriterionText(obj.add, '新增验收标准')
      if (!checked.ok) return checked
      changes.push({ add: checked.text })
    } else {
      return { ok: false, message: '模型返回了不支持的验收标准修改操作' }
    }
  }

  return { ok: true, base, changes }
}

/** 根据实际旧条目与合法修改生成确认条摘要（不信任模型自述，窄屏逐行可读）。 */
export function buildCriteriaSummary(
  criteriaBase: readonly string[],
  criteriaChanges: readonly CriteriaChangeItem[],
): string {
  const lines: string[] = []
  let nextIndex = criteriaBase.length + 1
  for (const change of criteriaChanges) {
    if ('replaceNth' in change) {
      const oldText = sanitizeProgressText(criteriaBase[change.replaceNth - 1] ?? '')
      const newText = sanitizeProgressText(change.text)
      lines.push(`第${change.replaceNth}条：${oldText} → ${newText}`)
    } else {
      const addText = sanitizeProgressText(change.add)
      lines.push(`新增第${nextIndex}条：${addText}`)
      nextIndex += 1
    }
  }
  return lines.join('\n')
}

/** 改一处同步另一处：与 desktop/clerk/revision.py 的 build_revision_prompt 保持逐字一致。 */
export function buildRevisionPrompt(
  criteria: readonly string[],
  userText: string,
  attachmentCount: number = 0,
): { readonly system: string; readonly user: string } {
  const count = Number.isFinite(attachmentCount) && attachmentCount > 0 ? Math.floor(attachmentCount) : 0
  const system = [
    '你负责把用户对任务卡的修改意见，拆成对“当前验收标准”的逐条结构化修改。',
    '你只能输出一个严格 JSON 对象，格式固定为：{"changes":[...]}。不要输出任何解释、寒暄或多余文字。',
    'changes 数组里只允许以下两种对象：',
    '1. 替换现有第 N 条：{"replaceNth": 整数(从1开始), "text": "修改后的整条单行验收标准"}',
    '2. 新增一条：{"add": "新增的单行验收标准"}',
    '规则：',
    '- 严禁发明删除、合并、排序、改标题等其他字段或操作；每条 text/add 必须是完整单行文字，不能包含换行符或 # 符号。',
    '- 若修改意见能明确对应到修改第几条或新增什么标准，就拆出来；若只是描述现象、反馈问题或不够明确，无法确定怎么改验收标准，必须原样返回 {"changes":[]}。',
    // 只在这次真的附了截图时才讲截图规矩（复查第 1 轮建议：免得纯文字修改引用旧截图时被误标“本次附图”）
    ...(count > 0
      ? ['- 用户这次附了截图，你看不到图：不要编造或猜测图里的内容。用户要求按截图改第 N 条时，把那条改写成保留原意、去掉被截图推翻的部分，并在末尾注明“（以本次附的截图为准）”。']
      : []),
  ].join('\n')

  const criteriaBlock = criteria.length > 0
    ? criteria.map((item, idx) => `${idx + 1}. ${item}`).join('\n')
    : '（当前暂无验收标准）'

  const user = [
    '当前验收标准：',
    criteriaBlock,
    '',
    '用户原话：',
    userText.trim(),
    ...(count > 0 ? ['', `用户这次附了 ${count} 张截图（你看不到图，保存后写代码的人会看）。`] : []),
  ].join('\n')

  return { system, user }
}

function unwrapSingleJsonFence(raw: string): string | null {
  const trimmed = raw.trim()
  if (trimmed.startsWith('{') && trimmed.endsWith('}')) return trimmed
  const fenceMatch = /^```(?:json)?\s*\r?\n([\s\S]*?)\r?\n```\s*$/i.exec(trimmed)
  if (!fenceMatch) return null
  const inner = (fenceMatch[1] ?? '').trim()
  return inner.startsWith('{') && inner.endsWith('}') ? inner : null
}

/** 严格解析并校验模型返回的 JSON；坏 JSON 或不受支持的操作一律失败。 */
export function parseRevisionModelOutput(
  rawText: string,
  criteriaBase: readonly string[],
): RevisionModelParseResult {
  const jsonText = unwrapSingleJsonFence(rawText)
  if (!jsonText) {
    return { ok: false, message: '拆分修改要求失败：模型没有返回有效格式，请重试' }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(jsonText) as unknown
  } catch {
    return { ok: false, message: '拆分修改要求失败：模型返回的 JSON 格式有误，请重试' }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { ok: false, message: '拆分修改要求失败：模型返回结构不对，请重试' }
  }
  const record = parsed as Record<string, unknown>
  const keys = Object.keys(record)
  if (keys.length !== 1 || !Array.isArray(record.changes)) {
    return { ok: false, message: '拆分修改要求失败：缺少或包含不支持的修改字段，请重试' }
  }
  const validated = validateCriteriaChanges(criteriaBase, record.changes)
  if (!validated.ok) {
    return { ok: false, message: `拆分修改要求失败：${validated.message}` }
  }
  return { ok: true, changes: validated.changes }
}

/** 从流转记录原文判定是否已做过结构化逐条验收标准更新（重启后仍可从持久化流转识别）。 */
export function hasCriteriaRevisionInFlow(flowText: string | null | undefined): boolean {
  if (!flowText) return false
  for (const rawLine of flowText.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line) continue
    try {
      const entry = JSON.parse(line) as { kind?: unknown; criteria_changes?: unknown }
      if (entry?.kind === '追加要求' && Array.isArray(entry.criteria_changes) && entry.criteria_changes.length > 0) {
        return true
      }
    } catch {
      // 跳过损坏行，不影响其余记录识别。
    }
  }
  return false
}
