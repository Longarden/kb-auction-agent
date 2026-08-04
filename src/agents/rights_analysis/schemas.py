"""A1 모듈 내부 중간 스키마.

src/schemas/core.py 는 동결된 대외 계약이고, 여기 정의는 A1 안에서만 쓰는
'프로바이더 출력 -> 규칙 엔진 입력' 계약이다. 규칙/LLM 두 프로바이더가
모두 이 모양을 만들어 내므로, rules.py 는 출처를 알 필요가 없다.

날짜는 전부 ISO 문자열("YYYY-MM-DD") 또는 None 이다. LLM 이 만들어 낸
자유 형식 날짜를 파서 단계에서 이미 정규화했다는 뜻이다.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RawBaseRightHint(BaseModel):
    """매각물건명세서 '최선순위 설정' 란. 법원이 스스로 적어 준 말소기준권리."""

    right_type: Optional[str] = None
    registered_date: Optional[str] = None
    evidence: str = ""


class RawRightRow(BaseModel):
    """등기부 갑구/을구 한 줄."""

    right_type: str
    registered_date: Optional[str] = None
    holder: str = ""


class RawTenantRow(BaseModel):
    """점유자 표 한 레코드. 이름은 이미 마스킹된 상태여야 한다(P8)."""

    name_masked: str
    move_in_date: Optional[str] = None
    fixed_date: Optional[str] = None
    deposit: Optional[int] = None
    dividend_demanded: Optional[bool] = None
    evidence: str = ""


class RawSpecialRight(BaseModel):
    """비고란/소멸되지 않는 권리 란에서 잡아낸 특수권리."""

    right_type: str
    claimed_amount: Optional[int] = None
    evidence: str = ""


class RawExtraction(BaseModel):
    """프로바이더 출력의 수렴 지점. run.py 가 여기서 한 번만 검증한다."""

    base_right_hint: Optional[RawBaseRightHint] = None
    rights: list[RawRightRow] = Field(default_factory=list)
    tenants: list[RawTenantRow] = Field(default_factory=list)
    special_rights: list[RawSpecialRight] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def extraction_json_schema() -> dict:
    """LLM tool-use 에 강제로 물릴 JSON 스키마(도구 입력 스키마)."""
    schema = RawExtraction.model_json_schema()
    schema.setdefault("type", "object")
    return schema
