/**
 * 客户端兼容入口：从共享纯计算模块 re-export 七步进度解析与文案工具。
 */
export {
  formatStepElapsed,
  parseProgressView,
  sanitizeProgressText,
  summarizePlainText,
} from '../progress-model.ts'
