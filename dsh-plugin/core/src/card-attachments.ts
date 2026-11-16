/**
 * Host 侧卡片截图附件校验与安全读取（仅在 Host Node 环境引用，禁止浏览器端导入）。
 */
import { constants, existsSync, lstatSync, realpathSync, statSync } from 'node:fs'
import { lstat, open, realpath, stat } from 'node:fs/promises'
import { join } from 'node:path'
import type { ImageMediaType, ImagePayload, TrajectoryAttachment } from './types.ts'

export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
export const MAX_ATTACHMENT_COUNT = 20

const MAX_BASE64_CHARS = Math.ceil(MAX_ATTACHMENT_BYTES / 3) * 4 + 4
const SAFE_FILENAME_RE = /^\d[\d_-]*\.(png|jpg)$/
const BASE64_RE = /^[A-Za-z0-9+/]+={0,2}$/
const SAFE_CARD_ID_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,63}$/
const ATTACHMENT_DIR_NAME = '附件'
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
const JPEG_MAGIC = Buffer.from([0xff, 0xd8, 0xff])

function hasImageMagic(mediaType: ImageMediaType, bytes: Buffer): boolean {
  if (mediaType === 'image/png') {
    return bytes.length >= PNG_MAGIC.length && bytes.subarray(0, PNG_MAGIC.length).equals(PNG_MAGIC)
  }
  return bytes.length >= JPEG_MAGIC.length && bytes.subarray(0, JPEG_MAGIC.length).equals(JPEG_MAGIC)
}

/**
 * 校验前端提交的图片数组：数量、格式白名单、纯 base64、10MiB 上限及 PNG/JPEG 文件头签名。
 * 校验通过返回剔除冗余字段的新数组；任何异常均抛出可读中文错误。
 */
export function validateImagePayloads(attachments: unknown): ImagePayload[] {
  if (attachments === undefined) return []
  if (!Array.isArray(attachments)) {
    throw new Error('图片附件格式不对')
  }
  if (attachments.length === 0) return []
  if (attachments.length > MAX_ATTACHMENT_COUNT) {
    throw new Error(`单次最多附带 ${MAX_ATTACHMENT_COUNT} 张截图`)
  }

  const normalized: ImagePayload[] = []
  for (let i = 0; i < attachments.length; i += 1) {
    const idx = i + 1
    const item = attachments[i]
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      throw new Error(`第 ${idx} 张图片格式不对`)
    }
    const record = item as Record<string, unknown>
    const mediaType = record.mediaType
    if (mediaType !== 'image/png' && mediaType !== 'image/jpeg') {
      throw new Error(`第 ${idx} 张图片只支持 PNG 或 JPG 格式`)
    }
    const data = record.data
    if (typeof data !== 'string' || !data) {
      throw new Error(`第 ${idx} 张图片内容为空`)
    }
    if (data.length > MAX_BASE64_CHARS) {
      throw new Error(`第 ${idx} 张图片不能超过 10MB`)
    }
    if (data.length % 4 !== 0 || !BASE64_RE.test(data)) {
      throw new Error(`第 ${idx} 张图片编码无效`)
    }
    const decoded = Buffer.from(data, 'base64')
    if (decoded.length === 0) {
      throw new Error(`第 ${idx} 张图片内容为空`)
    }
    if (decoded.length > MAX_ATTACHMENT_BYTES) {
      throw new Error(`第 ${idx} 张图片不能超过 10MB`)
    }
    if (!hasImageMagic(mediaType, decoded)) {
      throw new Error(`第 ${idx} 张图片内容与格式不匹配`)
    }
    normalized.push({ mediaType, data })
  }
  return normalized
}

/**
 * 从流转记录的 attachments 相对路径数组中提取安全的截图元数据。
 * 只认当前卡片目录真实附件文件夹下的时间命名 PNG/JPG 常规文件，拒绝软链接、目录逃逸与外部路径。
 */
export function extractTrajectoryAttachments(realCardDir: string, rawAttachments: unknown): TrajectoryAttachment[] {
  if (!Array.isArray(rawAttachments) || rawAttachments.length === 0) return []

  const attDir = join(realCardDir, ATTACHMENT_DIR_NAME)
  if (!existsSync(attDir)) return []
  let realAttDir: string
  try {
    const attLinkStat = lstatSync(attDir)
    if (attLinkStat.isSymbolicLink() || !attLinkStat.isDirectory()) return []
    realAttDir = realpathSync(attDir)
    if (realAttDir !== join(realCardDir, ATTACHMENT_DIR_NAME)) return []
  } catch {
    return []
  }

  const prefix = `${ATTACHMENT_DIR_NAME}/`
  const seen = new Set<string>()
  const items: TrajectoryAttachment[] = []

  for (const entry of rawAttachments) {
    if (typeof entry !== 'string' || entry.includes('\0') || entry.includes('\\')) continue
    if (!entry.startsWith(prefix)) continue
    const name = entry.slice(prefix.length)
    if (!SAFE_FILENAME_RE.test(name) || seen.has(name)) continue

    const target = join(realAttDir, name)
    if (!existsSync(target)) continue
    try {
      const linkStat = lstatSync(target)
      if (linkStat.isSymbolicLink() || !linkStat.isFile()) continue
      const realTarget = realpathSync(target)
      if (realTarget !== target) continue
      const fileStat = statSync(realTarget)
      if (!fileStat.isFile() || fileStat.size <= 0 || fileStat.size > MAX_ATTACHMENT_BYTES) continue
    } catch {
      continue
    }

    seen.add(name)
    items.push({
      name,
      mediaType: name.endsWith('.png') ? 'image/png' : 'image/jpeg',
    })
  }

  return items
}

/**
 * 安全读取当前配置项目下指定任务卡附件目录内的单张原始截图。
 * 逐级校验 `.collab/cards/<card>/附件/<name>` 无任何软链接或路径逃逸，
 * 且文件名、大小与字节签名均一致。任何失败仅抛出无路径泄露的中文错误。
 */
export async function readCardAttachmentOnHost(
  project: string,
  card: string,
  name: unknown,
): Promise<ImagePayload> {
  const cleanProject = typeof project === 'string' ? project.trim() : ''
  if (!cleanProject) {
    throw new Error('当前未配置项目目录，无法读取截图')
  }
  const cleanCard = typeof card === 'string' ? card.trim() : ''
  if (!SAFE_CARD_ID_RE.test(cleanCard)) {
    throw new Error('卡片编号不对')
  }
  if (typeof name !== 'string') {
    throw new Error('图片文件名格式不对')
  }
  const trimmedName = name.trim()
  if (
    !trimmedName
    || trimmedName.includes('\0')
    || trimmedName.includes('/')
    || trimmedName.includes('\\')
    || trimmedName.includes('..')
    || trimmedName.includes(':')
    || !SAFE_FILENAME_RE.test(trimmedName)
  ) {
    throw new Error('图片文件名格式不对')
  }

  let currentRealDir: string
  try {
    currentRealDir = await realpath(cleanProject)
    if (!(await stat(currentRealDir)).isDirectory()) {
      throw new Error('项目目录不可用')
    }
  } catch {
    throw new Error('项目目录不可用，无法读取截图')
  }

  for (const segment of ['.collab', 'cards', cleanCard, ATTACHMENT_DIR_NAME]) {
    const candidate = join(currentRealDir, segment)
    let linkStat: Awaited<ReturnType<typeof lstat>>
    try {
      linkStat = await lstat(candidate)
    } catch {
      throw new Error('图片文件不存在或不可读')
    }
    if (linkStat.isSymbolicLink() || !linkStat.isDirectory()) {
      throw new Error('图片路径不安全，已拒绝读取')
    }
    let resolvedStep: string
    try {
      resolvedStep = await realpath(candidate)
    } catch {
      throw new Error('图片文件不存在或不可读')
    }
    if (resolvedStep !== candidate) {
      throw new Error('图片路径不安全，已拒绝读取')
    }
    currentRealDir = resolvedStep
  }

  const filePath = join(currentRealDir, trimmedName)
  let fileLinkStat: Awaited<ReturnType<typeof lstat>>
  try {
    fileLinkStat = await lstat(filePath)
  } catch {
    throw new Error('图片文件不存在或不可读')
  }
  if (fileLinkStat.isSymbolicLink() || !fileLinkStat.isFile()) {
    throw new Error('图片路径不安全，已拒绝读取')
  }
  let resolvedFile: string
  try {
    resolvedFile = await realpath(filePath)
  } catch {
    throw new Error('图片文件不存在或不可读')
  }
  if (resolvedFile !== filePath) {
    throw new Error('图片路径不安全，已拒绝读取')
  }

  const openFlags = constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)
  let handle: Awaited<ReturnType<typeof open>> | undefined
  try {
    handle = await open(resolvedFile, openFlags)
    const fdStat = await handle.stat()
    if (!fdStat.isFile()) {
      throw new Error('图片路径不是有效文件')
    }
    if (fdStat.size <= 0) {
      throw new Error('图片文件为空')
    }
    if (fdStat.size > MAX_ATTACHMENT_BYTES) {
      throw new Error('图片文件大小不能超过 10MB')
    }

    const buffer = Buffer.allocUnsafe(fdStat.size)
    let offset = 0
    while (offset < fdStat.size) {
      const { bytesRead } = await handle.read(buffer, offset, fdStat.size - offset, offset)
      if (bytesRead <= 0) break
      offset += bytesRead
    }
    const content = offset === buffer.length ? buffer : buffer.subarray(0, offset)
    if (content.length === 0) {
      throw new Error('图片文件为空')
    }
    const mediaType: ImageMediaType = trimmedName.endsWith('.png') ? 'image/png' : 'image/jpeg'
    if (!hasImageMagic(mediaType, content)) {
      throw new Error('图片内容与格式不匹配')
    }
    return {
      mediaType,
      data: content.toString('base64'),
    }
  } catch (error: unknown) {
    if (
      error instanceof Error
      && (
        error.message === '图片路径不是有效文件'
        || error.message === '图片文件为空'
        || error.message === '图片文件大小不能超过 10MB'
        || error.message === '图片内容与格式不匹配'
      )
    ) {
      throw error
    }
    throw new Error('图片文件不存在或不可读')
  } finally {
    await handle?.close().catch(() => {})
  }
}
