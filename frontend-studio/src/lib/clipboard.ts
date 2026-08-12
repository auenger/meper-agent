/**
 * Clipboard utility — 兼容 HTTPS（clipboard API）和 HTTP（execCommand 兜底）。
 *
 * 生产环境如果通过 HTTP 部署（非 HTTPS），navigator.clipboard 不可用，
 * 用 document.execCommand('copy') 作为降级方案。
 */

export function copyToClipboard(text: string): boolean {
  // HTTPS / localhost：使用现代 Clipboard API
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).catch(() => fallbackCopy(text))
    return true
  }
  // HTTP 降级
  return fallbackCopy(text)
}

function fallbackCopy(text: string): boolean {
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.style.cssText = 'position:fixed;left:-9999px;top:-9999px;opacity:0'
  document.body.appendChild(textarea)
  textarea.select()
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    // execCommand not available
  }
  document.body.removeChild(textarea)
  return ok
}
