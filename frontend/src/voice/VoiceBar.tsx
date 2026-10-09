import { useEffect, useRef } from 'react'
import { App, Button, Popover, Space } from 'antd'
import { SoundOutlined } from '@ant-design/icons'
import MicButton from './MicButton'
import ReadAloud from './ReadAloud'

/**
 * Phase 23e — the top bar's voice controls (ruling Q23-11, both halves):
 *   🎤 dictate into the text box you were last in (any page, any field);
 *   🔊 read aloud the text you have selected, or else the page's heading and
 *      the paragraph under it.
 */
type Editable = HTMLInputElement | HTMLTextAreaElement

function isEditable(el: Element | null): el is Editable {
  if (!el) return false
  if (el instanceof HTMLTextAreaElement) return !el.readOnly && !el.disabled
  return el instanceof HTMLInputElement && !el.readOnly && !el.disabled
    && ['text', 'search', ''].includes(el.type)
}

/** Insert at the caret the way typing would — React sees an input event. */
export function insertText(el: Editable, text: string) {
  const start = el.selectionStart ?? el.value.length
  const end = el.selectionEnd ?? el.value.length
  const before = el.value.slice(0, start)
  const glue = before && !/\s$/.test(before) ? ' ' : ''
  const next = `${before}${glue}${text}${el.value.slice(end)}`
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value')?.set?.call(el, next)
  el.dispatchEvent(new Event('input', { bubbles: true }))
  const caret = before.length + glue.length + text.length
  el.focus()
  el.setSelectionRange?.(caret, caret)
}

function pageText(): string {
  const sel = window.getSelection()?.toString().trim()
  if (sel) return sel
  const main = document.querySelector('main') ?? document.body
  const h = main.querySelector('h1, h2, h3')
  const p = h?.parentElement?.querySelector('.ant-typography-secondary, p')
  return [h?.textContent, p?.textContent].filter(Boolean).join('. ')
}

export default function VoiceBar() {
  const { message } = App.useApp()
  const last = useRef<Editable | null>(null)
  useEffect(() => {
    const onFocus = (e: FocusEvent) => { if (isEditable(e.target as Element)) last.current = e.target as Editable }
    document.addEventListener('focusin', onFocus)
    return () => document.removeEventListener('focusin', onFocus)
  }, [])
  const controls = (
    <Space size={2}>
      <MicButton size="small" testId="dictate" onText={(t) => {
        const el = last.current
        if (el && document.contains(el)) insertText(el, t)
        else message.info(`Click into a text box first — heard: “${t}”`)
      }} />
      <ReadAloud text={pageText} testId="read-page" />
    </Space>
  )
  // ≥ 1441 px: inline. Narrower (a laptop), ONE button that opens both — the
  // HOD's top bar must still fit 1280 px (Phase 22f, pinned by the Practice spec)
  return (
    <span data-testid="voice-bar">
      <span className="gi-voice-inline">{controls}</span>
      <span className="gi-voice-compact">
        <Popover trigger="click" content={controls} placement="bottomRight">
          <Button type="text" size="small" icon={<SoundOutlined />} aria-label="Voice: dictate or read aloud"
            data-testid="voice-menu" />
        </Popover>
      </span>
    </span>
  )
}
