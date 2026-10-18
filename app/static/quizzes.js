import { renderAnswerHtml } from '/static/answer-format.js';
import renderMath from '/static/vendor/katex/contrib/auto-render.mjs';

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function rich(element, text, citations, onCitation, colorCitation) {
  element.innerHTML = renderAnswerHtml(text, citations);
  renderMath(element, { delimiters: [{ left: '\\[', right: '\\]', display: true },
    { left: '\\(', right: '\\)', display: false }, { left: '$$', right: '$$', display: true },
    { left: '$', right: '$', display: false }], throwOnError: false, trust: false, strict: 'ignore',
    ignoredClasses: ['citation-ref', 'unavailable-reference'] });
  element.querySelectorAll('.citation-ref').forEach(button => {
    colorCitation?.(button, Number(button.dataset.citation));
    button.addEventListener('click', () => onCitation(Number(button.dataset.citation)));
  });
}

export function renderQuiz(container, result, { onAnswer, onReset, onCitation, colorCitation }) {
  const quiz = result.quiz;
  const questions = quiz.questions;
  const key = `zhixue-quiz-position:${result.chat_id}:${quiz.attempt}`;
  const saved = Number(localStorage.getItem(key));
  let position = Number.isInteger(saved) ? Math.max(0, Math.min(saved, questions.length - 1)) : 0;
  let saving = false;
  const answered = Object.keys(quiz.answers).length;
  const score = questions.filter(q => quiz.answers[q.id] === q.answer).length;
  const draw = () => {
    container.replaceChildren();
    const card = node('section', 'quiz-card');
    card.setAttribute('aria-label', '交互式测验');
    const header = node('div', 'quiz-card-heading');
    header.append(node('span', 'quiz-eyebrow', '练一练 · 交互式测验'), node('span', 'quiz-saved', '进度自动保存'));
    const title = node('h3', '', quiz.topic || '巩固所学，检验理解');
    const status = node('div', 'quiz-status', `已答 ${answered} / ${questions.length} 题 · 答对 ${score} 题`);
    status.setAttribute('aria-live', 'polite');
    const progress = node('progress', 'quiz-progress'); progress.max = questions.length; progress.value = answered;
    progress.setAttribute('aria-label', '作答进度');
    card.append(header, title, status, progress);
    if (answered === questions.length) card.append(node('div', 'quiz-finished', `本轮完成 · ${score} / ${questions.length} 题正确，可翻页回顾解析。`));
    const q = questions[position];
    const submitted = Object.hasOwn(quiz.answers, q.id);
    let selected = submitted ? quiz.answers[q.id] : null;
    card.append(node('div', 'quiz-question-number', `第 ${position + 1} / ${questions.length} 题`));
    const prompt = node('div', 'quiz-prompt'); rich(prompt, q.prompt, [], onCitation); card.append(prompt);
    const choices = node('div', 'quiz-options'); choices.setAttribute('role', 'group'); choices.setAttribute('aria-label', '答案选项');
    const buttons = [];
    q.options.forEach((option, i) => {
      const button = node('button', 'quiz-option'); button.type = 'button'; button.disabled = submitted;
      button.setAttribute('aria-pressed', String(selected === i));
      const letter = node('span', 'quiz-option-letter', 'ABCD'[i]);
      const text = node('span', 'quiz-option-text'); rich(text, option, [], onCitation);
      button.append(letter, text);
      if (selected === i) button.classList.add('selected');
      if (submitted && i === q.answer) { button.classList.add('correct'); button.append(node('span', 'quiz-option-mark', '✓ 正确')); }
      if (submitted && selected === i && i !== q.answer) { button.classList.add('incorrect'); button.append(node('span', 'quiz-option-mark', '× 你的选择')); }
      button.addEventListener('click', () => {
        selected = i;
        buttons.forEach((b, j) => { b.classList.toggle('selected', i === j); b.setAttribute('aria-pressed', String(i === j)); });
        check.disabled = false;
      });
      buttons.push(button); choices.append(button);
    });
    card.append(choices);
    const error = node('p', 'feature-error'); error.setAttribute('role', 'alert'); error.hidden = true;
    const perform = async (operation) => {
      if (saving) return;
      saving = true; card.inert = true;
      try {
        const updated = await operation();
        if (container.isConnected) renderQuiz(container, updated, { onAnswer, onReset, onCitation, colorCitation });
      } catch (e) { error.textContent = e.message; error.hidden = false; }
      finally { saving = false; card.inert = false; }
    };
    const check = node('button', 'button primary quiz-check', '提交答案'); check.type = 'button'; check.disabled = selected === null;
    check.addEventListener('click', () => perform(() => onAnswer(q.id, selected)));
    if (!submitted) card.append(check);
    else {
      const feedback = node('section', `quiz-feedback ${selected === q.answer ? 'is-correct' : 'is-incorrect'}`);
      feedback.setAttribute('aria-live', 'polite');
      feedback.append(node('strong', '', selected === q.answer ? '回答正确' : `再理解一下 · 正确答案 ${'ABCD'[q.answer]}`));
      const explanation = node('div', 'quiz-explanation'); rich(explanation, q.explanation, result.citations, onCitation, colorCitation); feedback.append(explanation);
      const sources = node('div', 'quiz-citation-links'); sources.append(node('span', '', '查看依据'));
      q.sources.forEach(index => {
        const reference = result.citations.find(c => c.index === index);
        const button = node('button', 'button ghost quiz-source', `[${index}] ${reference?.document_name || '来源'}${reference?.page ? ` · 第 ${reference.page} 页` : ''}`);
        button.type = 'button'; colorCitation?.(button, index); button.addEventListener('click', () => onCitation(index)); sources.append(button);
      });
      feedback.append(sources); card.append(feedback);
    }
    const nav = node('div', 'quiz-navigation');
    const previous = node('button', 'button ghost', '上一题'); previous.type = 'button'; previous.disabled = position === 0;
    const next = node('button', 'button secondary', '下一题'); next.type = 'button'; next.disabled = position === questions.length - 1;
    const move = delta => { position += delta; localStorage.setItem(key, String(position)); draw(); container.scrollIntoView({ block: 'start', behavior: 'smooth' }); };
    previous.addEventListener('click', () => move(-1)); next.addEventListener('click', () => move(1));
    nav.append(previous, next);
    if (answered) {
      const reset = node('button', 'button ghost quiz-reset', '重新作答'); reset.type = 'button';
      reset.addEventListener('click', () => {
        if (window.confirm('重新作答会清空这份测验的本轮答题进度，继续？')) perform(onReset);
      }); nav.append(reset);
    }
    card.append(error, nav);
    for (const warning of result.warnings || []) card.append(node('p', 'quiz-warning', warning));
    container.append(card);
  };
  draw();
}
