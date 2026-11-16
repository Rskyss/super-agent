/**
 * 客户端草稿截图读取与校验（纯浏览器 Web API，零 Node / DSH 内部依赖）。
 * 仅接受 PNG 与 JPEG，单张不超过 10MiB，按原始字节转纯 base64，不压缩、不重编码、不上传文件名。
 */
import type { ImageMediaType, ImagePayload } from '../types.ts'

export const MAX_REVISION_IMAGE_BYTES = 10 * 1024 * 1024
export const MAX_REVISION_IMAGE_COUNT = 20

function isSupportedImageMime(mime: string): mime is ImageMediaType {
  return mime === 'image/png' || mime === 'image/jpeg'
}

function readFileBase64(file: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result !== 'string') {
        reject(new Error('读取截图数据失败，请重新选择截图'))
        return
      }
      const commaIdx = reader.result.indexOf(',')
      if (commaIdx === -1) {
        reject(new Error('读取截图编码失败，请重新选择截图'))
        return
      }
      const base64 = reader.result.slice(commaIdx + 1)
      if (!base64) {
        reject(new Error('截图内容为空，请重新选择截图'))
        return
      }
      resolve(base64)
    }
    reader.onerror = () => {
      reject(reader.error ?? new Error('读取截图失败，请重新选择截图'))
    }
    reader.readAsDataURL(file)
  })
}

/**
 * 将浏览器选中的草稿图片文件按原始字节转为纯 base64 载荷列表。
 * 遇到不支持的图片格式或超过 10MiB 的文件立即拒绝，绝不降级成只发文字。
 */
export async function encodeRevisionImages(files: readonly File[]): Promise<ImagePayload[]> {
  if (!Array.isArray(files) || files.length === 0) return []
  if (files.length > MAX_REVISION_IMAGE_COUNT) {
    throw new Error(`单次最多附带 ${MAX_REVISION_IMAGE_COUNT} 张截图`)
  }

  const payloads: ImagePayload[] = []
  for (const file of files) {
    if (!(file instanceof File)) {
      throw new Error('选中的截图文件无效，请重新选择')
    }
    const mime = typeof file.type === 'string' ? file.type.trim().toLowerCase() : ''
    if (!isSupportedImageMime(mime)) {
      throw new Error('仅支持 PNG 或 JPG 格式的截图图片，不能附带其他格式文件')
    }
    if (!Number.isFinite(file.size) || file.size <= 0) {
      throw new Error('截图文件内容为空，请重新选择')
    }
    if (file.size > MAX_REVISION_IMAGE_BYTES) {
      throw new Error('单张截图大小不能超过 10MB，请压缩或裁剪后再试')
    }
    const data = await readFileBase64(file)
    payloads.push({
      mediaType: mime,
      data,
    })
  }
  return payloads
}
