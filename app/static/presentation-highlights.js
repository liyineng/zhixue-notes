// Match saved citation text to the current converted preview, never guessed boxes.
const normalized = value => String(value || '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
const substantial = value => /[\u3400-\u9fff]/u.test(value) ? value.length >= 4 : value.length >= 8;

export function presentationHighlights(references, pageLines, pageNumber, formulaRegions = []) {
  const lines = pageLines.filter(line => Number(line.page) === Number(pageNumber) && Array.isArray(line.bbox));
  const fragments = lines.map(line => normalized(line.text));
  const page = fragments.join('');
  const offsets = [];
  let offset = 0;
  for (const fragment of fragments) { offsets.push(offset); offset += fragment.length; }
  const output = [];
  for (const citation of references) {
    if (Number(citation.page) !== Number(pageNumber)) continue;
    const quote = normalized(citation.text);
    if (!substantial(quote)) continue;
    const spans = [];
    const locate = text => {
      if (!substantial(text)) return;
      for (let start = page.indexOf(text); start >= 0; start = page.indexOf(text, start + text.length)) spans.push([start, start + text.length]);
    };
    locate(quote);
    if (!spans.length) {
      // A citation may include formulas that conversion represents differently.
      // Only try saved source lines covered by the quote, not all context lines.
      for (const line of citation.lines || []) {
        if (Number(line.page) !== Number(pageNumber)) continue;
        const fragment = normalized(line.text);
        if (quote.includes(fragment)) locate(fragment);
      }
      // Handle wrap/spacing differences using complete visible lines only.
      for (let i = 0; i < fragments.length; i++) {
        if (substantial(fragments[i]) && quote.includes(fragments[i])) spans.push([offsets[i], offsets[i] + fragments[i].length]);
      }
    }
    for (let i = 0; i < lines.length; i++) {
      if (fragments[i] && spans.some(([start, end]) => offsets[i] < end && offsets[i] + fragments[i].length > start)) {
        output.push({ ...lines[i], citationIndex: Number(citation.index) });
      }
    }
    for (const region of formulaRegions) {
      if (region.kind !== 'equation-object' || Number(region.page) !== Number(pageNumber) || !Array.isArray(region.bbox)) continue;
      const anchors = (region.anchor_texts || []).map(normalized).filter(substantial);
      if (anchors.some(anchor => quote.includes(anchor))) {
        output.push({ page: Number(pageNumber), bbox: region.bbox, citationIndex: Number(citation.index), formula: true });
      }
    }
  }
  return output;
}
