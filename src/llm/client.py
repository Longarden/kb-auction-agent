"""프로바이더 중립 LLM 추상화 (process.md P3/P7).

이 파일의 설계 원칙 세 가지.

1) 기본 프로바이더는 규칙(rule)이다. API 키가 없는 환경에서도 데모와 테스트가
   전부 동작해야 하므로, LLM 은 '있으면 쓰는 것'이지 전제조건이 아니다.
2) 두 프로바이더는 같은 dict 모양을 돌려준다. 하위(rules.py)가 프로바이더별로
   분기하지 않도록, 수렴 지점을 run.py 의 RawExtraction 검증 한 곳으로 모은다.
3) LLM 이 쓴 서술문에는 새 숫자가 등장할 수 없다(P3). assert_no_free_numbers 가
   이를 기계적으로 강제한다.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional, Protocol

from src.utils.env import optional

logger = logging.getLogger(__name__)

# ── 상수 ────────────────────────────────────────────────────
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
RULE_MODEL = "rule-v1"

ENV_PROVIDER = "SAFEBID_LLM_PROVIDER"
ENV_MODEL = "SAFEBID_LLM_MODEL"
ENV_API_KEY = "ANTHROPIC_API_KEY"

VALID_PROVIDERS = ("rule", "anthropic", "auto")

# 매각물건명세서와 등기부를 한 문자열로 넘기기 위한 구분자.
# Provider.extract 는 document_text 하나만 받는다(프로바이더 중립 서명 유지).
# 규칙 프로바이더는 이 구분자로 되쪼개고, LLM 은 라벨이 붙은 문서로 읽는다.
DOC_SEPARATOR = "\n\n===== [SAFEBID:REGISTRY] 등기사항전부증명서 =====\n\n"

# 추출기 서명: (매각물건명세서 본문, 등기부 본문) -> dict | (dict, 근거로그)
Extractor = Callable[[str, str], Any]


class LLMUnavailable(RuntimeError):
    """요청한 프로바이더를 쓸 수 없다(키 부재/패키지 부재/응답 형식 불량)."""


@dataclass(frozen=True)
class ExtractionResult:
    data: dict
    provider: str          # "rule" | "anthropic"
    model: str
    raw: str = ""
    warnings: tuple[str, ...] = ()


class Provider(Protocol):
    name: str
    model: str

    def available(self) -> bool: ...

    def extract(
        self,
        *,
        prompt: str,
        document_text: str,
        json_schema: dict,
        temperature: float = 0.0,
    ) -> ExtractionResult: ...

    def narrate(self, *, prompt: str, context: dict) -> str: ...


# ── 문서 결합/분리 ───────────────────────────────────────────
def combine_documents(sale_spec_text: str, registry_text: str) -> str:
    """두 원문을 하나의 document_text 로 합친다."""
    if not registry_text:
        return sale_spec_text
    return sale_spec_text + DOC_SEPARATOR + registry_text


def split_documents(document_text: str) -> tuple[str, str]:
    """combine_documents 의 역연산. 구분자가 없으면 등기부는 빈 문자열."""
    if DOC_SEPARATOR in document_text:
        head, _, tail = document_text.partition(DOC_SEPARATOR)
        return head, tail
    return document_text, ""


# ── P3: 자유 숫자 금지 ───────────────────────────────────────
_NUM_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _iter_context_values(obj: Any) -> Iterator[Any]:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_context_values(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from _iter_context_values(v)
    else:
        yield obj


def _allowed_number_tokens(context: dict) -> set[str]:
    allowed: set[str] = set()
    for value in _iter_context_values(context):
        if value is None or isinstance(value, bool):
            continue
        text = str(value).replace(",", "")
        for m in _NUM_TOKEN.finditer(text):
            token = m.group().replace(",", "")
            allowed.add(token)
            # 12.0 처럼 소수 표기된 정수도 12 로 매칭되게 한다.
            if token.endswith(".0"):
                allowed.add(token[:-2])
        if isinstance(value, float) and value.is_integer():
            allowed.add(str(int(value)))
    return allowed


def assert_no_free_numbers(text: str, context: dict) -> str:
    """서술문의 모든 숫자 토큰이 context 값에서 유래했는지 검사한다(P3).

    콤마는 제거하고 비교하므로 '60,000,000' 과 60000000 은 같은 값으로 본다.
    위반이면 ValueError. 통과하면 원문을 그대로 돌려준다.
    """
    allowed = _allowed_number_tokens(context)
    for m in _NUM_TOKEN.finditer(text):
        token = m.group().replace(",", "")
        if token in allowed:
            continue
        if token.endswith(".0") and token[:-2] in allowed:
            continue
        raise ValueError(
            f"LLM 서술문에 근거 없는 숫자 '{m.group()}' 가 포함되었습니다(P3 위반)."
        )
    return text


def _render(prompt: str, context: dict) -> str:
    from jinja2 import Template

    return Template(prompt, keep_trailing_newline=True).render(**context)


# ── 규칙 프로바이더(기본) ────────────────────────────────────
class RuleProvider:
    """LLM 없이 순수 함수로 추출한다. available() 은 항상 True."""

    name = "rule"

    def __init__(self, extractor: Optional[Extractor] = None, model: str = RULE_MODEL) -> None:
        self._extractor = extractor
        self.model = model

    def available(self) -> bool:
        return True

    def extract(
        self,
        *,
        prompt: str,
        document_text: str,
        json_schema: dict,
        temperature: float = 0.0,
    ) -> ExtractionResult:
        del prompt, json_schema, temperature  # 규칙 프로바이더는 프롬프트를 쓰지 않는다
        if self._extractor is None:
            raise LLMUnavailable("RuleProvider 에 추출 함수가 주입되지 않았습니다.")
        sale_spec_text, registry_text = split_documents(document_text)
        out = self._extractor(sale_spec_text, registry_text)
        if isinstance(out, tuple):
            data, evidence = out[0], list(out[1])
        else:
            data, evidence = out, []
        data = dict(data)
        if evidence:
            notes = list(data.get("notes") or [])
            for item in evidence:
                if item not in notes:
                    notes.append(item)
            data["notes"] = notes
        return ExtractionResult(
            data=data,
            provider=self.name,
            model=self.model,
            raw=json.dumps(data, ensure_ascii=False, sort_keys=True),
        )

    def narrate(self, *, prompt: str, context: dict) -> str:
        """Jinja2 템플릿을 결정적으로 렌더링한다(같은 입력 -> 같은 출력)."""
        return assert_no_free_numbers(_render(prompt, context), context)


# ── Anthropic 프로바이더 ─────────────────────────────────────
class AnthropicProvider:
    """Claude 호출. anthropic 패키지는 메서드 안에서 지연 임포트한다."""

    name = "anthropic"

    #: 구조화 추출에 강제로 물리는 도구 이름
    TOOL_NAME = "record_rights_extraction"
    MAX_TOKENS = 16000

    def __init__(self, model: str = DEFAULT_ANTHROPIC_MODEL) -> None:
        self.model = model

    def available(self) -> bool:
        """키가 없거나 패키지가 없으면 False. 절대 예외를 올리지 않는다."""
        if not optional(ENV_API_KEY):
            return False
        try:
            import anthropic  # noqa: F401
        except Exception:  # pragma: no cover - 환경 의존
            return False
        return True

    def _client(self):
        import anthropic

        return anthropic.Anthropic()

    def extract(
        self,
        *,
        prompt: str,
        document_text: str,
        json_schema: dict,
        temperature: float = 0.0,
    ) -> ExtractionResult:
        if not self.available():
            raise LLMUnavailable(
                f"{ENV_API_KEY} 미설정 또는 anthropic 패키지 미설치로 LLM 추출 불가."
            )
        warnings: list[str] = []
        if temperature:
            # Claude Opus 5 계열은 sampling 파라미터를 거부한다(400). 무시하고 기록만 남긴다.
            warnings.append(
                f"temperature={temperature} 는 {self.model} 에서 지원되지 않아 무시되었습니다."
            )
        tool = {
            "name": self.TOOL_NAME,
            "description": "매각물건명세서/등기부에서 추출한 권리 정보를 구조화해 기록한다.",
            "input_schema": json_schema,
        }
        response = self._client().messages.create(
            model=self.model,
            max_tokens=self.MAX_TOKENS,
            system=prompt,
            tools=[tool],
            tool_choice={"type": "tool", "name": self.TOOL_NAME},
            messages=[{"role": "user", "content": document_text}],
        )
        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMUnavailable("모델이 요청을 거부했습니다(stop_reason=refusal).")
        block = next(
            (b for b in response.content if getattr(b, "type", None) == "tool_use"), None
        )
        if block is None:
            raise LLMUnavailable("도구 호출 응답이 없어 구조화 추출에 실패했습니다.")
        data = dict(block.input)
        return ExtractionResult(
            data=data,
            provider=self.name,
            model=self.model,
            raw=json.dumps(data, ensure_ascii=False, sort_keys=True),
            warnings=tuple(warnings),
        )

    def narrate(self, *, prompt: str, context: dict) -> str:
        if not self.available():
            raise LLMUnavailable(
                f"{ENV_API_KEY} 미설정 또는 anthropic 패키지 미설치로 LLM 서술 불가."
            )
        response = self._client().messages.create(
            model=self.model,
            max_tokens=2048,
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False, default=str),
                }
            ],
        )
        text = "".join(
            getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
        )
        return assert_no_free_numbers(text, context)


# ── 선택 ────────────────────────────────────────────────────
def get_provider(*, extractor: Optional[Extractor] = None) -> Provider:
    """환경변수 SAFEBID_LLM_PROVIDER 로 프로바이더를 고른다(기본 rule).

    rule      : 항상 규칙 프로바이더
    anthropic : 사용 불가면 LLMUnavailable
    auto      : 가능하면 anthropic, 아니면 조용히 rule 로 폴백
    """
    raw = (optional(ENV_PROVIDER, "rule") or "rule").strip().lower()
    if raw not in VALID_PROVIDERS:
        logger.warning(
            "%s=%r 는 알 수 없는 값입니다. rule 로 처리합니다(허용: %s).",
            ENV_PROVIDER,
            raw,
            ", ".join(VALID_PROVIDERS),
        )
        raw = "rule"

    model = optional(ENV_MODEL) or DEFAULT_ANTHROPIC_MODEL

    if raw == "rule":
        return RuleProvider(extractor)

    candidate = AnthropicProvider(model)
    if raw == "anthropic":
        if not candidate.available():
            raise LLMUnavailable(
                f"{ENV_PROVIDER}=anthropic 이지만 {ENV_API_KEY} 또는 anthropic 패키지가 없습니다."
            )
        return candidate

    # auto
    if candidate.available():
        return candidate
    logger.info("anthropic 사용 불가 -> 규칙 프로바이더로 폴백합니다.")
    return RuleProvider(extractor)
