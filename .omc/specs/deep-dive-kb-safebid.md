# Spec: SafeBid v1 프로토타입 (deep-dive 산출)

작성: 2026-08-03 · 근거: `.omc/specs/deep-dive-trace-kb-safebid.md` · 원본 SSOT: `process.md`
**제약: 마감 D-1. 프로토타입 완성·제출 최우선. 동작하는 데모 + 그린 테스트 + GitHub push가 성공 조건.**

---

## Goal

`process.md`의 A1~A5 + ORCH 파이프라인을 **오늘 이 PC에서 실제로 실행되는 상태**로 구현하고, Streamlit 데모 3종을 무에러로 완주시킨 뒤 private GitHub 레포에 push한다.

트레이스에서 확정된 BLOCKER 9건을 **구현 시점에 함께 교정**한다. §4 스키마는 `sample_size` Optional 필드 1개 추가를 제외하고 동결.

---

## Non-Goals (v1 스코프 아웃 — 코드 자리는 만들되 실행하지 않음)

- 실제 공공데이터 수집(T1a/T1b) — 수집기 코드는 작성하되 네트워크 테스트 제외
- LightGBM 학습(T3 MODEL 경로) — 코드 경로는 두되 zero-data prior가 기본
- 스캔본 OCR, 배당표 시뮬레이션, 실문서 골든셋 라벨링
- 제안서 PDF/PPTX 산출 (마크다운 초안까지만)

---

## 확정 결정 (Decisions)

| # | 항목 | 결정 |
|---|------|------|
| D1 | fixture vs 공식 | **§T4/§T5 공식이 규범.** fixture 수치를 공식에 맞춰 재설계 |
| D2 | A1 문서추출 | **RuleProvider가 기본**, LLM은 키 있을 때만 승격 (`SAFEBID_LLM_PROVIDER`) |
| D3 | A2 밴드 | **zero-data 구조적 prior가 기본.** `sample_size` 필드로 측정치와 구분 |
| D4 | 검증 | 실문서 대신 **적대적 fixture + held-out case_004** + 골든값 없는 불변식 |
| D5 | 데모 3종 | GREEN(case_001×user_young) / YELLOW(case_002×user_invest) / RED(case_003) |
| D6 | Python | **3.12 고정**, 레포 로컬 `.venv`, `scripts/*.cmd`로만 실행 |
| D7 | 문서 톤 | README는 구현된 것 중심. 미측정 항목은 "로드맵"으로 표기, 거짓 수치 금지 |

---

## 교정 목록 (BLOCKER → 처방)

### C1. 환경 위생 (L2-F1/F2/F3)
- `py -3.12 -m venv .venv` · `requires-python = ">=3.12,<3.13"`
- `scripts/test.cmd`, `scripts/demo.cmd` — `PYTHONUTF8=1` 설정 후 `.venv\Scripts\python.exe -m ...` 직접 호출 (Activate 사용 금지)
- `src/utils/io.py` 단일 IO 게이트: `read_text/read_yaml/read_json/write_text/write_json` 전부 `encoding="utf-8"` + NFKC 정규화 + CRLF→LF. `src/`·`app/`에서 raw `open()` 금지 (정책 테스트로 강제)
- 로그·예외 메시지에 **이모지 금지** (cp949 콘솔에서 프로세스 사망). 이모지는 Streamlit 레이어에서만
- `app/main.py` 최상단에 `sys.path.insert(0, repo_root)` — 다른 import보다 먼저
- `pyproject.toml`에 `[tool.pytest.ini_options] pythonpath = ["."]`
- `.gitattributes`: `*.txt text eol=lf`

### C2. 지연 import (L2-F5/F6)
top-level 허용: pydantic, pyyaml, jinja2, python-dotenv
함수 내부 지연: lightgbm(`_load_model`), pdfplumber(`_pdf_to_text`), anthropic(provider 내부), pandas(통계 경로)
`src/`에서 streamlit·plotly import 절대 금지 → `tests/test_import_hygiene.py`로 강제

### C3. config에서 `null` 제거 (L1-F3)
- `product_cap.default: null` → 로더가 `float("inf")`로 치환
- `eviction_cost.LIEN_CLAIMED: null` → 로더가 `0`으로 치환 + warning "명도비 산정 불가(유치권 주장)"
- `multi_house_none.h2: null` → 기본세율 함수 호출
로더는 `src/utils/config.py`에 단일 진입점. 로딩 직후 `null` 잔존 시 예외.

### C4. LTV 테이블 확장 + 폴백 (L1-F5)
`MULTI_HOUSE`, `DETACHED` 행 추가. `OFFICETEL`/`COMMERCIAL`에 ADJUSTMENT/SPECULATION 추가.
폴백 규칙 정정: **해당 property_type 내 최소값(0.0 제외)** → 없으면 APARTMENT 프로필 적용 + `calc_trace` 경고. "테이블 전역 최소" 규칙 폐기.

### C5. region_map 폴백 (L1-F6 = L2-F9)
미등재 `region_code` → **`SEOUL`(최대 방공제 = 가장 보수적)** 적용 + `calc_trace` + `warnings`.
fixture 지역 3건은 명시 등재.

### C6. 점유유형 파생 규칙 명문화 (L1-F2)
`RightsAnalysisReport`에 필드 추가 없이, A4가 아래 **결정적 순서**로 파생:
```
1) assumed_rights에 LIEN 존재            → LIEN_CLAIMED
2) eviction_difficulty == HIGH           → UNKNOWN_OCCUPANT
3) eviction_difficulty == MID            → TENANT_PARTIAL
4) tenants 비어 있음                      → OWNER_OCCUPIED
5) else                                   → TENANT_FULL_DIVIDEND
```

### C7. risk_grade 재작성 (L1-F4/F11/F21)
**first-match-wins + 기본행 필수**:
```
D: right_type ∈ {LIEN, STATUTORY_SUPERFICIES, GRAVE_BASE, TRANSFER_PROV_REG,
                 INJUNCTION, LAND_SEPARATE_REGISTRY} 존재
   또는 (base_right is None and tenants 존재)
C: total_assumed_cost >= appraisal_price*0.10  또는  uncertain_count >= 2
B: total_assumed_cost < appraisal_price*0.10 and eviction_difficulty != HIGH
   and 특수권리 없음
A: B 조건 + total_assumed_cost == 0 and eviction_difficulty == LOW
else: C  (보수적 폴백, P4)
```
평가 순서: A → B → C → D 를 역순으로, 즉 **D부터 검사**하고 마지막 else C.
`uncertain_count = len([r for r in assumed_rights if r.confidence==UNCERTAIN])
                + len([t for t in tenants if t.confidence==UNCERTAIN and t.expected_assumed_deposit > 0])`

`eviction_difficulty` 복수 조건 시 **더 어려운 쪽 채택(HIGH > MID > LOW)**.

### C8. 금액 불명 특수권리 (L1-F10)
`estimated_cost = 0` 폐기 → `appraisal_price * heuristics.unknown_right_cost_ratio(=0.10)` + `confidence=UNCERTAIN` + warning.

### C9. base_right 동점 결정성 (L3-F6, 실측 재현됨)
`min(candidates, key=lambda r: (r.registered_date, RIGHT_PRIORITY[r.right_type], r.holder))`
`RIGHT_PRIORITY = {MORTGAGE:0, COLLATERAL_PROV_REG:1, SEIZURE:2, AUCTION_START:3, JEONSE_RIGHT:4}`
근거: §2.2(b) "같은 날이면 저당권이 우선". 후보 수집은 `set`이 아니라 `list` + 정렬.

### C10. A3 시그니처 정정 (L1-F7)
`run(case, user, rights, cost_fn, cfg) -> FinancingCeiling` — `cfg` 인자 추가(P7 준수).

### C11. decide() 정정 (L1-F8/F9/F13/F14)
```python
def decide(rights, band, financing, min_bid_price) -> tuple[Signal, Optional[int]]:
    if rights.risk_grade == RiskGrade.D:
        return Signal.RED, None
    if financing.max_bid_price < min_bid_price:      # 신규: 법정 최저가 미만
        return Signal.RED, None
    if band is not None:
        if financing.max_bid_price < band.p10:  return Signal.RED, None
        sig = Signal.YELLOW if financing.max_bid_price < band.p50 else Signal.GREEN
    else:
        sig = Signal.YELLOW
    if rights.risk_grade == RiskGrade.C and sig == Signal.GREEN:
        sig = Signal.YELLOW
    rec = financing.max_bid_price      # p90 캡 제거 (P1 준수 — 밴드는 참고정보일 뿐)
    return sig, rec
```
`key_numbers` 5키 고정: `p50`(band 없으면 `None`), `max_bid`, `assumed_cost`, `effective_price_at_max`, `monthly_payment`.

### C12. 10만원 내림 하한 (L1-F16)
내림 결과가 `min_bid_price` 미만이면 `max_bid_price = 0`, `binding_constraint = CASH`, warning.

### C13. `vacancy_months_formula` 문자열 제거 (L1-F20)
`vacancy_months: {per_fail: 2, base: 6, cap: 36}` → `min(cap, failed_count*per_fail + base)`. eval 금지.

### C14. A2 zero-data prior (L2-F8) — `method="HEURISTIC"` 유지
```
n>=1 and 0<L<A : step = (L/A)**(1/n);  upper = ceil_unit(L/step)
n>=1 else      : upper = ceil_unit(L/(1-discount_per_fail))
n==0           : upper = max(L, A)
p10 = floor_unit(L);  p90 = max(p10, upper);  p50 = floor_unit((p10+p90)/2)
sample_size = 0
```
`caveats`에 "관측 0건 · 구조적 사전구간 · 시장 관측치 아님 · 참고 정보" 명시.
`sample_size == 0`이면 UI에서 점선 + "참고용 구조 추정(관측 0건)" 라벨.

### C15. 금지어 가드 재작성 (L3-F3, 실측 미탐 8/9)
`src/utils/guard.py`:
- `normalize()`: NFKC + **모든 공백/개행 제거** + lower
- 서브스트링 집합 + 어간 정규식 `낙찰[가-힣]{0,3}(확률|가능성)`, `(확률|가능성)[은는이가]?\s*\d+\s*%`, `이\s*금액이면`
- **긍정 단언**: 밴드 언급 블록에 "참고 정보"/"참고용" 없으면 실패
- 적용 범위: `report_markdown`만이 아니라 **FinalVerdict 전체를 재귀 순회**(모든 str/list[str]/dict 값) + `app/**/*.py` 문자열 리터럴

### C16. P5 하드코딩 검사 교체 (L3-F2, 실측 오탐3/미탐11)
grep 폐기. 대신 2단:
- `tests/test_p5_ast.py` — `src/agents/financing`, `src/agents/tco`의 숫자 리터럴 ⊆ `{0,1,2,12,100,-1}`
- `tests/test_p5_sensitivity.py` — 규제 키를 섭동하면 출력이 **반드시** 변함

### C17. 부가세율 정합 (L1-F15)
`local_edu_tax`를 낙찰가 정률이 아니라 **본세율 연동**으로. `commercial` 실효 4.6% 맞추기.

---

## Fixture 설계 (D1 반영 — 아래 수치는 §T4 공식으로 검산 완료)

### 공통 감당상한 공식
```
max_bid = (cash − room_deduction − 정액비용 − 인수권리비용) / (1 + 비용비율 − ltv)
비용비율 = 취득세율 + 지방교육세 + 농특세 + 법무채권0.004 + 수선충당0.01
```

### 유저 3종
| id | 연소득 | 현금 | 보유주택 | 목적 | 나이 | 비고 |
|----|--------|------|----------|------|------|------|
| `user_young` | 42,000,000 | **80,000,000** | 0 | OWNER_OCCUPY | 28 | 무주택 생애최초 |
| `user_invest` | 90,000,000 | 200,000,000 | 2 | INVEST | 45 | |
| `user_tight` | 30,000,000 | 20,000,000 | 0 | OWNER_OCCUPY | 26 | |

### 케이스 4종
| id | 물건 | 지역 | 감정가 | 최저가(유찰) | 핵심 | 기대 등급 |
|----|------|------|--------|--------------|------|-----------|
| `case_001_clean` | 아파트 59.8㎡ | 대전 서구(30170, METRO) | 160,000,000 | 112,000,000 (1회·30%) | 소유자 점유, 근저당 1건 | **A** |
| `case_002_tenant` | 다세대 45㎡ | 인천 부평(28237, OVERCROWDED) | 180,000,000 | 126,000,000 (1회·30%) | 임차인 전입 < 근저당, 배당요구 X, 보증금 60,000,000 | **C** |
| `case_003_lien` | 근생 상가 | 대전 유성(30200, METRO) | 300,000,000 | 210,000,000 (1회·30%) | 유치권 신고 80,000,000 | **D** |
| `case_004_noise` | (held-out) | = case_002와 **의미 동일, 표기 전부 다름** | | | 파서 개발 중 열람 금지 | **C** |

### 검산 — 데모 3종
```
[GREEN] case_001 × user_young
  ltv 0.70(APARTMENT/NONE/house_0_1), 방공제 28,000,000(METRO)
  정액 = 명도 1,500,000 + 관리비 3000×59.8×8×0.6 = 861,120  → 2,361,120
  비용비율 = 0.01+0.001+0.002+0.004+0.01 = 0.027
  max_bid = (80,000,000 − 28,000,000 − 2,361,120) / (1+0.027−0.70)
          = 49,638,880 / 0.327 ≈ 151,800,000
  band(prior): p10 112,000,000 · p50 136,000,000 · p90 160,000,000
  151,800,000 ≥ p50 → GREEN ✓  (risk A → 하향 없음)

[YELLOW] case_002 × user_invest
  ltv 0.60(MULTI_HOUSE/NONE/house_2plus), 방공제 48,000,000(OVERCROWDED)
  인수보증금 60,000,000, 정액 = 명도 3,000,000 + 관리비 3000×45×8×0.6=648,000
  max_bid = (200,000,000 − 48,000,000 − 3,648,000 − 60,000,000) / (1+0.027−0.60)
          = 88,352,000 / 0.427 ≈ 206,900,000
  band(prior): p10 126,000,000 · p50 153,000,000 · p90 180,000,000
  206,900,000 ≥ p50 → GREEN → risk C 하향 → YELLOW ✓

[RED] case_003 × 아무 유저
  유치권 → risk D → RED, rec=None ✓  (자금 계산과 무관하게 우선)

[보너스] case_002 × user_young → 방공제 48,000,000 + 인수 60,000,000에 현금 8천만이 못 미침
  → max_bid = 0 → RED(자금). "같은 물건, 청년은 불가 / 투자자는 조건부 가능" 대비 장면
```

---

## Acceptance Criteria

1. `scripts\test.cmd` 전체 그린 — 예외 0, skip 사유 명시
2. `scripts\demo.cmd` 로 Streamlit 기동, 데모 3종 무에러 완주
3. 불변식 테스트 **최소 25개** 통과 (I1~I50 중 핵심): 상한 최대성 · f 단조감소 · 비음수 · LOAN_FORBIDDEN 일관성 · TCO 합계 정합 · breakdown 합계 · 실질취득가 정의 · 인수비용 pass-through · 취득세 경계 연속성 · 인수총액 정합 · 대항력 strict 경계 · 보수성 강제(P4) · 특수권리⇒D · base_right 동점 결정성 · 마스킹(P8) · 추천 상하한 · D등급 전파 · key_numbers 완전성 · 면책 렌더 · 금지어/수식어 · 밴드 정렬 · 폴백 불파괴 · P5 AST · P5 민감도 · LLM 미의존(P2) · 크로스프로세스 결정성 · 전 조합 스모크
4. `case_004_noise`(held-out)가 파서 수정 없이 case_002와 **동일한 판정** 산출
5. 모든 사용자 노출 문자열이 금지어 가드 통과 + 밴드 언급 시 "참고 정보" 동반
6. GitHub `Longarden/kb-auction-agent` private 레포에 push 완료
7. `README.md`에 VSCode 실행 방법(venv 생성 → test → demo) 명시

---

## Technical Context

- Python 3.12 · pydantic v2 · pyyaml · jinja2 · python-dotenv · requests · streamlit<2 · plotly · pandas<3 · numpy · pyarrow
- extras: `ml`(lightgbm, scikit-learn) · `pdf`(pdfplumber) · `llm`(anthropic) · `dev`(pytest)
- `.venv` 레포 로컬. 전역 Python312/conda에 절대 설치하지 않음(사용자 HF/torch 환경 보호)

---

## Assumptions Exposed

- 규제 수치는 전부 **예시값(`verified: false`)**. 데모에서 "규제값 미확인" 경고를 강제 표시
- 서울/인천 규제지역 지정 여부 미확인 → 전부 `NONE`으로 두고 §9에 기록
- 인수보증금은 대출로 커버되지 않는 **순현금 지출**로 모델링 (경락잔금대출은 낙찰가 기준)
- fixture 문서는 대법원 매각물건명세서 서식을 모사하되 실문서가 아님 → 정확도 주장 근거로 쓰지 않음

---

## Trace Findings

`.omc/specs/deep-dive-trace-kb-safebid.md` 참조. 요약:
- L1(스펙 모순) 22건 · BLOCKER 5 — 대표 데모가 산술적으로 성립 불가
- L2(외부 의존) **반증** — lightgbm/streamlit/plotly 실기동 확인. 진짜 블로커는 인코딩·sys.path·인터프리터 분열
- L3(검증 붕괴) 실측 확정 — §7.2 grep 오탐3/미탐11, §7.4 금지어 미탐 8/9, `PYTHONHASHSEED`로 base_right 실제 변동
- 3중 교차 확인: region_map KeyError · 저감률 30% vs 20% · 자기채점 순환
