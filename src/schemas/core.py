"""src/schemas/core.py — 공통 스키마 SSOT. process.md §4와 동기화 유지.

이 파일의 필드는 동결되어 있다. 기존 필드의 이름·타입·의미 변경 금지.
추가는 Optional 필드로만 가능하며 process.md §4와 §10 변경 로그를 함께 갱신한다.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

DISCLAIMER = (
    "본 분석은 법률·투자 자문이 아니며 정보 제공 목적입니다. "
    "입찰 전 매각물건명세서 원문 확인과 법률 전문가 상담이 필요합니다."
)


# ── 공통 열거형 ──────────────────────────────────────────────
class PropertyType(str, Enum):
    APARTMENT = "APARTMENT"          # 아파트
    OFFICETEL = "OFFICETEL"          # 오피스텔(주거용)
    MULTI_HOUSE = "MULTI_HOUSE"      # 다세대/연립
    DETACHED = "DETACHED"            # 단독/다가구
    COMMERCIAL = "COMMERCIAL"        # 상가/근생
    LAND = "LAND"                    # 토지


class Purpose(str, Enum):
    OWNER_OCCUPY = "OWNER_OCCUPY"    # 실거주
    INVEST = "INVEST"                # 투자(임대)
    BUSINESS = "BUSINESS"            # 사업장 확보(소상공인 확장용)


class RegulationZone(str, Enum):
    SPECULATION = "SPECULATION"      # 투기과열지구
    ADJUSTMENT = "ADJUSTMENT"        # 조정대상지역
    NONE = "NONE"                    # 비규제


class Confidence(str, Enum):
    CONFIRMED = "CONFIRMED"   # 매각물건명세서 등 문서에 명시됨
    LIKELY = "LIKELY"         # 문서 교차 대조로 강하게 추정
    UNCERTAIN = "UNCERTAIN"   # 판단 불가 → 보수적(인수) 가정 적용됨


class RiskGrade(str, Enum):
    A = "A"   # 인수권리 없음, 명도 용이
    B = "B"   # 경미한 리스크(소액/협조적 점유)
    C = "C"   # 상당한 인수금액 또는 불확실성
    D = "D"   # 특수권리(유치권 등) 또는 중대한 불확실성 → 무조건 RED


class Signal(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


# ── 입력 ────────────────────────────────────────────────────
class CaseDocuments(BaseModel):
    """문서 경로. PDF 또는 텍스트(.txt) 추출본. 없으면 None."""

    sale_spec_path: Optional[str] = None      # 매각물건명세서 (A1 필수)
    registry_path: Optional[str] = None       # 등기사항전부증명서
    status_report_path: Optional[str] = None  # 현황조사보고서
    appraisal_path: Optional[str] = None      # 감정평가서


class PropertyCase(BaseModel):
    case_id: str                    # "대전지법-2025타경12345-1" 형식
    court: str                      # "대전지방법원"
    case_number: str                # "2025타경12345"
    item_number: int = 1            # 물건번호
    property_type: PropertyType
    address: str
    region_code: str                # 법정동코드 앞 5자리(시군구). 실거래가 API 키
    regulation_zone: RegulationZone = RegulationZone.NONE
    building_area_m2: Optional[float] = None
    land_area_m2: Optional[float] = None
    built_year: Optional[int] = None
    floor: Optional[int] = None
    appraisal_price: int            # 원
    min_bid_price: int              # 원 (현재 회차 최저가)
    failed_count: int = 0
    sale_date: Optional[date] = None
    deposit_rate: float = 0.10      # 입찰보증금율(재매각 시 0.2~0.3)
    documents: CaseDocuments = Field(default_factory=CaseDocuments)


class UserProfile(BaseModel):
    annual_income: int              # 세전 연소득(원)
    cash_available: int             # 동원 가능 현금(원)
    existing_annual_debt_payment: int = 0   # 기존 대출 연 원리금(원)
    owned_house_count: int = 0
    purpose: Purpose
    age: int
    is_first_time_buyer: bool = False
    target_loan_years: int = 30
    target_region_codes: list[str] = Field(default_factory=list)


# ── A1 출력 ─────────────────────────────────────────────────
class RightType(str, Enum):
    MORTGAGE = "MORTGAGE"                             # (근)저당권
    SEIZURE = "SEIZURE"                               # 압류/가압류
    COLLATERAL_PROV_REG = "COLLATERAL_PROV_REG"       # 담보가등기
    AUCTION_START = "AUCTION_START"                   # 경매개시결정등기
    JEONSE_RIGHT = "JEONSE_RIGHT"                     # 전세권
    TRANSFER_PROV_REG = "TRANSFER_PROV_REG"           # 소유권이전청구권 가등기
    INJUNCTION = "INJUNCTION"                         # 가처분
    LIEN = "LIEN"                                     # 유치권
    STATUTORY_SUPERFICIES = "STATUTORY_SUPERFICIES"   # 법정지상권
    GRAVE_BASE = "GRAVE_BASE"                         # 분묘기지권
    LAND_SEPARATE_REGISTRY = "LAND_SEPARATE_REGISTRY"  # 토지별도등기
    TENANT_DEPOSIT = "TENANT_DEPOSIT"                 # 임차보증금 인수


class BaseRight(BaseModel):
    right_type: RightType
    registered_date: date
    holder: str                     # 마스킹된 권리자명


class AssumedRight(BaseModel):
    right_type: RightType
    description: str                # 사람이 읽을 설명(한국어)
    estimated_cost: int             # 인수 예상 금액(원). 불명이면 보수 추정치
    confidence: Confidence
    evidence: str                   # 근거: "매각물건명세서 비고란: '...'" 형식


class TenantInfo(BaseModel):
    name_masked: str                            # "김○○"
    move_in_date: Optional[date] = None         # 전입일
    fixed_date: Optional[date] = None           # 확정일자
    deposit: Optional[int] = None
    opposing_power: Optional[bool] = None       # None=판단불가
    dividend_demanded: Optional[bool] = None    # 배당요구 여부
    expected_assumed_deposit: int = 0           # 낙찰자 인수 예상 보증금
    confidence: Confidence = Confidence.UNCERTAIN


class RightsAnalysisReport(BaseModel):
    case_id: str
    base_right: Optional[BaseRight] = None
    assumed_rights: list[AssumedRight] = Field(default_factory=list)
    tenants: list[TenantInfo] = Field(default_factory=list)
    total_assumed_cost: int         # Σ(assumed_rights) + Σ(tenants.expected_assumed_deposit)
    eviction_difficulty: Literal["LOW", "MID", "HIGH"]
    risk_grade: RiskGrade
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


# ── A2 출력 ─────────────────────────────────────────────────
class ComparableCase(BaseModel):
    case_id: str
    address_short: str
    sold_price: int
    sold_date: date
    appraisal_ratio: float
    similarity: float               # 0~1


class PriceBandEstimate(BaseModel):
    case_id: str
    p10: int                        # 낙찰가 10분위(원)
    p50: int
    p90: int
    appraisal_ratio_p50: float      # p50/감정가
    method: Literal["MODEL", "HEURISTIC"]
    comparables: list[ComparableCase] = Field(default_factory=list)
    model_version: str = "v0"
    caveats: list[str] = Field(default_factory=list)
    # ── 신규 Optional 필드 (process.md §10 변경 로그 2026-08-03) ──
    # 관측 표본 수. 0이면 시장 관측치가 아니라 사건 구조에서 유도한 사전(prior)이다.
    # method Literal을 변경하지 않고 '측정된 밴드'와 '구조적 사전'을 구별하기 위함.
    sample_size: Optional[int] = None


# ── A3 출력 ─────────────────────────────────────────────────
class BindingConstraint(str, Enum):
    LTV = "LTV"
    DSR = "DSR"
    CASH = "CASH"
    PRODUCT_CAP = "PRODUCT_CAP"
    LOAN_FORBIDDEN = "LOAN_FORBIDDEN"   # 규제로 대출 불가 케이스


class FinancingCeiling(BaseModel):
    case_id: str
    applied_ltv: float
    room_deduction: int
    max_loan_by_ltv: int            # max_bid 시점 기준
    max_loan_by_dsr: int
    max_loan: int
    binding_constraint: BindingConstraint
    max_bid_price: int              # ★ 서비스 핵심 출력: 감당 가능 입찰 상한
    monthly_payment_at_max: int     # max_loan 기준 월 원리금
    assumed_rate: float             # 계산에 쓴 금리(연, config)
    calc_trace: list[str]           # 단계별 계산 로그(감사 가능성)
    config_version: str


# ── A4 출력 ─────────────────────────────────────────────────
class TCOReport(BaseModel):
    case_id: str
    bid_price: int                  # 이 리포트가 가정한 입찰가
    acquisition_tax: int
    local_edu_tax: int
    special_rural_tax: int
    legal_and_bond_fee: int         # 법무+채권할인 간이 추정
    eviction_cost: int
    unpaid_maintenance: int
    repair_reserve: int
    assumed_rights_cost: int        # A1.total_assumed_cost 전달값
    total_upfront_excl_bid: int     # 낙찰가 제외 초기비용 합
    effective_acquisition_price: int  # bid + total_upfront_excl_bid (★핵심 지표)
    breakdown: list[dict]           # [{"label": str, "amount": int, "note": str}]


# ── A5 출력 ─────────────────────────────────────────────────
class ProductMatch(BaseModel):
    product_id: str
    name: str
    category: Literal["POLICY_LOAN", "BANK_LOAN", "GUARANTEE"]
    applicable: bool
    applicable_to_auction: Optional[bool] = None  # 경락자금 용도 가능 여부(불명이면 None)
    max_amount: Optional[int] = None
    rate_note: str = ""
    requirements_met: list[str] = Field(default_factory=list)
    requirements_unmet: list[str] = Field(default_factory=list)
    source_url: str = ""
    checked_date: Optional[date] = None


class PolicyMatchResult(BaseModel):
    case_id: str
    products: list[ProductMatch] = Field(default_factory=list)
    summary_note: str = ""          # LLM 서술 허용(숫자 생성 금지)


# ── 최종 출력 ────────────────────────────────────────────────
class FinalVerdict(BaseModel):
    case_id: str
    signal: Signal
    recommended_max_bid: Optional[int]   # RED이면 None (권리·자금 사유 공통)
    key_numbers: dict                    # p50/max_bid/assumed_cost/
    #                                      effective_price_at_max/monthly_payment
    rationale: str                       # LLM 서술 허용(수치는 치환만)
    warnings: list[str] = Field(default_factory=list)
    report_markdown: str = ""
    disclaimer: str = DISCLAIMER
