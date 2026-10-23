from __future__ import annotations

import json
import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from .retrieval import excerpt, quoted_evidence
from .model_context import context_info, history_budget, estimate_tokens

PLANNER_SYSTEM_PROMPT = """你是课程资料学习助手的检索规划器。
分析用户输入，只返回一个 JSON 对象，不要使用代码块，不要添加解释。

JSON 字段：
{
  "intent": "chat" | "summary" | "question",
  "needs_retrieval": true | false,
  "search_query": "用于在课程资料中检索的自然语言查询",
  "keywords": ["关键词1", "关键词2"],
  "reason": "一句话说明判断"
}

规则：
1. 问候、感谢、询问助手身份或能力，intent 填 chat，needs_retrieval 填 false。
2. 要求总结、梳理资料、章节、课件或考点，intent 填 summary，needs_retrieval 填 true。
3. 询问资料中的概念、公式、事实或要求，intent 填 question，needs_retrieval 填 true。
4. 与上传资料无关的一般知识问题，needs_retrieval 填 false。
5. search_query 需要补全上下文、扩展同义表达和可能的关键词，不能只是复制用户原句。
6. 使用与用户输入相同的语言。"""

ANSWER_SYSTEM_PROMPT = """你是课程资料学习助手。

回答规则：
1. 只有“资料片段”中明确出现的内容才能作为资料依据，并必须标注引用编号，例如 [1]。
2. 不得编造引用编号，也不得把资料中没有的事实写成资料依据。
3. 资料能够回答时，先写“【资料依据】”，再给出答案。
4. 资料不足，但你用通用模型知识补充时，写“【通用知识补充】”，并明确这部分不是来自上传资料。
5. 资料与问题无关时，说明未在资料中找到直接依据；能够回答的通用知识放在“【通用知识补充】”。
6. 问候、感谢和助手能力询问可以直接自然回答，不需要强行提资料。
7. 使用与用户问题相同的语言，直接、简洁，便于复习。
8. 遇到公式时保留数学表达，不要凭空改写。"""

PLANNER_SYSTEM_PROMPT += "\n7. 图片习题检索应提炼原理与方法，不要把 H1、B1 等主机/设备编号、完整拓扑、题号和文件名当作检索关键词。"
PLANNER_SYSTEM_PROMPT += "\n8. 输入包含近期对话或明确引用的问答时，只根据“本次问题”或“本次追问”判断 intent；历史仅用于理解指代和补全检索词，不要把旧问题的意图当成本次意图。用户换话题时忽略无关历史。"
PLANNER_SYSTEM_PROMPT += "\n9. 历史引用编号不是页码。输入附有引用定位时，使用该定位对应的课件与真实页码；用户要求解答某条引用中的题目时 intent=question，不要当成整份课件总结。"
ANSWER_SYSTEM_PROMPT += """
9. 解题先给结论与最终表格，再给必要步骤，最后简列相关资料原理。使用 Markdown 表格、标题和有序列表。
10. 题图/题干中的已知条件来自用户；依据资料原理对题目推导得出的结果标为“依据资料原理推导”，无需资料中出现完全相同例题才可以引用原理。
11. 每个引用必须直接支持其前面的具体说法。不相关的生成树、路由等内容不得凑作网桥自学习的依据；资料未提到自学习时不得声称其给出了该规则。
12. 只使用提供的引用编号。不要罗列与解答无关的检索结果，也不要把所有推导机械地归入通用知识。
13. 算法、流程和状态变化题须在内部按输入顺序逐步核对：先明确每一步的输入和规则，再更新状态；不得混淆源与目的、请求与响应，不得把未发生的事件作为已知。最终表格须与逐步推导一致。
14. 近期对话和被引用的旧回答只帮助理解指代，不是资料依据。旧回答可能有误，应核对并纠正；历史引用编号不能充当本次引用。优先回答本次问题，换话题时不要延续无关旧主题。
15. 资料引用严格使用半角方括号 [1]，不要写成【1】。公式使用 LaTeX 的 \\( ... \\) 或 \\[ ... \\]，不要把公式放进代码块。
16. 长公式使用 aligned 环境分行，一行只表达一步；较长中文解释放在正文，不要堆在数学下标里。题干没有给出的初始窗口等条件不能当作默认事实，必须明确假设或分情况回答。
"""

SUMMARY_PROMPT = """你是课程资料学习助手。请仅依据用户提供的“资料片段”，生成复习材料。

输出结构：
1. 本章摘要：用 3 到 5 句话概括。
2. 核心概念：逐条列出，并在每条后标注引用编号。
3. 复习提纲：按层次列出考点，并在每条后标注引用编号。
4. 常见问题：列出 3 个可能考到的问题，并附答案要点和引用编号。

资料不足时明确写出“给定资料中未找到依据”，不得编造。
资料按课件和真实页码提供。必须通读全部页面，覆盖开头、中间和结尾的主要主题，不要只围绕第一个片段组织总结。
用户指定某份课件时，只整理这份课件。按主题合并相邻页面，各主题附能支持该主题的实际引用编号。
只使用所提供的引用编号；不要为了增加引用数量逐页罗列，也不要把单页内容说成整份课件的全部内容。
引用列表不是逐页目录，不能根据引用编号或页码跳号推断某页未提供。图片识别转写也是资料内容；标注不清晰时说明识别局限，不得猜测补全。
控制在约 1500～2200 汉字，以主题提纲覆盖全貌，避免重复同一知识点。
"""

MATH_FORMAT = r"""
公式必须使用 LaTeX：行内用 \( ... \)，独立公式用 \[ ... \]；上下标、分式、根号分别用 _、^、\frac、\sqrt。
不要用 Unicode 上下标或 √ 拼接复杂公式，不要把公式放进代码块或 Markdown 表格。
资料引用使用半角 [1]，放在数学定界符外。保留原式的符号、下标和适用条件。
"""
SUMMARY_PROMPT += MATH_FORMAT

FORMULA_PROMPT = r"""你是课程资料学习助手。用户要的是公式清单，直接逐条给出实际数学表达式。
只依据本批资料，按主题列出：公式名称、独立展示的完整公式、资料中给出的符号含义与适用条件、支持它的引用。
合并重复公式但保留不同条件或变形；不要用“某公式的表达式”代替公式本身。
不要套用本章摘要、核心概念、复习提纲、常见问题的模板，不要添加习题或考试建议。
每条公式必须能从本批资料直接读出；只有名称而无表达式、图片未识别或标记看不清时，列入“未能读取的公式”，不得用常识补写。
通读本批所有页面，不遗漏末尾；只使用本批提供的引用编号，不从编号或页码空缺推断缺页。
没有公式依据时明确说明。近期对话和旧回答不是公式依据，不得照抄。
""" + MATH_FORMAT


class OutputTruncatedError(RuntimeError):
    """The provider explicitly reported an incomplete, length-limited output."""


class ModelConnectionError(RuntimeError):
    """A transient connection failed after bounded retries with fresh clients."""


def _connection_diagnostic(exc: BaseException) -> list[dict[str, Any]]:
    """Record numeric network/TLS causes without messages, URLs or credentials."""
    pending, seen, causes = [exc], set(), []
    while pending and len(causes) < 12:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        item = {'type': type(current).__name__}
        for name in ('errno', 'verify_code'):
            value = getattr(current, name, None)
            if isinstance(value, int):
                item[name] = value
        causes.append(item)
        pending.extend(child for child in (current.__cause__, current.__context__) if child is not None)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
    return causes


def model_configured(settings: dict[str, Any]) -> bool:
    return bool(
        str(settings.get("base_url", "")).strip()
        and str(settings.get("model", "")).strip()
    )


def _endpoint(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _context_text(contexts: list[dict[str, Any]], character_limit: int = 4000) -> str:
    blocks: list[str] = []
    for context in contexts:
        text = " ".join(str(context["text"]).split())
        if len(text) > character_limit:
            text = text[:character_limit].rstrip() + "..."
        page = context.get("page")
        page_text = f"，第 {page} 页" if page else ""
        recognition = "（图片识别转写，公式与图表以原图为准）" if context.get('image_recognition') else ""
        blocks.append(
            f"[{context['index']}] 来源：{context['document_name']}{page_text}{recognition}\n{text}"
        )
    return "\n\n".join(blocks)


async def select_evidence(question: str, candidates: list[dict[str, Any]],
                          settings: dict[str, Any], limit: int = 6, *,
                          checkpoint_factory=None, on_batch=None) -> list[dict[str, Any]]:
    if not candidates or not model_configured(settings):
        return candidates if limit == 0 else candidates[:limit]
    if limit == 0:
        # Check every matching candidate in bounded requests, without silently
        # restoring a six/twenty-fragment cap. Keep original provenance intact.
        selected, seen = [], set()
        capacity = history_budget(context_info(settings), {**settings, 'max_tokens': max(5600, int(settings.get('max_tokens') or 1400))}, question)
        batches, batch, used = [], [], 0
        for candidate in candidates:
            size = estimate_tokens(_context_text([candidate])) + 2
            if size > capacity:
                raise RuntimeError('相关资料超出当前模型的上下文容量，请缩小勾选来源范围或检查模型上下文设置。')
            if batch and (len(batch) >= 8 or used + size > capacity):
                batches.append(batch)
                batch, used = [], 0
            batch.append(candidate)
            used += size
        if batch:
            batches.append(batch)
        checkpoints = checkpoint_factory(batches) if checkpoint_factory else None
        for index, batch in enumerate(batches, 1):
            cached = _cached_evidence(checkpoints.get(index - 1), batch) if checkpoints else None
            if on_batch:
                on_batch(index, len(batches), batch, cached is not None)
            evidence_batch = cached if cached is not None else await _select_evidence_batch(question, batch, settings, len(batch))
            if checkpoints and cached is None:
                checkpoints.save(index - 1, evidence_batch)
            for evidence in evidence_batch:
                key = (evidence['document_id'], evidence.get('page'), evidence['text'])
                if key not in seen:
                    seen.add(key)
                    selected.append({**evidence, 'index': len(selected) + 1})
        return selected
    return await _select_evidence_batch(question, candidates, settings, limit)


def _cached_evidence(cached, candidates: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Rebuild cached quotations from current candidates, never cached geometry."""
    if not isinstance(cached, list):
        return None
    verified = []
    for item in cached:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            return None
        evidence = None
        for candidate in candidates:
            if any(item.get(key) != candidate.get(key) for key in ('document_id', 'page', 'chunk_id', 'id')):
                continue
            evidence = quoted_evidence(candidate, item['text'])
            if evidence is not None:
                break
        if evidence is None:
            return None
        verified.append({**evidence, 'index': len(verified) + 1})
    return verified if len(verified) <= len(candidates) else None


async def _select_evidence_batch(question: str, candidates: list[dict[str, Any]],
                                 settings: dict[str, Any], limit: int,
                                 quote_limit: int = 500) -> list[dict[str, Any]]:
    try:
        content = await chat_completion(
            base_url=str(settings["base_url"]), api_key=str(settings.get("api_key", "")),
            model=str(settings["model"]), temperature=0, max_tokens=max(1800, limit * 700),
            thinking=False, json_mode=True,
            messages=[{"role": "system", "content":
                "你是资料证据筛选器。只返回 JSON 对象：{\"evidence\":[{\"source\":1,\"quote\":\"原文连续摘录\"}]}。"
                "挑选直接支持解题原理或方法的资料，按相关性排序。不要被主机号、设备号、题号等巧合词吸引。"
                "例如网桥自学习题应选源地址学习/目的地址查表/转发过滤的规则，而不是生成树或路由器转发表。"
                f"最多 {limit} 条，每条摘录不超过 {quote_limit} 字。同一页可以摘录不同规则，但不要重复。"
                "quote 必须是候选原文连续的逐字摘录，可把换行替换为空格，不得改写或省略中间文字。"
                "没有直接相关证据时返回 {\"evidence\":[]}。"},
                {"role": "user", "content": f"题目：{question}\n\n候选原文：\n{_context_text(candidates)}"}])
    except OutputTruncatedError as exc:
        # Retry only explicit output truncation. Each smaller request retains the
        # original source indices; never parse a partial response or skip a half.
        if len(candidates) > 1:
            middle = len(candidates) // 2
            selected, seen = [], set()
            for batch in (candidates[:middle], candidates[middle:]):
                evidence_batch = await _select_evidence_batch(
                    question, batch, settings, min(limit, len(batch)), quote_limit)
                for evidence in evidence_batch:
                    key = (evidence['document_id'], evidence.get('page'), evidence['text'])
                    if key not in seen:
                        seen.add(key)
                        selected.append({**evidence, 'index': len(selected) + 1})
            return selected[:limit]
        if quote_limit > 160:
            return await _select_evidence_batch(question, candidates, settings, limit, 160)
        candidate = candidates[0]
        page = f"第 {candidate['page']} 页" if candidate.get('page') else '该片段'
        raise RuntimeError(
            f"单独核对《{candidate['document_name']}》{page}并缩短摘录后，模型输出仍达到长度上限。"
            "本次未使用不完整的证据，请重试；若持续失败，请检查模型接口。") from exc
    parsed = _extract_json(content)
    if parsed is None or not isinstance(parsed.get("evidence"), list):
        raise RuntimeError("资料证据筛选未返回有效结果，请重试。")
    selected = []
    seen = set()
    by_index = {c["index"]: c for c in candidates}
    for item in parsed["evidence"]:
        if not isinstance(item, dict) or not isinstance(item.get("source"), int):
            continue
        candidate = by_index.get(item["source"])
        if not candidate or not isinstance(item.get("quote"), str):
            continue
        evidence = quoted_evidence(candidate, item["quote"])
        if evidence is None:
            continue
        key = (evidence["document_id"], evidence.get("page"), evidence["text"])
        if key in seen:
            continue
        selected.append({**evidence, "index": len(selected) + 1})
        seen.add(key)
        if len(selected) == limit:
            break
    return selected


def cited_sources(answer: str, contexts: list[dict[str, Any]], *, renumber: bool = True) -> tuple[str, list[dict[str, Any]]]:
    """Return only sources actually cited, with matching contiguous reference numbers."""
    parts = re.split(r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`]*`+|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\$\$[\s\S]*?\$\$|\$[^$\n]+\$)", answer)
    parts = [re.sub(r'【(\d+)】', r'[\1]', part) if i % 2 == 0 else part for i, part in enumerate(parts)]
    used = {int(n) for part in parts[::2] for n in re.findall(r"\[(\d+)\]", part)}
    allowed = {c["index"] for c in contexts}
    if used - allowed:
        raise RuntimeError("模型使用了不存在的资料引用编号，请重新生成回答。")
    sources = [c for c in contexts if c["index"] in used]
    if not renumber:
        return ''.join(parts), sources
    numbers = {c["index"]: i for i, c in enumerate(sources, 1)}
    answer = "".join(re.sub(r"\[(\d+)\]", lambda m: f"[{numbers[int(m[1])]}]", part)
                     if i % 2 == 0 else part for i, part in enumerate(parts))
    return answer, [{**c, "index": i} for i, c in enumerate(sources, 1)]


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    thinking: bool | None = None,
    json_mode: bool = False,
) -> str:
    headers = {"Content-Type": "application/json"}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    deepseek = urlparse(base_url).hostname == "api.deepseek.com"
    if json_mode and deepseek:
        payload['response_format'] = {'type': 'json_object'}
    if deepseek and thinking is not None:
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        if thinking:
            payload["reasoning_effort"] = "low"
    async def request() -> httpx.Response:
        for attempt in range(4):
            try:
                # A disconnected proxy/HTTP connection must not be reused on
                # retry. Keep the configured proxy and credentials unchanged.
                async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
                    response = await client.post(_endpoint(base_url), headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                if attempt >= 1:
                    raise RuntimeError('模型接口请求超时，自动重试后仍未收到完整响应。已完成的识别和资料核对批次会保留，请稍后重试。') from exc
            except httpx.TransportError as exc:
                logging.getLogger(__name__).warning('Model connection attempt %s/4 failed: %s',
                                                    attempt + 1, _connection_diagnostic(exc))
                if attempt >= 3:
                    raise ModelConnectionError(
                        f'连接模型接口失败（{type(exc).__name__}，4 次尝试）。已完成的资料核对批次会保留，'
                        '可按原来源重试；若持续失败，请检查网络、代理和模型接口地址。') from exc
            else:
                if response.status_code not in {429, 502, 503, 504} or attempt >= 1:
                    return response
            await asyncio.sleep((0.5, 2, 5)[attempt])
        raise RuntimeError('模型接口请求未完成，请稍后重试。')

    response = await request()
    if response.status_code < 400 and deepseek:
        choices = response.json().get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        if choices and (not message.get("content") or
                        (thinking is not False and choice.get("finish_reason") == "length")):
            # Thinking tokens share the output budget. Preserve reasoning for
            # problem solving; short extraction tasks can retry without it.
            if thinking is True:
                payload["max_tokens"] = min(16384, max(8192, max_tokens * 2))
            else:
                payload["thinking"] = {"type": "disabled"}
            response = await request()
    if response.status_code >= 400:
        detail = response.text[:800]
        raise RuntimeError(
            f"Model request failed with HTTP {response.status_code}: {detail}"
        )
    data = response.json()
    try:
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise OutputTruncatedError("模型输出达到长度上限，未完整返回结果。请在模型设置中提高最大输出长度后重试。")
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "Model response does not contain choices[0].message.content"
        ) from exc
    if isinstance(content, list):
        content = "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    content = str(content or "").strip()
    if not content:
        raise RuntimeError("模型接口返回了空的正文，请重试；若持续出现，请检查模型或服务商状态。")
    return content


async def analyze_question(
    question: str,
    document_names: list[str],
    settings: dict[str, Any],
) -> dict[str, Any]:
    fallback = {
        "intent": "question",
        "needs_retrieval": True,
        "search_query": question,
        "keywords": [],
        "reason": "未配置模型，使用原始问题检索。",
    }
    if not model_configured(settings):
        return fallback

    documents = "、".join(document_names[:20]) or "暂无上传资料"
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"已上传资料：{documents}\n"
                f"用户输入：{question}\n\n"
                "请判断是否需要检索，并生成更适合检索的查询词。"
            ),
        },
    ]
    content = await chat_completion(
        base_url=str(settings["base_url"]),
        api_key=str(settings.get("api_key", "")),
        model=str(settings["model"]),
        messages=messages,
        temperature=0.0,
        max_tokens=420,
        thinking=False,
    )
    parsed = _extract_json(content)
    if not parsed:
        return fallback

    intent = str(parsed.get("intent", "question")).strip().lower()
    if intent not in {"chat", "summary", "question"}:
        intent = "question"
    keywords = parsed.get("keywords")
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(item).strip() for item in keywords if str(item).strip()]
    search_query = str(parsed.get("search_query", "")).strip() or question
    return {
        "intent": intent,
        "needs_retrieval": bool(parsed.get("needs_retrieval", True)),
        "search_query": search_query,
        "keywords": keywords[:12],
        "reason": str(parsed.get("reason", "")).strip(),
    }


def extractive_answer(contexts: list[dict[str, Any]], question: str = "") -> str:
    if not contexts:
        return "当前未配置模型，且未在资料中检索到相关片段。"
    lines = [
        "当前尚未配置模型，先返回检索到的资料原文。配置模型后可以生成归纳式回答。",
        "",
    ]
    for context in contexts[:3]:
        text = (
            excerpt(str(context["text"]), question, limit=260)
            if question
            else " ".join(str(context["text"]).split())
        )
        if not question and len(text) > 230:
            text = text[:230].rstrip() + "..."
        lines.append(f"- {text} [{context['index']}]")
    return "\n".join(lines)


async def answer_question(
    question: str,
    contexts: list[dict[str, Any]],
    settings: dict[str, Any],
    document_names: list[str] | None = None,
    needs_retrieval: bool = True,
) -> str:
    if not model_configured(settings):
        return extractive_answer(contexts, question)

    names = "、".join(document_names or []) or "暂无上传资料"
    if not needs_retrieval:
        retrieval_section = (
            "本次不需要检索上传资料。请直接回答用户输入，"
            "不要假设资料中没有内容，也不要编造资料引用。"
        )
    elif contexts:
        retrieval_section = f"资料片段：\n{_context_text(contexts)}"
    else:
        retrieval_section = (
            "已完成资料检索，但没有找到相关片段。"
            "如果使用通用知识回答，必须用“【通用知识补充】”标明。"
        )
    messages = [
        {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"上传资料名称：{names}\n\n"
                f"{retrieval_section}\n\n"
                f"用户输入：{question}\n\n"
                "请根据上述规则回答。"
            ),
        },
    ]
    deepseek = urlparse(str(settings["base_url"])).hostname == "api.deepseek.com"
    return await chat_completion(
        base_url=str(settings["base_url"]),
        api_key=str(settings.get("api_key", "")),
        model=str(settings["model"]),
        messages=messages,
        temperature=float(settings.get("temperature", 0.2)),
        max_tokens=max(8192, int(settings.get("max_tokens", 1400))) if deepseek else int(settings.get("max_tokens", 1400)),
        thinking=True if deepseek else None,
    )


async def make_summary(
    contexts: list[dict[str, Any]],
    settings: dict[str, Any],
    question: str = "",
    *, formula_inventory: bool = False,
) -> str:
    if not contexts:
        return "给定资料中未找到依据。"
    if not model_configured(settings):
        return extractive_answer(contexts)

    messages = [
        {"role": "system", "content": FORMULA_PROMPT if formula_inventory else SUMMARY_PROMPT},
        {
            "role": "user",
            "content": f"整理要求：{question or '整理当前选中的全部课程资料'}\n\n按页提供的资料：\n{_context_text(contexts, character_limit=100000)}",
        },
    ]
    return await chat_completion(
        base_url=str(settings["base_url"]),
        api_key=str(settings.get("api_key", "")),
        model=str(settings["model"]),
        messages=messages,
        temperature=float(settings.get("temperature", 0.2)),
        max_tokens=max(int(settings.get("max_tokens", 1400)), 8192),
        thinking=False,
    )
