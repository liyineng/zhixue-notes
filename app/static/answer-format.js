// A small, HTML-escaping renderer for the answer formats used by the app.
const escape = (text) => String(text).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

const inlineTokens = /(`+[^`]*`+|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\$\$[\s\S]*?\$\$|\$[^$\n]+\$)/g;
const tokenAtStart = new RegExp(`^(?:${inlineTokens.source})`);

function inline(text, references) {
  return text.split(inlineTokens).map((part) => {
    const code = part.match(/^(`+)([\s\S]*?)\1$/);
    if (code) return `<code>${escape(code[2])}</code>`;
    if (/^(?:\\\(|\\\[|\$)/.test(part)) return escape(part);
    return escape(part)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(?!\*)([^*]+)\*/g, '<em>$1</em>')
      .replace(/【(\d+)】/g, (whole, number) => references.has(Number(number))
        ? `[${number}]` : `<span class="unavailable-reference" title="这条历史回答没有保存此编号的来源，无法核对原文">【${number} · 来源缺失】</span>`)
      .replace(/\[(\d+)\]/g, (whole, number) => references.has(Number(number))
        ? `<button class="citation-ref" type="button" data-citation="${number}">[${number}]</button>` : whole);
  }).join('');
}

function cells(line) {
  let text = line.trim();
  if (text.startsWith('|')) text = text.slice(1);
  if (text.endsWith('|') && !text.endsWith('\\|')) text = text.slice(0, -1);
  const result = [];
  let cell = '', code = false;
  for (let i = 0; i < text.length; i++) {
    const token = text.slice(i).match(tokenAtStart);
    if (token) { cell += token[0]; i += token[0].length - 1; continue; }
    if (text[i] === '\\' && text[i + 1] === '|') { cell += '|'; i++; continue; }
    if (text[i] === '`') code = !code;
    if (text[i] === '|' && !code) { result.push(cell.trim()); cell = ''; }
    else cell += text[i];
  }
  result.push(cell.trim());
  return result;
}

export function renderAnswerHtml(text, citations = []) {
  const references = new Set(citations.map((c) => Number(c.index)));
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  const render = (line) => inline(line, references);
  const tableAt = (i) => i + 1 < lines.length && lines[i].includes('|') &&
    cells(lines[i + 1]).length === cells(lines[i]).length &&
    cells(lines[i + 1]).every((cell) => /^:?-{3,}:?$/.test(cell));
  for (let i = 0; i < lines.length;) {
    const line = lines[i].trim();
    if (!line) { i++; continue; }
    if (line.startsWith('\\[') || line.startsWith('$$')) {
      const delimiter = line.startsWith('\\[') ? '\\]' : '$$';
      const math = [lines[i++]];
      while (math.join('\n').indexOf(delimiter, 2) < 0) {
        if (i >= lines.length) break;
        math.push(lines[i++]);
      }
      const block = math.join('\n');
      const closing = block.indexOf(delimiter, 2);
      const end = closing < 0 ? block.length : closing + delimiter.length;
      out.push(`<div class="answer-math">${escape(block.slice(0, end))}${render(block.slice(end))}</div>`);
      continue;
    }
    const fence = line.match(/^(`{3,}|~{3,})/);
    if (fence) {
      const code = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) code.push(lines[i++]);
      i++;
      out.push(`<pre><code>${escape(code.join('\n'))}</code></pre>`);
      continue;
    }
    if (tableAt(i)) {
      const headers = cells(lines[i]);
      const align = cells(lines[i + 1]).map((cell) => cell.endsWith(':') ? (cell.startsWith(':') ? 'center' : 'right') : 'left');
      const row = (values, tag) => `<tr>${headers.map((_, j) => `<${tag} style="text-align:${align[j]}">${render(values[j] || '')}</${tag}>`).join('')}</tr>`;
      out.push(`<div class="answer-table-wrap" role="region" aria-label="回答表格" tabindex="0"><table><thead>${row(headers, 'th')}</thead><tbody>`);
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes('|')) out.push(row(cells(lines[i++]), 'td'));
      out.push('</tbody></table></div>');
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*$/);
    if (heading) {
      const level = Math.min(6, heading[1].length + 1);
      out.push(`<h${level}>${render(heading[2])}</h${level}>`); i++; continue;
    }
    if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(line)) { out.push('<hr>'); i++; continue; }
    const list = line.match(/^(?:([-*+])|(\d+)[.)])\s+(.+)$/);
    if (list) {
      const tag = list[2] ? 'ol' : 'ul';
      out.push(`<${tag}${list[2] ? ` start="${Number(list[2])}"` : ''}>`);
      while (i < lines.length) {
        const item = lines[i].trim().match(/^(?:([-*+])|(\d+)[.)])\s+(.+)$/);
        if (!item || Boolean(item[2]) !== Boolean(list[2])) break;
        out.push(`<li>${render(item[3])}</li>`); i++;
      }
      out.push(`</${tag}>`); continue;
    }
    if (line.startsWith('> ')) { out.push(`<blockquote>${render(line.slice(2))}</blockquote>`); i++; continue; }
    const paragraph = [line]; i++;
    while (i < lines.length && lines[i].trim() && !tableAt(i) &&
      !/^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|`{3,}|~{3,}|>\s|\\\[|\$\$)/.test(lines[i])) paragraph.push(lines[i++].trim());
    out.push(`<p>${paragraph.map(render).join('<br>')}</p>`);
  }
  return out.join('');
}

export function matchingSourceLines(citation, chunks) {
  const seen = new Set();
  const lines = chunks.flatMap((chunk) => chunk.lines || []).filter((line) => {
    if (citation.page && Number(line.page) !== Number(citation.page)) return false;
    const key = JSON.stringify([line.page, line.bbox, line.text]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const normalize = (text) => String(text || '').trim().replace(/\s+/g, ' ');
  const text = lines.map((line) => normalize(line.text)).join(' ');
  const quote = normalize(citation.text);
  const start = quote ? text.indexOf(quote) : -1;
  if (start < 0) return lines;
  let offset = 0;
  return lines.filter((line) => {
    const length = normalize(line.text).length;
    const match = offset < start + quote.length && offset + length > start;
    offset += length + 1;
    return match;
  });
}
