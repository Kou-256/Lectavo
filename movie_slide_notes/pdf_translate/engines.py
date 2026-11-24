"""Translation engines: built-in local LLM, Claude, OpenAI-compatible APIs, and DeepL."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from threading import Event

from movie_slide_notes.pdf_translate.languages import AUTO, language

PROVIDERS: dict[str, str] = {
    "builtin": "内蔵ローカルLLM（無料・オフライン）",
    "claude": "Claude（Anthropic）",
    "openai": "OpenAI",
    "gemini": "Gemini（Google）",
    "deepl": "DeepL",
    "local": "外部のローカルLLM（Ollama / LM Studio）",
}
DEFAULT_PROVIDER = "builtin"

DEFAULT_MODELS: dict[str, str] = {
    "builtin": "qwen3-4b-2507",
    "claude": "claude-opus-5-5",
    "openai": "gpt-5-mini",
    "gemini": "gemini-2.5-flash",
    "deepl": "",
    "local": "qwen3:8b",
}

MODEL_SUGGESTIONS: dict[str, list[str]] = {
    "builtin": [],
    "claude": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
    "openai": ["gpt-5-mini", "gpt-5", "gpt-4.1-mini"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro"],
    "deepl": [],
    "local": ["qwen3:8b", "gemma3:12b", "llama3.1:8b"],
}

DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "local": "http://localhost:11434/v1",
}

# Claude models that accept the server-side refusal fallback.
CLAUDE_FALLBACK_MODELS = {
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-fable-5-2",
    "claude-fable-5-1",
    "claude-opus-5",
}


def style_instruction(style: str, target: str) -> str:
    name = language(target).prompt_name
    if style == "literal":
        text = (
            f"Translate faithfully and literally into {name}. Keep the original sentence structure, "
            "clause order and every qualifier, even where the result is a little stiff. "
            "Do not paraphrase, summarize, or add explanations."
        )
        return text + (" Use である調." if target == "ja" else "")
    if style == "plain":
        if target == "ja":
            return (
                "Translate into やさしい日本語 (easy Japanese) for readers who are not specialists or "
                "not fluent in Japanese: short sentences, everyday words, です・ます調, and avoid "
                "difficult kanji words when a simpler word exists. The first time a technical term "
                "appears, keep it and add a short explanation in parentheses. Do not drop any information."
            )
        return (
            f"Translate into plain, easy {name} for non-specialist readers and language learners: "
            "short sentences and common everyday words. The first time a technical term appears, keep "
            "it and add a short explanation in parentheses. Do not drop any information."
        )
    text = (
        f"Translate into natural, fluent {name} that reads as if it were written in {name}. "
        "Restructure sentences freely when it improves readability, without adding or dropping meaning."
    )
    return text + (
        " Use the plain academic style (である調) for papers and reports." if target == "ja" else ""
    )


def system_prompt(style: str, source: str, target: str) -> str:
    target_name = language(target).prompt_name
    origin = (
        "The items may be in any language; detect it per item."
        if source == AUTO
        else f"The source language is {language(source).prompt_name}."
    )
    labels = "図1 / 表2" if target == "ja" else f"the usual {target_name} form"
    return f"""You translate text extracted from a PDF into {target_name}. {origin}

Each input item is one paragraph, heading, caption or list entry from the same document. Rules:
- Translate every item; return exactly one translation per id, with the same ids.
- If an item is already in {target_name}, return it unchanged.
- Copy markers like ⟦0⟧ exactly, once each, where they belong in the translation; they stand for ions and chemical formulas.
- Keep numbers, units, chemical formulas, math, variable names, citations like [3] or (Smith et al., 2020), URLs and code unchanged.
- Translate figure and table labels as {labels}, and section headings concisely.
- Keep proper nouns that are normally left untranslated (software, gene names, etc.) as they are.
- Use the same term for the same source term throughout.
- Output only the translation text for each item, with no notes.

Style: {style_instruction(style, target)}"""


DEEPL_URL_FREE = "https://api-free.deepl.com/v2/translate"
DEEPL_URL_PRO = "https://api.deepl.com/v2/translate"


class TranslationError(RuntimeError):
    """A translation request failed in a way the user can act on."""


@dataclass
class EngineConfig:
    provider: str
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    use_gpu: bool = True

    @property
    def cache_key(self) -> str:
        return f"{self.provider}:{self.model or '-'}"


def supports_style(provider: str, style: str) -> bool:
    return not (provider == "deepl" and style == "plain")


def supports_language(provider: str, source: str, target: str) -> bool:
    if provider != "deepl":
        return True
    if language(target).deepl_target is None:
        return False
    return source == AUTO or language(source).deepl_source is not None


def needs_api_key(provider: str) -> bool:
    return provider not in ("local", "builtin")


def _items_payload(texts: list[str], context: str) -> str:
    items = [{"id": index, "text": text} for index, text in enumerate(texts)]
    header = f"Document: {context}\n\n" if context else ""
    return header + "Translate these items:\n" + json.dumps({"items": items}, ensure_ascii=False)


def parse_translations(raw: str, count: int) -> list[str]:
    """Read ``{"translations": [{"id", "text"}]}`` (tolerating extra text around it)."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise TranslationError("翻訳結果を読み取れませんでした。")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise TranslationError("翻訳結果を読み取れませんでした。") from exc
    entries = data.get("translations", data.get("items", []))
    results: list[str | None] = [None] * count
    for entry in entries if isinstance(entries, list) else []:
        try:
            index = int(entry["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= index < count and isinstance(entry.get("text"), str):
            results[index] = entry["text"].strip()
    missing = [i for i, value in enumerate(results) if not value]
    if missing:
        raise TranslationError(f"{len(missing)}件の翻訳が返されませんでした。")
    return [value or "" for value in results]


TRANSLATIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}


class Engine:
    # How many requests may run at once, and how much text goes into one request.
    parallel = 3
    batch_chars = 3500
    # Ions and formulas are swapped for ⟦n⟧ markers before translating.
    protect_terms = True

    def __init__(self, config: EngineConfig):
        self.config = config

    def prepare(self, cancel_event: Event | None = None) -> None:
        """Called once before translating (e.g. to start a local model)."""

    def translate(
        self,
        texts: list[str],
        style: str,
        context: str = "",
        source: str = AUTO,
        target: str = "ja",
    ) -> list[str]:
        raise NotImplementedError

    def _retry(self, call, attempts: int = 4):
        delay = 2.0
        for attempt in range(attempts):
            try:
                return call()
            except _Retryable as exc:
                if attempt == attempts - 1:
                    raise TranslationError(str(exc)) from exc
                time.sleep(exc.wait or delay)
                delay *= 2
        raise TranslationError("翻訳に失敗しました。")


class _Retryable(Exception):
    def __init__(self, message: str, wait: float | None = None):
        super().__init__(message)
        self.wait = wait


class ClaudeEngine(Engine):
    def __init__(self, config: EngineConfig):
        super().__init__(config)
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=config.api_key or None, max_retries=3, timeout=300.0
        )

    def translate(
        self,
        texts: list[str],
        style: str,
        context: str = "",
        source: str = AUTO,
        target: str = "ja",
    ) -> list[str]:
        anthropic = self._anthropic
        model = self.config.model or DEFAULT_MODELS["claude"]
        request = {
            "model": model,
            "max_tokens": 16000,
            "system": system_prompt(style, source, target),
            "messages": [{"role": "user", "content": _items_payload(texts, context)}],
            "output_config": {
                "effort": "medium",
                "format": {"type": "json_schema", "schema": TRANSLATIONS_SCHEMA},
            },
        }
        try:
            if model in CLAUDE_FALLBACK_MODELS:
                response = self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request
                )
            else:
                response = self._client.messages.create(**request)
        except anthropic.AuthenticationError as exc:
            raise TranslationError("ClaudeのAPIキーが正しくありません。") from exc
        except anthropic.PermissionDeniedError as exc:
            raise TranslationError("このAPIキーではClaudeを利用できません。") from exc
        except anthropic.NotFoundError as exc:
            raise TranslationError(f"モデル「{model}」が見つかりません。") from exc
        except anthropic.RateLimitError as exc:
            raise TranslationError(
                "Claudeの利用上限に達しました。時間をおいて再実行してください。"
            ) from exc
        except anthropic.BadRequestError as exc:
            raise TranslationError(f"Claudeへのリクエストが拒否されました: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise TranslationError(f"Claudeでエラーが発生しました（{exc.status_code}）。") from exc
        except anthropic.APIConnectionError as exc:
            raise TranslationError(
                "Claudeに接続できません。ネットワークを確認してください。"
            ) from exc
        if response.stop_reason == "refusal":
            raise TranslationError("Claudeがこの内容の翻訳を断りました。")
        if response.stop_reason == "max_tokens":
            raise TranslationError("翻訳結果が長すぎて途中で切れました。")
        text = "".join(block.text for block in response.content if block.type == "text")
        return parse_translations(text, len(texts))


class OpenAICompatibleEngine(Engine):
    def _base_url(self) -> str:
        return (self.config.base_url or DEFAULT_BASE_URLS.get(self.config.provider, "")).rstrip("/")

    def _model(self) -> str:
        return self.config.model or DEFAULT_MODELS.get(self.config.provider, "")

    def _api_key(self) -> str:
        return self.config.api_key

    def _extra_body(self) -> dict:
        return {"response_format": {"type": "json_object"}}

    def _connect_error(self) -> Exception:
        if self.config.provider == "local":
            return TranslationError(
                "ローカルLLMに接続できません。Ollama または LM Studio が起動しているか確認してください。"
            )
        return _Retryable("翻訳サービスに接続できません。")

    def translate(
        self,
        texts: list[str],
        style: str,
        context: str = "",
        source: str = AUTO,
        target: str = "ja",
    ) -> list[str]:
        import httpx

        base = self._base_url()
        model = self._model()
        headers = {"Content-Type": "application/json"}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt(style, source, target)
                    + '\n\nRespond with JSON only: {"translations": [{"id": 0, "text": "..."}]}',
                },
                {"role": "user", "content": _items_payload(texts, context)},
            ],
            **self._extra_body(),
        }

        def call():
            try:
                response = httpx.post(
                    f"{base}/chat/completions", headers=headers, json=body, timeout=600.0
                )
            except httpx.ConnectError as exc:
                raise self._connect_error() from exc
            except httpx.TimeoutException as exc:
                raise _Retryable("翻訳サービスの応答がありません。") from exc
            if response.status_code in (429, 500, 502, 503, 504):
                wait = response.headers.get("retry-after")
                raise _Retryable(
                    f"翻訳サービスが混雑しています（{response.status_code}）。",
                    float(wait) if wait and wait.isdigit() else None,
                )
            if response.status_code in (401, 403):
                raise TranslationError("APIキーが正しくないか、権限がありません。")
            if response.status_code == 404:
                raise TranslationError(f"モデル「{model}」またはURLが見つかりません。")
            if response.status_code >= 400:
                raise TranslationError(
                    f"翻訳サービスでエラーが発生しました（{response.status_code}）。"
                )
            return response.json()

        data = self._retry(call)
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationError("翻訳結果を読み取れませんでした。") from exc
        if choice.get("finish_reason") == "length":
            raise TranslationError("翻訳結果が長すぎて途中で切れました。")
        return parse_translations(content, len(texts))


class BuiltinEngine(OpenAICompatibleEngine):
    """The bundled llama.cpp server with a downloaded model, on this PC only."""

    parallel = 1
    batch_chars = 1600

    def __init__(self, config: EngineConfig):
        super().__init__(config)
        from movie_slide_notes.pdf_translate import local_llm

        self._local = local_llm
        self.model = local_llm.model_by_key(config.model or DEFAULT_MODELS["builtin"])
        self._base = ""

    def prepare(self, cancel_event: Event | None = None) -> None:
        try:
            self._base = self._local.server().ensure(self.model, self.config.use_gpu, cancel_event)
        except self._local.LocalLLMError as exc:
            raise TranslationError(str(exc)) from exc

    def _base_url(self) -> str:
        if not self._base:
            self.prepare()
        return self._base

    def _model(self) -> str:
        return self.model.key

    def _api_key(self) -> str:
        return self._local.server().api_key

    def _extra_body(self) -> dict:
        return {
            # Constrains the output to the exact JSON shape; small models need it.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "translations", "schema": TRANSLATIONS_SCHEMA},
            },
            "chat_template_kwargs": {"enable_thinking": False},
            "temperature": 0.2,
            "max_tokens": 4096,
        }

    def _connect_error(self) -> Exception:
        return TranslationError("内蔵のローカルLLMが応答しません。もう一度実行してください。")


class DeepLEngine(Engine):
    protect_terms = False  # DeepL keeps such terms itself and may reorder markers

    def translate(
        self,
        texts: list[str],
        style: str,
        context: str = "",
        source: str = AUTO,
        target: str = "ja",
    ) -> list[str]:
        import httpx

        if not supports_style("deepl", style):
            raise TranslationError("DeepLは「やさしい表現」に対応していません。")
        if not supports_language("deepl", source, target):
            raise TranslationError("DeepLはこの言語に対応していません。")
        key = self.config.api_key
        url = DEEPL_URL_FREE if key.endswith(":fx") else DEEPL_URL_PRO
        body = {
            "text": texts,
            "target_lang": language(target).deepl_target,
            "preserve_formatting": True,
        }
        if source != AUTO:
            body["source_lang"] = language(source).deepl_source
        if style == "literal" and target in ("ja", "de", "fr", "it", "es", "nl", "pl", "pt", "ru"):
            body["formality"] = "prefer_more"
        if context:
            body["context"] = context

        def call():
            try:
                response = httpx.post(
                    url,
                    headers={"Authorization": f"DeepL-Auth-Key {key}"},
                    json=body,
                    timeout=120.0,
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise _Retryable("DeepLに接続できません。") from exc
            if response.status_code in (429, 500, 502, 503, 504, 529):
                raise _Retryable(f"DeepLが混雑しています（{response.status_code}）。")
            if response.status_code == 403:
                raise TranslationError("DeepLのAPIキーが正しくありません。")
            if response.status_code == 456:
                raise TranslationError("DeepLの今月の翻訳文字数の上限に達しました。")
            if response.status_code >= 400:
                raise TranslationError(f"DeepLでエラーが発生しました（{response.status_code}）。")
            return response.json()

        data = self._retry(call)
        translations = [item.get("text", "") for item in data.get("translations", [])]
        if len(translations) != len(texts):
            raise TranslationError("DeepLの翻訳結果の件数が一致しません。")
        return translations


def create_engine(config: EngineConfig) -> Engine:
    if needs_api_key(config.provider) and not config.api_key:
        raise TranslationError(
            "翻訳エンジンのAPIキーが設定されていません。設定ページで登録してください。"
        )
    if config.provider == "builtin":
        return BuiltinEngine(config)
    if config.provider == "claude":
        return ClaudeEngine(config)
    if config.provider == "deepl":
        return DeepLEngine(config)
    if config.provider in ("openai", "gemini", "local"):
        return OpenAICompatibleEngine(config)
    raise TranslationError(f"不明な翻訳エンジンです: {config.provider}")
