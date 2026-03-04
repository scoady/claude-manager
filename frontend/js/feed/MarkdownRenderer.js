/** MarkdownRenderer — safe markdown → HTML via marked + DOMPurify, with fallback. */

const ALLOWED_TAGS = [
  'h1', 'h2', 'h3', 'h4', 'p', 'ul', 'ol', 'li', 'strong', 'em', 'code', 'pre',
  'blockquote', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'a', 'hr', 'br',
  'span', 'div', 'img', 'del', 'sup', 'sub',
];

const ALLOWED_ATTR = ['href', 'title', 'alt', 'src', 'class', 'target', 'rel'];

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Render markdown string to sanitized HTML.
 * Falls back to escaped text in <pre> if libraries aren't loaded.
 */
export function renderMarkdown(markdown) {
  if (!markdown || typeof markdown !== 'string') return '';

  // Check if marked + DOMPurify are available (loaded from CDN)
  if (typeof window.marked !== 'undefined' && typeof window.DOMPurify !== 'undefined') {
    try {
      const rawHtml = window.marked.parse(markdown, { breaks: true, gfm: true });
      return window.DOMPurify.sanitize(rawHtml, {
        ALLOWED_TAGS,
        ALLOWED_ATTR,
        ADD_ATTR: ['target'],
      });
    } catch (e) {
      console.warn('MarkdownRenderer: parse error, falling back', e);
    }
  }

  // Fallback: escaped text in <pre>
  return `<pre class="agent-stream-pre">${escapeHtml(markdown)}</pre>`;
}
