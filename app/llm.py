from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .retrieval import excerpt

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

SUMMARY_PROMPT = """你是课程资料学习助手。请仅依据用户提供的“资料片段”，生成复习材料。

输出结构：
1. 本章摘要：用 3 到 5 句话概括。
2. 核心概念：逐条列出，并在每条后标注引用编号。
3. 复习提纲：按层次列出考点，并在每条后标注引用编号。
4. 常见问题：列出 3 个可能考到的问题，并附答案要点和引用编号。

资料不足时明确写出“给定资料中未找到依据”，不得编造。
"""


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


def _context_text(contexts: list[dict[str, Any]], character_limit: int = 520) -> str:
    blocks: list[str] = []
    for context in contexts:
        text = " ".join(str(context["text"]).split())
        if len(text) > character_limit:
            text = text[:character_limit].rstrip() + "..."
        page = context.get("page")
        page_text = f"，第 {page} 页" if page else ""
        blocks.append(
            f"[{context['index']}] 来源：{context['document_name']}{page_text}\n{text}"
        )
    return "\n\n".join(blocks)


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
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            _endpoint(base_url),
            headers=headers,
            json=payload,
        )
    if response.status_code >= 400:
        detail = response.text[:800]
        raise RuntimeError(
            f"Model request failed with HTTP {response.status_code}: {detail}"
        )
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "Model response does not contain choices[0].message.content"
        ) from exc
    if isinstance(content, list):
        content = "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    return str(content).strip()


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
    return await chat_completion(
        base_url=str(settings["base_url"]),
        api_key=str(settings.get("api_key", "")),
        model=str(settings["model"]),
        messages=messages,
        temperature=float(settings.get("temperature", 0.2)),
        max_tokens=int(settings.get("max_tokens", 1400)),
    )


async def make_summary(
    contexts: list[dict[str, Any]],
    settings: dict[str, Any],
) -> str:
    if not contexts:
        return "给定资料中未找到依据。"
    if not model_configured(settings):
        return extractive_answer(contexts)

    messages = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {
            "role": "user",
            "content": f"资料片段：\n{_context_text(contexts, character_limit=620)}",
        },
    ]
    return await chat_completion(
        base_url=str(settings["base_url"]),
        api_key=str(settings.get("api_key", "")),
        model=str(settings["model"]),
        messages=messages,
        temperature=float(settings.get("temperature", 0.2)),
        max_tokens=max(int(settings.get("max_tokens", 1400)), 1800),
    )
