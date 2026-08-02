# process.md — KB AI Challenge: 경매 안전입찰 에이전트 (가칭 SafeBid)

> **이 문서는 프로젝트의 단일 진실 원천(SSOT)이다.**
> 어떤 모델/개발자든 이 문서만 읽고 태스크 하나를 집어 독립적으로 구현한 뒤,
> 다른 사람의 결과물과 병합했을 때 인터페이스·방향성 충돌이 없도록 작성되었다.

---

## 0. 문서 사용 규칙 (구현 시작 전 반드시 읽을 것)

1. **우선순위**: 충돌 시 `§4 공통 스키마` > `§1.3 불변 원칙` > `§6 태스크 상세` 순으로 우선한다.
2. **스키마는 수정 금지.** 필드 추가가 꼭 필요하면 `Optional` 필드로만 추가하고, 이 문서의 §4를 함께 갱신한 뒤 변경 내역을 §10 변경 로그에 기록한다. 기존 필드의 이름·타입·의미 변경은 금지.
3. 모든 모듈은 `src/schemas/core.py`를 **import해서** 사용한다. 스키마를 자기 모듈에 복사해서 재정의하지 않는다.
4. 각 태스크는 §6에 정의된 **입력/출력/DoD(완료 조건)** 를 그대로 따른다. DoD를 통과하지 못한 코드는 병합하지 않는다.
5. 미결정 사항을 발견하면 임의로 결정하지 말고 §9 미결정 사항 목록에 추가하거나, 이미 목록에 있으면 거기 적힌 기본값(보수적 가정)을 따른다.
6. 이 문서에 등장하는 **모든 규제 수치(LTV, 방공제, 취득세율 등)는 "예시값"** 이다. 실제 값은 `config/regulation.yaml`에만 존재하며, 구현자는 데모 전까지 최신 값으로 검증하고 `verified: true` + 근거 URL을 기록해야 한다.

---

## 1. 프로젝트 개요

### 1.1 한 줄 정의

**"부동산 경매 참가자(특히 청년 실수요자·경매 초보자)에게 ① 물건의 숨은 인수 리스크(권리분석)를 자동 분석하고, ② 본인의 소득·자산·규제 기준으로 감당 가능한 입찰 상한선을 계산하며, ③ 경락잔금대출·정책금융을 매칭해주는 KB 금융 에이전트"**

### 1.2 배경과 KB 연결 고리 (제안서 스토리의 뼈대)

- 경매는 시세보다 저렴하게 부동산을 취득하는 경로지만, 초보자는 두 가지에서 실패한다:
  - **권리분석 실패**: 대항력 있는 임차인 보증금, 유치권 등 "낙찰가에 안 보이는 인수 금액" 때문에 실질 취득가가 급등.
  - **자금 계획 실패**: 낙찰 후 대금지급기한(통상 40~60일)이 짧은데, 대출 한도를 모른 채 입찰해 잔금 미납 → 보증금 몰수.
- KB의 강점과 정확히 겹친다:
  - KB부동산 시세는 담보 감정의 사실상 표준.
  - **경락잔금대출**은 KB의 실제 상품이며, 낙찰자는 "즉시 대출 수요가 확정된 고객"이다.
  - 위험 물건을 걸러주는 것은 은행의 여신 건전성과도 일치(금융소비자 보호).
- 공모전 주제 매핑: **2번(AI 데이터 기반 최적 입지 컨설팅, 메인)** + 1번(청년 주거 금융) + 4번(금융 소비자 보호)을 하나의 서비스로 묶는다.
  - 스토리 한 줄: *"청년이 경매로 첫 집을 마련할 때, 위험한 물건은 걸러주고, 감당 가능한 금액을 알려주고, 잔금 대출까지 이어주는 KB 에이전트."*

### 1.3 불변 원칙 (P1~P8) — 모든 태스크에 강제 적용

| ID | 원칙 |
|----|------|
| **P1** | 서비스의 1차 출력은 **"감당 가능 입찰 상한(affordability ceiling)"** 이다. "낙찰 확률을 높이는 입찰가"는 계산하지도, 표현하지도 않는다. 예상 낙찰가 밴드는 **참고 정보**로만 표시한다. |
| **P2** | 금액 계산 모듈(A3 자금상한, A4 TCO)에는 **LLM을 절대 사용하지 않는다.** 결정적(deterministic) 룰 엔진만 사용한다. |
| **P3** | **LLM은 숫자를 생성하지 않는다.** 최종 리포트의 모든 수치는 스키마 필드 값을 템플릿 변수 치환으로 삽입한다. LLM은 서술문만 작성한다. |
| **P4** | 권리분석에서 판단이 불확실하면 **보수적으로(인수된다고) 가정**하고 `confidence=UNCERTAIN`으로 표시한다. 낙관적 가정 금지. |
| **P5** | 규제 수치는 `config/regulation.yaml`에서만 읽는다. **소스코드 내 규제 수치 하드코딩 금지.** |
| **P6** | 모든 사용자 노출 리포트에 면책 고지를 포함한다: *"본 분석은 법률·투자 자문이 아니며, 매각물건명세서 원문과 전문가 확인이 필요합니다."* |
| **P7** | 모든 에이전트 모듈은 `run(input) -> output` 형태의 **순수 함수**로 구현한다(데이터 수집기 제외). 전역 상태, 파일 사이드이펙트 금지. |
| **P8** | 임차인 성명 등 개인정보는 수집·저장 단계에서 마스킹한다(예: "김○○"). |

### 1.4 금지 사항 (안티패턴) — 코드 리뷰에서 즉시 리젝

- "이 금액이면 낙찰됩니다", "낙찰 확률 87%" 류의 표현·기능. (금지어 검사 목록: §7.4)
- **응찰자 수** 등 매각기일 이후에만 알 수 있는 사후 정보를 낙찰가 예측 피처로 사용하는 것(타겟 누수).
- 매각물건명세서/등기부 원문 없이 주소만으로 권리분석 결과를 `CONFIRMED`로 표시하는 것.
- 스키마 필드의 임의 변경, 다른 모듈 내부 함수 직접 호출(공개 `run()` 외).
- 데모 UI에서 라이브 크롤링(안정성 문제). 데모는 `data/samples/`의 사전 수집 케이스만 사용.

---

## 2. 도메인 지식 요약 — 구현자가 알아야 할 최소한

> 이 절의 내용은 구현 로직의 근거다. 여기 정의된 규칙과 다른 로직을 짜지 말 것.

### 2.1 법원경매 절차와 타임라인

```
경매개시결정 → 배당요구종기 → 감정평가·최저매각가격 결정
→ 매각기일(기일입찰: 최저가 이상 응찰, 입찰보증금 = 최저가의 10%)
→ 최고가매수신고인 선정 → 매각허가결정(약 7일) → 허가 확정(약 7일)
→ 대금지급기한 지정(통상 확정 후 1개월 내) → 잔금 납부 → 소유권이전 + 배당
→ (점유자 있으면) 인도명령 신청 → 명도
```

- 유찰 시 다음 기일 최저매각가격이 **20~30% 저감**된다(법원별 상이 — config로 관리).
- 잔금 미납 시 **입찰보증금 몰수**, 재매각(재매각 시 보증금 20~30%로 상향).
- **서비스 훅**: 낙찰~잔금까지 통상 40~60일. 이 짧은 기간에 대출이 필수 → "낙찰 전 미리 한도를 아는 것"이 서비스의 핵심 가치이자 KB의 리드 확보 시점.

### 2.2 권리분석 핵심 규칙 (A1 모듈의 판정 로직 근거)

**(a) 말소기준권리(base_right)**
등기부상 아래 유형 중 **등기 일자가 가장 빠른 것**:
- (근)저당권, (가)압류, 담보가등기, 강제경매개시결정등기
- 선순위 전세권 중 **배당요구를 했거나 경매를 신청한** 전세권

→ 말소기준권리보다 **후순위** 등기 권리는 낙찰로 소멸, **선순위** 권리는 낙찰자가 인수.

**(b) 임차인 대항력(opposing_power)**
- 성립 요건: 주택의 인도(점유) + 전입신고 → **전입 다음 날 0시**에 효력 발생.
- 판정: `전입일 < 말소기준권리 등기일` 이면 대항력 있음. (같은 날이면 저당권이 우선 → 대항력 없음. 반드시 strict inequality로 구현.)
- 대항력 있는 임차인이 ① 배당요구를 하지 않았거나 ② 배당으로 보증금을 전액 회수하지 못하면 → **미회수 보증금을 낙찰자가 인수**.
- 확정일자 = 우선변제권(배당 순위)의 기준. 대항력과는 별개 개념이므로 필드를 분리한다.

**(c) 말소기준과 무관하게 항상 인수 가능성이 있는 특수 권리** (발견 시 무조건 경고 + 등급 하향)
- 유치권(신고 여부와 무관하게 주장 가능), 법정지상권 성립 여지, 분묘기지권, 선순위 처분금지가처분·소유권이전청구권 가등기, 토지별도등기, 대지권 미등기.

**(d) 기타 인수성 비용**
- 미납 공용관리비: 판례상 **공용부분은 낙찰자 부담**(소멸시효 3년). 전유부분은 부담 없음.
- 명도 비용: 점유자 유형에 따라 상이 (§6 T5 휴리스틱 표).

**(e) 문서 신뢰 우선순위** (A1이 문서 간 정보가 충돌할 때 따를 순서)
1. **매각물건명세서** (법원 작성. "매각으로 소멸되지 않는 권리", "임차인 현황"이 여기 기재됨 — 최우선 신뢰)
2. 등기사항전부증명서
3. 현황조사보고서 (집행관 작성)
4. 감정평가서
- 충돌 시 명세서 기준으로 판정하되 `warnings`에 충돌 사실을 기록한다.

### 2.3 경락잔금대출 구조 (A3 모듈의 계산 근거)

```
대출한도 ≈ min(
    LTV × min(낙찰가, 감정가[또는 KB시세]) − 방공제,
    DSR 한도로 역산한 원금,
    상품별 최대 한도
)
```

- **방공제(room_deduction)**: 주택임대차보호법상 소액임차인 최우선변제금액을 한도에서 차감. 지역별 상이. MCI/MCG 보증 가입 시 면제 가능하나 **v1은 보수적으로 방공제 적용을 기본값**으로 한다.
- **DSR**: `(기존 대출 연 원리금 + 신규 대출 연 원리금) / 연소득 ≤ 한도(은행권 통상 40%)`. 스트레스 금리 가산 제도는 변동이 잦으므로 config 파라미터(`stress_rate_addon`)로 두고, 값 미확인 시 0으로 두되 `verified: false` 유지.
- 규제지역(투기과열/조정대상)·보유주택수에 따라 LTV 차등 및 대출 불가 케이스 존재 → 전부 config 테이블 조회로 처리.

### 2.4 취득 부대비용 (A4 모듈의 계산 근거)

낙찰가 외 초기 비용: 취득세(+지방교육세, 농어촌특별세), 법무 비용, 국민주택채권 매입 할인비용, 명도비, 미납관리비, 수선충당금, 대출이자(보유기간).

- 취득세 골격(예시값 — config 관리, 기준일 명시 필수):
  - 1주택: 6억 이하 1% / 6~9억 구간 슬라이딩(`세율% = 취득가 × 2/3억 − 3`) / 9억 초과 3%
  - 다주택 중과: 조정대상지역 2주택 8%, 3주택 이상 12% (비조정은 완화) — 정책 변동이 매우 잦은 영역이므로 **반드시 최신 확인 후 config 기록**
  - 비주거(상가 등): 4% (지방교육세·농특세 포함 실효 약 4.6%)

### 2.5 용어집 — 코드 네이밍 표준 (전 모듈 공통, 다른 이름 사용 금지)

| 한국어 | 코드 표준 명칭 | 비고 |
|---|---|---|
| 말소기준권리 | `base_right` | |
| 대항력 | `opposing_power` | |
| 인수되는 권리 | `assumed_right` | |
| 방공제(소액임차 최우선변제 공제) | `room_deduction` | |
| 감정가 | `appraisal_price` | 원 단위 int |
| 최저매각가격 | `min_bid_price` | 원 단위 int |
| 과거 사건의 낙찰가 | `sold_price` | 학습 데이터용 |
| 사용자의 입찰(예정)가 | `bid_price` | |
| 낙찰가율 | `appraisal_ratio` | `sold_price / appraisal_price` |
| 유찰 횟수 | `failed_count` | |
| 명도 | `eviction` | |
| 경락잔금대출 | `auction_balance_loan` | |
| 배당요구 | `dividend_demand` | |
| 확정일자 | `fixed_date` | |
| 전입일 | `move_in_date` | |
| 총소유비용 | `TCO` | |
| 감당 가능 입찰 상한 | `max_bid_price` | 서비스의 핵심 출력 |

- 금액은 **전부 원(KRW) 단위 정수(int)**. 만원/억 단위 변환은 UI 레이어에서만.
- 날짜는 `datetime.date`, 직렬화는 ISO-8601(`YYYY-MM-DD`).

---

## 3. 시스템 아키텍처

### 3.1 파이프라인

```
[입력]
  UserProfile (소득/현금/보유주택/목적/연령)
  PropertyCase (사건번호, 감정가, 최저가, 문서 4종)
     │
     ▼
┌─────────────────────────────────────────────────────┐
│ A1 rights_analysis  권리분석 (LLM 추출 + 룰 검증)      │
│   → RightsAnalysisReport (인수권리, 인수총액, 등급)    │
└─────────────────────────────────────────────────────┘
     │                          ┌──────────────────────┐
     │        (병렬 실행 가능)    │ A2 price_band         │
     │                          │  낙찰가 밴드 (ML/통계)  │
     │                          │  → PriceBandEstimate  │
     │                          └──────────────────────┘
     ▼
┌─────────────────────────────────────────────────────┐
│ A3 financing  자금조달 상한 (순수 룰 엔진, LLM 금지)    │
│   A4의 cost_function을 호출하며 이분탐색으로 상한 도출  │
│   → FinancingCeiling (max_bid_price)                │
└─────────────────────────────────────────────────────┘
     ▼
┌─────────────────────────────────────────────────────┐
│ A4 tco  총소유비용 (순수 룰 엔진, LLM 금지)            │
│   → TCOReport (입찰가 기준 비용 브레이크다운)          │
└─────────────────────────────────────────────────────┘
     ▼
┌─────────────────────────────────────────────────────┐
│ A5 policy_match  정책·상품 매칭 (룰 필터 + LLM 설명)   │
│   → PolicyMatchResult                               │
└─────────────────────────────────────────────────────┘
     ▼
┌─────────────────────────────────────────────────────┐
│ ORCH orchestrator  종합 판정 + 리포트 생성            │
│   신호등(GREEN/YELLOW/RED), recommended_max_bid      │
│   → FinalVerdict                                    │
└─────────────────────────────────────────────────────┘
```

### 3.2 모듈 책임 분리

| 모듈 | 패키지 | LLM | 책임 | 하지 않는 것 |
|---|---|---|---|---|
| A1 | `src/agents/rights_analysis` | **허용** (추출) | 문서 파싱 → 인수권리·임차인·등급 판정 | 금액 예측, 대출 계산 |
| A2 | `src/agents/price_band` | **금지** | 낙찰가 p10/p50/p90 밴드 + 유사사례 | "권장 입찰가" 산출 |
| A3 | `src/agents/financing` | **금지** | LTV/DSR/방공제 기반 max_bid_price | 문서 해석 |
| A4 | `src/agents/tco` | **금지** | 입찰가 함수형 비용 계산 `cost_function(bid, ...)` | 대출 한도 판단 |
| A5 | `src/agents/policy_match` | 설명문만 허용 | 자격 룰 필터 + 상품 카드 | 자격 판정에 LLM 사용 |
| ORCH | `src/orchestrator` | 서술문만 허용 | 실행 순서, 실패 처리, 신호등, 리포트 | 자체 수치 계산 |

**의존 방향(순환 금지)**: `ORCH → {A1..A5}`, `A3 → A4.cost_function`. 그 외 에이전트 간 직접 의존 금지. 에이전트 간 데이터 전달은 반드시 ORCH를 경유해 스키마 객체로만.

### 3.3 기술 스택 (고정 — 변경하려면 §10에 사유 기록)

- Python **3.11+**, Pydantic **v2**
- ML: **LightGBM**(quantile regression), scikit-learn, pandas
- 문서 파싱: **pdfplumber**(텍스트 PDF 기본), PyMuPDF(보조). **스캔본 OCR은 v1 스코프 아웃** — 텍스트 추출 가능한 문서만 사용하고, 추출 실패 시 사람이 붙여넣은 텍스트 파일(.txt)을 입력으로 받는 우회 경로 제공.
- LLM: provider 중립 추상화 `src/llm/client.py` 하나만 통해 호출. JSON/structured output 강제, `temperature=0`. 프롬프트는 코드에 인라인 금지, `src/agents/<name>/prompts/*.md` 파일로 분리.
- 데모 UI: **Streamlit** (공모전 데모 속도 우선. React 전환은 v2)
- 저장: 파일 기반(parquet/json/yaml) + SQLite. 외부 DB·클라우드 의존 금지(PoC 재현성).
- 테스트: pytest. 시각화: plotly(Streamlit 내장 호환).

### 3.4 디렉토리 구조 (고정)

```
kb-auction-agent/
├── process.md                  # 본 문서 (SSOT)
├── pyproject.toml
├── config/
│   ├── regulation.yaml         # 규제 파라미터 (§5)
│   └── heuristics.yaml         # 명도비/관리비 등 휴리스틱 (§5)
├── data/
│   ├── raw/                    # 수집 원본 (git 제외)
│   ├── processed/              # 정규화 산출물
│   ├── samples/                # 데모·fixture용 케이스
│   │   ├── case_001_clean/     #   각 케이스 폴더 구조는 §6 T0 참조
│   │   ├── case_002_tenant/
│   │   └── case_003_lien/
│   └── policies.yaml           # 정책·상품 DB (§6 T6)
├── src/
│   ├── schemas/core.py         # 공통 스키마 (§4) — SSOT
│   ├── llm/client.py
│   ├── collectors/             # T1 수집기 (onbid.py, molit_trades.py, manual_court.py)
│   ├── agents/
│   │   ├── rights_analysis/    # A1: run.py, parser.py, rules.py, prompts/
│   │   ├── price_band/         # A2: run.py, features.py, train.py, heuristic.py
│   │   ├── financing/          # A3: run.py, ltv.py, dsr.py, solver.py
│   │   ├── tco/                # A4: run.py, cost_function.py, tax.py
│   │   └── policy_match/       # A5: run.py, eligibility.py
│   ├── orchestrator/           # run.py, verdict.py, report.py, templates/
│   └── utils/
├── app/main.py                 # Streamlit 데모 (T8)
├── tests/
│   ├── fixtures/               # 기대 출력 JSON (T0 산출)
│   ├── test_schemas.py
│   ├── test_rights.py / test_financing.py / test_tco.py / ...
│   └── test_e2e.py
├── notebooks/                  # A2 학습·백테스트 노트북
└── docs/proposal/              # T10 제안서·발표자료
```

### 3.5 모듈 공통 규약

- 각 에이전트 패키지는 `run()` 단일 공개 진입점을 `__init__.py`에서 export. 시그니처는 §6 각 태스크에 명시된 것을 그대로 사용.
- 예외는 `src/utils/errors.py`의 `AgentError(module: str, reason: str, recoverable: bool)`로 통일. ORCH가 잡아서 §6 T7의 실패 정책대로 처리.
- 로깅은 표준 `logging`, 로거 이름 = 모듈 경로. `print()` 금지.
- A3/A4는 **결정성 테스트 필수**: 동일 입력 2회 호출 결과 완전 일치.
- 난수 사용 시 seed 고정(42).

---

## 4. 공통 데이터 스키마 (SSOT) — `src/schemas/core.py`

> 아래 코드가 원본이다. 그대로 파일로 옮겨 구현하고, 모든 모듈은 이것을 import한다.

```python
"""src/schemas/core.py — 공통 스키마 SSOT. process.md §4와 동기화 유지."""
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
    MORTGAGE = "MORTGAGE"                          # (근)저당권
    SEIZURE = "SEIZURE"                            # 압류/가압류
    COLLATERAL_PROV_REG = "COLLATERAL_PROV_REG"    # 담보가등기
    AUCTION_START = "AUCTION_START"                # 경매개시결정등기
    JEONSE_RIGHT = "JEONSE_RIGHT"                  # 전세권
    TRANSFER_PROV_REG = "TRANSFER_PROV_REG"        # 소유권이전청구권 가등기
    INJUNCTION = "INJUNCTION"                      # 가처분
    LIEN = "LIEN"                                  # 유치권
    STATUTORY_SUPERFICIES = "STATUTORY_SUPERFICIES"# 법정지상권
    GRAVE_BASE = "GRAVE_BASE"                      # 분묘기지권
    LAND_SEPARATE_REGISTRY = "LAND_SEPARATE_REGISTRY" # 토지별도등기
    TENANT_DEPOSIT = "TENANT_DEPOSIT"              # 임차보증금 인수

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
    total_assumed_cost: int         # Σ(assumed_rights.estimated_cost) + Σ(tenants.expected_assumed_deposit)
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
    # 신규 Optional 필드 (2026-08-03, §10 변경 로그 참조)
    # 관측 표본 수. 0이면 시장 관측치가 아니라 사건 구조에서 유도한 사전(prior)이다.
    # method Literal을 바꾸지 않고 '측정된 밴드'와 '구조적 사전'을 구별하기 위함.
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
    recommended_max_bid: Optional[int]   # RED(권리 사유)면 None
    key_numbers: dict                    # {"p50":..,"max_bid":..,"assumed_cost":..,"effective_price_at_max":..}
    rationale: str                       # LLM 서술 허용(수치는 치환만)
    warnings: list[str] = Field(default_factory=list)
    report_markdown: str = ""
    disclaimer: str = DISCLAIMER
```

**직렬화 규약**: 모듈 간 파일 교환이 필요하면 `model_dump_json(indent=2)` / `Model.model_validate_json()`만 사용. dict 수동 조립 금지.

---

## 5. 설정 파일 규격

### 5.1 `config/regulation.yaml`

> ⚠️ **아래 수치는 전부 예시값이다.** 정책이 자주 바뀌는 영역(LTV/DSR/취득세 중과/방공제)이므로,
> 구현자는 실장 전 최신 값을 확인해 `verified: true`, `verified_date`, `source_url`을 채워야 한다.
> `verified: false` 상태로 데모 금지. 코드에서는 이 파일의 값만 읽는다(P5).

```yaml
version: "2026-08-02"
verified: false            # 최신 확인 완료 시 true
verified_date: null
sources: []                # 확인에 사용한 근거 URL 목록

loan:
  assumed_rate_annual: 0.045      # 월상환 계산용 가정 금리(예시)
  dsr_limit: 0.40                 # 은행권 DSR 한도(예시)
  stress_rate_addon: 0.0          # 스트레스 DSR 가산(미확인 시 0 + verified false 유지)
  product_cap:                    # 상품 최대한도(원). null=한도 없음 가정
    default: null

ltv:                              # 적용 LTV 테이블 (예시값)
  APARTMENT:
    NONE:        {house_0_1: 0.70, house_2plus: 0.60}
    ADJUSTMENT:  {house_0_1: 0.50, house_2plus: 0.30}
    SPECULATION: {house_0_1: 0.40, house_2plus: 0.00}   # 0.00 = LOAN_FORBIDDEN 처리
  OFFICETEL:
    NONE:        {house_0_1: 0.70, house_2plus: 0.60}
  COMMERCIAL:
    NONE:        {house_0_1: 0.60, house_2plus: 0.60}   # 비주거는 방공제·DSR 취급 상이 → v1은 동일 로직 + 경고
  # 미정의 조합 조회 시: KeyError를 내지 말고 가장 보수적인 값(최소 LTV) 적용 + calc_trace에 기록

room_deduction:                   # 소액임차 최우선변제금(예시: 2023-02-21 시행령 수준)
  SEOUL: 55000000
  OVERCROWDED: 48000000           # 과밀억제권역·세종·용인·화성·김포 등
  METRO: 28000000                 # 광역시 등
  OTHER: 25000000
  region_map:                     # region_code(시군구 5자리) → 위 구분 매핑. T0에서 대상 지역만 채움
    "30110": "METRO"              # 예: 대전 동구 (예시)

acquisition_tax:
  house:
    tier_1_max: 600000000         # 6억
    tier_1_rate: 0.01
    tier_2_max: 900000000         # 9억
    # 6~9억 구간: rate = bid * 2 / 300000000 - 3 (%) → 코드로 구현, 수치 상수는 여기서 읽기
    tier_3_rate: 0.03
    multi_house_adjust: {h2: 0.08, h3plus: 0.12}    # 조정지역 중과(예시)
    multi_house_none:   {h2: null, h3plus: 0.08}    # null = 기본세율 적용
  commercial_rate: 0.04
  local_edu_tax_rate: 0.001       # 간이(예시)
  special_rural_tax_rate: 0.002   # 간이(예시), 85m² 초과 등 조건은 v1 단순화 + 경고

auction:
  discount_per_fail: 0.20         # 유찰 저감률(법원별 상이, 기본 20%)
  deposit_rate_default: 0.10
  payment_deadline_days: 45       # 낙찰~잔금 통상 가정(이자 계산용)
```

### 5.2 `config/heuristics.yaml` (명도비·관리비 등 추정 파라미터)

```yaml
eviction_cost:                    # 점유 유형별 명도비 추정(원)
  OWNER_OCCUPIED: 1500000
  TENANT_FULL_DIVIDEND: 1000000   # 배당 전액 수령 임차인(협조적)
  TENANT_PARTIAL: 3000000
  UNKNOWN_OCCUPANT: 5000000
  LIEN_CLAIMED: null              # 산정 불가 → risk D 사유

unpaid_maintenance:
  monthly_fee_per_m2: 3000        # 관리비 추정 단가(원/m²·월)
  vacancy_months_formula: "failed_count * 2 + 6"
  common_area_ratio: 0.6          # 공용부분 비율(낙찰자 부담분)

repair_reserve_ratio: 0.01        # 낙찰가 대비 수선충당 추정
legal_bond_fee_ratio: 0.004       # 법무+채권할인 간이 추정(낙찰가 대비)

tenant_deposit_fallback:          # 보증금 불명 임차인의 보수 추정
  jeonse_ratio_of_appraisal: 0.6  # 감정가×60%를 보증금으로 가정 + UNCERTAIN
```

### 5.3 `data/policies.yaml` (정책·상품 DB — T6에서 큐레이션)

```yaml
- product_id: kb_auction_balance
  name: "KB 경락잔금대출(일반)"
  category: BANK_LOAN
  applicable_to_auction: true
  conditions: {min_age: 19}
  rate_note: "물건·신용도별 상이"
  source_url: ""
  checked_date: null
  verified: false
- product_id: didimdol
  name: "디딤돌대출"
  category: POLICY_LOAN
  applicable_to_auction: null      # 경락자금 용도 가능 여부 확인 필요 → 확인 전 매칭 시 경고 표시
  conditions:
    houseless_only: true
    max_annual_income_single: 60000000
    max_house_price: 500000000
    max_amount: 250000000
  rate_note: "소득 구간별 우대"
  source_url: ""
  checked_date: null
  verified: false
# ... 보금자리론, 청년 전용 상품, MCI/MCG 등 6~10개로 확장 (T6)
```

---

## 6. 태스크 상세

### 6.0 태스크 개요와 의존성

| ID | 이름 | 산출물 위치 | 의존 | 병렬 가능 |
|----|------|------------|------|----------|
| T0 | 스캐폴딩+스키마+fixture | 레포 전체 골격 | — | 최우선 단독 |
| T1a | 경매 사례 수집(낙찰결과 포함) | `data/processed/auction_cases.parquet` | T0 | T1b/c와 병렬 |
| T1b | 실거래가 수집 | `data/processed/trades.parquet` | T0 | |
| T1c | 문서 샘플 확보 | `data/samples/`, `data/raw/docs/` | T0 | |
| T2 | A1 권리분석 | `src/agents/rights_analysis` | T0, T1c | T3~T6과 병렬 |
| T3 | A2 낙찰가 밴드 | `src/agents/price_band` | T0, T1a, T1b | |
| T4 | A3 자금 상한 | `src/agents/financing` | T0, (T5의 cost_function 시그니처) | |
| T5 | A4 TCO | `src/agents/tco` | T0 | T4보다 cost_function 먼저 |
| T6 | A5 정책 매칭 | `src/agents/policy_match` | T0 | |
| T7 | 오케스트레이터 | `src/orchestrator` | T0 (mock으로 선개발), 최종은 T2~T6 | |
| T8 | 데모 UI | `app/main.py` | T7 | |
| T9 | 평가·테스트 | `tests/`, `notebooks/` | T2~T7 | |
| T10 | 제안서·발표 | `docs/proposal/` | 전체 | 초안은 병렬 |

**병합 전략의 핵심**: T0가 모든 스키마 + mock 구현 + fixture를 먼저 만든다. 이후 T2~T6은 자기 모듈의 mock을 실구현으로 교체하기만 하면 되고, T7/T8은 mock 위에서 먼저 완성할 수 있다. → 어떤 순서로 병합해도 파이프라인이 항상 동작한다.

---

### T0. 프로젝트 스캐폴딩 + 공통 스키마 + fixture (최우선, 다른 모든 작업의 전제)

**목적**: 인터페이스와 테스트 기준을 먼저 고정해 이후 태스크의 독립 개발을 가능하게 한다.

**작업 내용**
1. §3.4 디렉토리 구조 그대로 레포 생성. `pyproject.toml`(pydantic, lightgbm, pdfplumber, streamlit, pytest, plotly, pyyaml, requests).
2. §4 코드를 `src/schemas/core.py`로 작성.
3. §5의 두 config 파일을 예시값 그대로 생성(`verified: false`).
4. **가상 fixture 케이스 3종** 생성 — 각 폴더에 `case.json`(PropertyCase), `sale_spec.txt`(가상 매각물건명세서 전문 텍스트), `registry.txt`(가상 등기 요약):
   - `case_001_clean`: 대전 아파트, 감정가 3.2억, 1회 유찰 최저가 2.24억. 소유자 점유, 근저당 1건(말소기준). 기대: 인수권리 0, grade A.
   - `case_002_tenant`: 서울 다세대, 감정가 2.8억. 임차인 전입일이 근저당 설정일보다 앞섬 + 배당요구 안 함 + 보증금 1.2억. 기대: 보증금 전액 인수, grade C.
   - `case_003_lien`: 상가, 유치권 신고(공사대금 주장 8천만). 기대: grade D.
5. 각 케이스의 **기대 중간 산출** JSON을 `tests/fixtures/`에 작성: `case_001.rights.json`, `case_001.verdict.json` … (직접 손으로 값 계산해 채운다. 이것이 이후 모든 모듈의 정답지다.)
6. **mock 에이전트**: 각 `src/agents/*/run.py`에 "fixture 케이스면 fixtures의 JSON을 로드해 반환, 아니면 NotImplementedError" 스텁 구현.
7. 3명의 fixture 유저 프로필: `user_young`(연소득 4200만, 현금 6천, 무주택, 실거주), `user_invest`(연소득 9천만, 현금 2억, 2주택), `user_tight`(연소득 3천만, 현금 2천).

**DoD**
- `pytest tests/test_schemas.py` 통과 (모든 fixture JSON이 스키마로 validate됨).
- mock만으로 `orchestrator.run(case_001, user_young)`이 FinalVerdict를 반환하는 E2E 스모크 테스트 통과.

---

### T1. 데이터 수집

> 수집기는 P7 예외(사이드이펙트 허용) 영역. 단 `collect() -> 저장` 이후의 모든 소비는 processed 파일만 읽는다.

**T1a. 경매 사례(낙찰 결과 포함, A2 학습용)**
- 우선순위 전략:
  1. **온비드(공매) 공공 API** — 공공데이터포털 "한국자산관리공사_온비드" 계열 API로 낙찰 결과 수집. 법원경매와 낙찰가율 분포가 완전히 같지는 않으나 PoC 학습 데이터로 사용 가능(리포트에 `caveats`로 명시).
  2. 법원경매정보(courtauction.go.kr) — **이용약관·robots 확인 후** 소량(30~100건) 수동/반자동 수집. 약관상 자동수집 불가로 판단되면 수동 수집만.
  3. 유료 데이터(지지옥션 등)는 예산 확보 시에만.
- 필드 매핑: PropertyCase 필드 + `sold_price: int`, `sold_date: date`, `bidder_count(수집만 하고 학습 사용 금지)`.
- 최소 목표: **단일 지역(대전 우선) 아파트 낙찰 사례 300건**. 미달 시 A2는 HEURISTIC 모드로 동작(§T3)하므로 파이프라인은 깨지지 않는다.
- 산출: `data/processed/auction_cases.parquet` + 수집 스크립트 `src/collectors/`.

**T1b. 실거래가**
- 국토교통부 실거래가 공개 API(공공데이터포털, 인증키 필요 — `.env`로 관리, 커밋 금지).
- 대상: T1a와 같은 region_code, 최근 24개월, 매매.
- 산출: `data/processed/trades.parquet` (단지명/면적/층/금액/계약일).

**T1c. 문서 샘플**
- 종결 사건의 매각물건명세서·현황조사보고서 PDF **10건 이상** 수동 확보(법원경매정보에서 열람 가능한 범위).
- pdfplumber로 텍스트 추출 → `data/raw/docs/<case_id>/*.txt` 저장. 추출 품질이 나쁜 문서는 목록에서 제외하고 사유 기록.
- 이 중 5건은 T9 골든셋 후보로 라벨링 대상.

**DoD(T1 공통)**: processed 파일이 스키마 validate 통과, 수집 방법·일자·약관 검토 결과를 `data/README.md`에 기록.

---

### T2. A1 권리분석 에이전트

**시그니처**: `run(case: PropertyCase) -> RightsAnalysisReport`

**구현 3단계**

1. **파싱** (`parser.py`)
   - 입력: `documents.*_path` (PDF면 pdfplumber로 텍스트화, .txt면 그대로).
   - 매각물건명세서를 섹션 단위로 분리: ① 사건 기본정보 ② 임차인(점유자) 현황 표 ③ "등기된 부동산에 관한 권리 또는 가처분으로서 매각으로 그 효력이 소멸되지 아니하는 것" ④ 비고란.
   - `sale_spec_path`가 없으면 `AgentError(recoverable=False)` — 권리분석 없이는 서비스 진행 불가(§T7 실패 정책).

2. **LLM 구조화 추출** (`prompts/extract.md`)
   - 문서 텍스트 → 중간 추출 스키마(모듈 내부 `schemas.py`에 정의: `RawTenantRow`, `RawRightRow` 등) JSON으로 추출. structured output 강제, temperature=0.
   - 프롬프트 필수 지시: "문서에 없는 내용을 만들지 마라. 불명확하면 null. 각 항목에 원문 근거 문구를 evidence로 인용하라." / 임차인명은 마스킹해 출력.
   - LLM 호출은 반드시 `src/llm/client.py` 경유.

3. **룰 검증·판정 레이어** (`rules.py`) — 최종 판단은 룰이 한다(LLM 출력은 소재료).
   - 말소기준권리 판정:
     ```
     candidates = 등기권리 중 {MORTGAGE, SEIZURE, COLLATERAL_PROV_REG, AUCTION_START}
                + {JEONSE_RIGHT 이면서 (배당요구 or 경매신청)한 선순위 전세권}
     base_right = min(candidates, key=registered_date)
     candidates가 비면 base_right=None + warning("말소기준권리 식별 불가") + risk C 이상
     ```
   - 대항력: `tenant.move_in_date < base_right.registered_date` (strict). 전입일 불명 → `opposing_power=None`, UNCERTAIN.
   - 임차보증금 인수액(보수 원칙 P4):
     ```
     대항력 없음                → 0
     대항력 있음 + 배당요구 없음 → deposit 전액 인수
     대항력 있음 + 배당요구 있음 → v1 단순화: deposit 전액을 UNCERTAIN 인수로 잡고
                                  warning("배당 후 미회수분만 실제 인수 — 배당표 시뮬레이션은 v2")
     deposit 불명               → heuristics.tenant_deposit_fallback으로 추정 + UNCERTAIN
     배당요구 여부 불명          → '없음'으로 가정(인수) + UNCERTAIN
     ```
   - 특수권리(§2.2c) 탐지 시: AssumedRight 추가(금액 불명이면 명세서·신고서상 주장액, 그것도 없으면 estimated_cost=0 + warning "금액 산정 불가") 및 아래 등급표 적용.
   - **risk_grade 판정표(고정)**:
     ```
     D: LIEN/STATUTORY_SUPERFICIES/GRAVE_BASE/TRANSFER_PROV_REG/선순위 INJUNCTION 존재
        또는 base_right 식별 불가 + 임차인 존재
     C: total_assumed_cost ≥ appraisal_price×10% 또는 UNCERTAIN 항목 ≥ 2
     B: 0 < total_assumed_cost < 감정가 10%, 명도 MID 이하
     A: 인수권리 없음 + 소유자 점유 또는 전액배당 임차인
     ```
   - eviction_difficulty: 점유자 유형 → {소유자/전액배당 임차인: LOW, 일부·미배당 임차인: MID, 불명 점유·다수: HIGH}.
   - LLM 추출값과 룰 재계산값이 충돌하면 룰 우선 + 해당 항목 UNCERTAIN + warning.

**DoD**
- fixture 3건에서 기대 JSON과 일치(특히 **인수권리 미검출 0건** — recall 100%가 최우선, 과검출은 warning으로 허용).
- T1c 실제 문서 5건에 대해 수동 검증표(`tests/manual_review_rights.md`) 작성: 항목별 정/오/판단불가.
- `sale_spec_path=None` 시 recoverable=False AgentError 발생 테스트.

---

### T3. A2 낙찰가 밴드 예측

**시그니처**: `run(case: PropertyCase) -> PriceBandEstimate`

**피처 목록(허용)** — `features.py`에 이 목록 그대로 구현:
- 물건: property_type, building_area_m2, built_year, floor, region_code(타깃 인코딩 대신 빈도/그룹 통계), appraisal_price(로그), min_bid_price/appraisal_price, failed_count
- 시장: 동일 region_code 최근 6개월 실거래 중위가 대비 감정가 비율(T1b), 최근 3개월 지역 낙찰가율 이동평균(T1a)
- **금지 피처(타겟 누수)**: bidder_count, sold_date 이후 정보 일체, sold_price 파생값.

**모델**
- 타깃: `appraisal_ratio = sold_price / appraisal_price` (가격 자체보다 안정적).
- LightGBM **quantile regression** 3개(α=0.1/0.5/0.9). p10/p50/p90 = 예측비율 × appraisal_price. 밴드 교차 시(p10>p50 등) 정렬 보정 후 caveat 기록.
- 분할: **시간 기준 split**(sold_date 기준 과거→학습, 최근 20%→검증). 랜덤 split 금지(미래 누수).
- `train.py`는 노트북이 아니라 스크립트로: `python -m src.agents.price_band.train` → `data/models/price_band_{version}.txt` 저장.

**유사사례(comparables)**: 학습 데이터에서 [지역, 유형, 면적, 감정가 로그] 표준화 후 유클리드 거리 top5. 근거 제시용이며 예측에 재사용하지 않는다.

**HEURISTIC 폴백(필수 구현)** — 학습 데이터 <300건이거나 모델 파일 부재 시:
- 동일 region_code×property_type의 최근 12개월 낙찰가율 중앙값을 p50으로, IQR로 p10/p90 구성. 해당 세그먼트 표본 <10건이면 광역 단위로 확대.
- `method="HEURISTIC"` + caveats에 표본 수 명시. **파이프라인은 절대 이 모듈 때문에 중단되지 않는다.**

**DoD**
- 홀드아웃에서 밴드 커버리지(실제 sold_price가 p10~p90에 포함) **≥ 75%**, p50 MAPE 리포트 — `notebooks/price_band_eval.ipynb`.
- fixture 케이스는 HEURISTIC 경로로 값 반환됨을 테스트.
- 결정성: 동일 입력 재실행 시 동일 출력.

---

### T4. A3 자금조달 상한 (순수 룰 엔진 — LLM 금지)

**시그니처**: `run(case: PropertyCase, user: UserProfile, rights: RightsAnalysisReport, cost_fn) -> FinancingCeiling`
- `cost_fn`은 A4의 `cost_function`을 ORCH가 주입(부분적용 형태). A3가 A4 패키지를 직접 import하지 않고 함수만 받는다 → 순환 의존 차단.

**계산 순서** (`calc_trace`에 각 단계 문자열 기록):
1. 규제 조회: `ltv = config.ltv[property_type][regulation_zone][house_bucket]` (house_bucket: owned_house_count 0~1 vs 2+). `ltv==0.0`이면 `binding_constraint=LOAN_FORBIDDEN`, max_loan=0으로 진행(현금만으로 계산 지속).
2. 방공제: `room_deduction = config.room_deduction[region_map[region_code]]` (주거형만, COMMERCIAL은 0 + trace 기록).
3. DSR 한도 역산:
   ```
   가용 연 원리금 = annual_income × dsr_limit − existing_annual_debt_payment (음수면 0)
   월 가용액 M = 가용 연 원리금 / 12
   r = (assumed_rate + stress_rate_addon) / 12,  n = target_loan_years × 12
   max_loan_by_dsr = M × ((1+r)^n − 1) / (r × (1+r)^n)     # 원리금균등 역산
   ```
4. 입찰가 bid에 대한 대출함수:
   ```
   loan(bid) = max(0, min(ltv × min(bid, appraisal_price) − room_deduction,
                          max_loan_by_dsr, product_cap))
   ```
5. 가용성 함수: `f(bid) = cash_available + loan(bid) − cost_fn(bid).total_upfront_excl_bid − bid`
   - `cost_fn`에는 rights.total_assumed_cost가 이미 포함되어 들어온다(ORCH가 주입 시 바인딩).
6. **max_bid_price = f(bid) ≥ 0 을 만족하는 최대 bid** — 이분탐색:
   - 탐색 구간 [case.min_bid_price, cash×20 + appraisal×2], f는 bid에 대해 단조감소(loan 증가분 기울기 < 1, cost 증가) → 이분탐색 유효. 수렴 정밀도 10만원, 결과는 10만원 단위 내림.
   - `f(min_bid_price) < 0`이면: max_bid_price = 0, binding_constraint=CASH, warning("최저가조차 감당 불가").
7. binding_constraint 판정: max_bid 시점에 loan을 결정한 min() 항목. 대출이 0인데 현금으로만 성립하면 CASH.
8. 출력 조립: max_loan_by_ltv는 max_bid 대입값, monthly_payment_at_max는 max_loan 기준 원리금균등.

**DoD**
- 수기 계산 대조 단위테스트 **5케이스 이상**(무주택 일반 / 조정지역 2주택 / DSR 바인딩 / LOAN_FORBIDDEN / 현금 부족).
- 결정성 테스트, LLM import가 없는지 정적 검사(`grep -r "llm" src/agents/financing` 결과 0).
- fixture user 3명 × case 3건 = 9조합 스냅샷이 tests/fixtures와 일치.

---

### T5. A4 총소유비용 (순수 룰 엔진 — LLM 금지)

**시그니처 2개**
- `cost_function(bid: int, case: PropertyCase, user: UserProfile, rights: RightsAnalysisReport, cfg) -> TCOReport` — **T4보다 먼저 이 함수의 시그니처·동작을 확정**한다(TCOReport 반환, A3는 total_upfront_excl_bid만 사용).
- `run(...)`: ORCH가 최종 리포트용으로 max_bid_price를 넣어 호출하는 래퍼.

**계산 항목**
1. 취득세: §5.1 config 기반. 주택: 구간·주택수·규제지역 반영(6~9억 슬라이딩 공식은 tax.py에 구현, 상수는 config에서). 비주거: commercial_rate. + 지방교육세·농특세 간이율. v1 단순화 사항은 breakdown note에 명시("농특세 면적 조건 미반영" 등).
2. 법무·채권: `bid × legal_bond_fee_ratio`.
3. 명도비: rights의 점유 유형 → `heuristics.eviction_cost` 표. LIEN_CLAIMED(null)이면 0으로 두되 warning은 A1/ORCH 몫(D등급이라 RED 처리됨).
4. 미납관리비: `monthly_fee_per_m2 × building_area_m2 × vacancy_months(failed_count) × common_area_ratio` (주거 집합건물만, 그 외 0).
5. 수선충당: `bid × repair_reserve_ratio`.
6. 인수권리비용: `rights.total_assumed_cost` 그대로 전달(재계산 금지 — 중복 방지).
7. `total_upfront_excl_bid = Σ(1..6)`, `effective_acquisition_price = bid + total_upfront_excl_bid`.
8. breakdown 리스트에 전 항목 {label(한국어), amount, note}.

**DoD**: 항목별 단위테스트(경계값: 정확히 6억/9억, 면적 0/None 방어), 결정성, fixture 스냅샷 일치.

---

### T6. A5 정책·상품 매칭

**시그니처**: `run(case, user, financing: FinancingCeiling) -> PolicyMatchResult`

**작업**
1. `data/policies.yaml` 큐레이션: KB 경락잔금대출(일반 조건), 디딤돌, 보금자리론, 청년 관련 상품, MCI/MCG, (BUSINESS 목적용) 소상공인 정책자금 1~2건 — **각 항목에 source_url + checked_date 필수**, 미확인이면 verified:false.
2. `eligibility.py` — 자격 판정은 100% 룰:
   - 조건 필드(houseless_only, max_annual_income, max_house_price↔case.appraisal_price 비교, min/max_age, purpose 제한)를 UserProfile/PropertyCase와 대조 → requirements_met/unmet 채움.
   - `applicable = len(unmet)==0`. 단 `applicable_to_auction`이 None이면 applicable이어도 카드에 "경락자금 용도 가능 여부 확인 필요" 문구 강제.
3. summary_note만 LLM 허용(P3: 숫자는 필드값 치환).

**DoD**: fixture 유저 3명 × 상품 전체 매칭 결과 스냅샷 테스트. verified:false 상품이 데모 화면에서 '확인 필요' 배지로 표시되는 것 확인(T8과 연동).

---

### T7. 오케스트레이터

**시그니처**: `run(case: PropertyCase, user: UserProfile) -> FinalVerdict`

**실행 순서와 실패 정책**
```
1) A1 실행 — 실패(recoverable=False) 시 전체 중단, 사용자에게 "명세서 없이는 분석 불가" 반환
2) A2 실행 — 실패 시 HEURISTIC 재시도, 그것도 실패면 밴드 없이 진행 + warning
3) cost_fn = partial(A4.cost_function, case=case, user=user, rights=A1결과, cfg=...)
4) A3 실행(cost_fn 주입)
5) A4.run(max_bid_price) → 최종 TCO
6) A5 실행 — 실패 시 빈 products + warning
7) 신호등 판정 → 리포트 생성
```

**신호등 판정 로직(고정 — 임의 변경 금지)**
```python
def decide(rights, band, financing) -> tuple[Signal, Optional[int]]:
    # 1) 권리 리스크가 자금과 무관하게 우선한다
    if rights.risk_grade == RiskGrade.D:
        return Signal.RED, None                      # 입찰 자체를 말린다
    # 2) 자금 대비 시장 밴드
    if band is not None:
        if financing.max_bid_price < band.p10:
            return Signal.RED, None                  # 사실상 낙찰권 밖 + 무리 금지
        if financing.max_bid_price < band.p50:
            sig = Signal.YELLOW                      # 시도 가능하나 경쟁 열위 고지
        else:
            sig = Signal.GREEN
    else:
        sig = Signal.YELLOW                          # 밴드 없으면 보수적으로
    # 3) 권리 등급으로 하향 보정
    if rights.risk_grade == RiskGrade.C and sig == Signal.GREEN:
        sig = Signal.YELLOW
    rec = min(financing.max_bid_price,
              band.p90 if band else financing.max_bid_price)   # 과열 방지 캡
    return sig, rec
```
- `recommended_max_bid`는 **절대 financing.max_bid_price를 초과할 수 없다**(테스트로 강제).
- key_numbers 필수 키: `p50, max_bid, assumed_cost, effective_price_at_max, monthly_payment`.

**리포트 생성(`report.py`)**
- `templates/report.md.j2` — 섹션: 신호등 요약 / 이 물건의 인수 리스크(카드) / 감당 가능 상한과 근거(calc_trace 요약) / 예상 낙찰가 밴드(참고) / 총비용 브레이크다운 / 추천 금융 시나리오 / 면책.
- 수치는 전부 Jinja 변수 치환. LLM은 rationale 서술 1~2문단만 생성(수치 언급 시 반드시 치환 변수 사용 — 프롬프트에 명시).
- **금지어 검사**: 렌더 결과에 §7.4 금지어 포함 시 예외 발생.

**DoD**: mock 기반 E2E 3케이스(GREEN=case_001+user_young / YELLOW 또는 RED(자금)=case_001+user_tight / RED(권리)=case_003) 스냅샷 일치. 부분 실패 시나리오(A2 다운) 테스트.

---

### T8. 데모 UI (Streamlit)

**화면 플로우**
1. 사이드바: UserProfile 입력 폼(만원 단위 입력 → 내부 원 변환) + fixture 유저 프리셋 버튼 3개.
2. 메인: 케이스 선택 드롭다운(`data/samples/` 스캔) — **라이브 크롤링 금지**.
3. [분석 실행] → 단계별 스피너(권리분석 중 → 밴드 추정 중 → …).
4. 결과 대시보드:
   - 상단: 신호등 배지 + recommended_max_bid(크게) + "이 금액은 감당 가능 상한이며 낙찰 보장 아님" 고지.
   - 차트 1: 가로 밴드 차트 — p10~p90 밴드 위에 max_bid_price 수직선(plotly). 상한이 밴드보다 왼쪽이면 붉은 음영.
   - 인수 리스크 카드: assumed_rights + tenants (confidence 배지: CONFIRMED 초록/UNCERTAIN 주황).
   - 차트 2: TCO 워터폴(bid → +세금 → +명도 → … → effective_acquisition_price).
   - 상품 카드 목록(A5): applicable=True만 상단, '확인 필요' 배지 처리.
   - 하단: report_markdown 렌더 + disclaimer.
5. RED 케이스는 recommended_max_bid 대신 "입찰 비권장 사유"를 크게 표시.

**DoD**: `streamlit run app/main.py`로 3개 데모 시나리오(§T10) 무에러 완주. 스크린샷을 `docs/proposal/screenshots/`에 저장.

---

### T9. 평가·테스트

1. **골든셋**: T1c 실문서 중 5~10건 수동 라벨(인수권리 유무·금액, 실제 sold_price 확인 가능 건). `data/golden/labels.yaml`.
2. A1 평가: 골든셋 recall/precision — **recall 우선**(미검출은 치명, 과검출은 허용). 목표 recall 100%(미달 시 원인 분석 문서화).
3. A2 평가: T3 DoD의 커버리지 리포트를 골든셋 sold_price로 교차 확인.
4. A3/A4: 수기 대조 표(`tests/manual_calc.md`) — 케이스별 엑셀/손계산 값 vs 코드 값.
5. 통합: `tests/test_e2e.py` — fixture 9조합 전체 스냅샷 + 금지어 검사 + recommended_max_bid ≤ max_bid_price 불변식.

**DoD**: 위 산출물 전부 + `pytest` 전체 green. 평가 요약 1페이지(`docs/proposal/eval_summary.md`).

---

### T10. 제안서·발표자료

**스토리라인(슬라이드 순서)**
1. 문제: "경매는 싸다. 그러나 초보자에게는 지뢰밭이다" — 보증금 인수 실패 사례 구조 1장.
2. 기존 서비스 한계: 낙찰가 '예측'만 있고, '내가 감당 가능한가'와 '숨은 인수금액'을 통합한 서비스 부재.
3. 솔루션: 파이프라인 다이어그램(§3.1) + 신호등 UX.
4. **차별점 3가지(발표 핵심 멘트)**: ① 낙찰 확률이 아닌 소비자 보호 관점의 '감당 상한'(주제 4 연결) ② 문서 기반 권리분석 자동화(LLM은 추출, 판정은 룰 — 신뢰성) ③ KB 실상품(경락잔금대출)·KB시세와의 즉시 연결 = 낙찰자 리드 확보 BM(주제 2 연결).
5. 라이브 데모 3종:
   - GREEN: user_young × case_001 → 상한 제시 + 디딤돌 매칭.
   - RED(권리): case_002 → "실질 취득가 = 낙찰가 + 1.2억" 경고 장면.
   - RED(자금): user_tight → "이 물건은 포기 권고" 장면(소비자 보호 강조).
6. 평가 결과 요약(T9) + 한계와 로드맵: OCR, 배당표 시뮬레이션, 대안 물건 추천, 실시간 연동(v2).
7. 기대효과: 청년 주거 사다리(주제 1) + 여신 건전성 + 신규 고객 접점.

**DoD**: `docs/proposal/` 에 제안서(pdf/pptx)와 발표 스크립트, 데모 리허설 체크리스트.

---

## 7. 병합·품질 체크리스트 (PR마다 확인)

### 7.1 인터페이스
- [ ] `src/schemas/core.py`만 import (스키마 재정의 grep 0건)
- [ ] `run()` 시그니처가 §6 정의와 일치
- [ ] 에이전트 간 직접 import 없음(A3→A4는 함수 주입만)

### 7.2 원칙 준수
- [ ] A2/A3/A4 내 LLM 관련 import 0건
- [ ] 규제 수치 하드코딩 검사: `grep -rnE "0\.(4|5|6|7)0?|55000000|600000000" src/ | grep -v config` 류로 확인
- [ ] 모든 리포트 경로에 DISCLAIMER 포함

### 7.3 테스트
- [ ] 해당 모듈 fixture 스냅샷 green
- [ ] 결정성 테스트(해당 시) green
- [ ] `recommended_max_bid ≤ financing.max_bid_price` 불변식 테스트 green

### 7.4 금지어(대소문자 무시, 리포트·UI 문자열 대상)
```
"낙찰 확률", "낙찰될 확률", "이길 수 있는", "낙찰 보장", "무조건 낙찰",
"승리 입찰가", "winning bid probability"
```
- 예상 낙찰가 밴드를 언급할 땐 반드시 "참고 정보" 수식어를 동반한다.

---

## 8. 데이터 소스 요약

| 소스 | 용도 | 접근성 | 비고 |
|---|---|---|---|
| 온비드(캠코) 공공 API | 낙찰 결과 학습(T1a) | 공공데이터포털 인증키 | 공매≠경매 분포 차이 caveat 명시 |
| 법원경매정보 | 물건·문서·매각결과 | 웹 열람. **자동수집 약관 확인 필수** | PoC는 수동 소량 |
| 국토부 실거래가 API | 시세 피처(T1b) | 공공데이터포털 인증키 | region_code 기준 |
| 인터넷등기소 | 등기부 | 유료(건당 소액) | 샘플만 수동 확보 |
| KB부동산 시세 | 감정 대체 기준 | 공개 API 제한적 | **공모전에서 KB 데이터 제공 여부 확인 → 제공 시 최우선 사용(가산점)** |
| 주택도시기금/주금공 | 정책상품 조건(T6) | 공개 웹 | policies.yaml에 URL·확인일 기록 |

API 키는 전부 `.env`(git 제외), `src/utils/env.py`로 로드.

---

## 9. 미결정 사항 & 보수적 기본값 (발견 시 여기에 추가)

| 항목 | 상태 | 확인 전 기본값(보수적) |
|---|---|---|
| 공모전 요강: 외부 LLM API 허용 범위, KB 제공 데이터 유무, 제출 형식 | **미확인 — 최우선 확인** | LLM 교체 가능하도록 client 추상화 유지 |
| 법원경매정보 자동수집 약관 | 미확인 | 수동 수집만 |
| 스트레스 DSR 현행 가산치 | 미확인 | 0 적용 + verified:false 유지 |
| 방공제 지역표·최우선변제 기준시점(말소기준권리 설정 당시 시행령 적용 이슈) | 미확인 | 현행 표 일괄 적용 + warning "기준시점에 따라 달라질 수 있음" |
| 디딤돌 등 정책모기지의 경락자금 용도 허용 | 미확인 | applicable_to_auction=null → '확인 필요' 배지 |
| 다주택 취득세 중과 현행률 | 미확인 | 예시값 + verified:false |
| 배당표 시뮬레이션(임차인 실제 미회수액 정밀 계산) | v2 스코프 | 전액 인수 가정(P4) |
| 스캔본 PDF OCR | v1 스코프 아웃 | .txt 수동 입력 우회 |

---

## 10. 변경 로그

| 날짜 | 변경 | 사유 | 작성자 |
|---|---|---|---|
| 2026-08-02 | 문서 초판 | — | 초기 설계 |
| 2026-08-03 | v1 구현 착수 전 심층 검토(트레이스 3레인) 후 §11 교정 22건 반영 | 스펙을 문자 그대로 구현하면 대표 데모가 산술적으로 성립하지 않고, DoD의 다수가 자기참조적이라 정확성을 보장하지 못함 | v1 구현 |
| 2026-08-03 | `PriceBandEstimate.sample_size: Optional[int] = None` 추가 (§4) | 수집 데이터 0건 상태에서 '측정된 밴드'와 '구조적 사전(prior)'을 `method` Literal 변경 없이 구별하기 위함. 기존 필드 불변 | v1 구현 |
| 2026-08-03 | §T4 A3 시그니처에 `cfg` 인자 추가 | 원 시그니처는 config 주입 경로가 없어 모듈 내부 파일 읽기를 강제 → P7(전역 상태·사이드이펙트 금지) 위반이 불가피했음 | v1 구현 |
| 2026-08-03 | §T7 `decide()`에 `min_bid_price` 인자 추가, `band.p90` 캡 제거 | (a) 법정 최저매각가격 미만 금액을 추천하는 경로가 열려 있었음 (b) p90 캡은 시장 밴드로 '감당 상한'을 깎는 것이라 P1(밴드는 참고 정보) 위반 | v1 구현 |
| 2026-08-03 | 기술 스택 Python 버전을 `3.11+` → `3.12` 고정 | 3.13은 pandas 3.0 계열이라 `streamlit requires pandas<3`과 충돌. 3.12에서 전 스택 상호운용 실측 확인 | v1 구현 |

> 스키마·원칙·태스크 정의를 바꾸는 모든 커밋은 이 표에 한 줄을 추가해야 한다.

---

## 11. v1 구현 교정 사항 (2026-08-03)

착수 전 3개 레인 심층 검토에서 확정된 결함과 그 처방이다. 아래 항목은 §6 태스크 상세보다
**우선한다**(§0 규칙 1의 우선순위 체계에서 §4 스키마 다음 순위로 취급).

### 11.1 산술 모순 — 대표 데모가 성립하지 않았다

`user_young × case_001`의 기대 신호등 GREEN은 §T4 공식으로 계산하면 도출되지 않는다.

```
f(bid) = cash + loan(bid) − cost(bid) − bid
       = 60,000,000 + (0.7·bid − 28,000,000) − (0.027·bid + 2,364,000) − bid
       = 29,636,000 − 0.327·bid
f(224,000,000) = −43,612,000 < 0  →  max_bid = 0  →  RED(자금)
```

**핵심 성질**: `max_bid`는 물건 가격과 무관하게 **현금과 방공제로만** 결정된다.

```
max_bid = (cash − room_deduction − 정액비용 − 인수권리비용) / (1 + 비용비율 − ltv)
```

교정: §T4/§T5 공식을 규범으로 삼고 fixture 수치를 재설계했다(§11.5).

### 11.2 스펙 내부 모순 (BLOCKER 5 + HIGH 6)

| ID | 결함 | 처방 |
|----|------|------|
| C1 | `heuristics.eviction_cost` 키 5개를 만들어낼 필드가 `RightsAnalysisReport`에 없음(비단사 매핑) | A4가 결정적 순서로 파생: LIEN 존재→`LIEN_CLAIMED` / HIGH→`UNKNOWN_OCCUPANT` / MID→`TENANT_PARTIAL` / tenants 비어있음→`OWNER_OCCUPIED` / else→`TENANT_FULL_DIVIDEND` |
| C2 | config `null` 3곳이 기본값 그대로 실행 시 100% TypeError | 로더가 치환: `product_cap.default`→`inf`, `eviction_cost.LIEN_CLAIMED`→0+경고, `multi_house_none.h2`→기본세율 분기 |
| C3 | `risk_grade` 판정표에 미커버 조합 4개 이상(가장 흔한 실무 케이스가 구멍에 빠짐) | D→C→B→A first-match-wins + **마지막 else C**(보수적 폴백)로 구멍 제거 |
| C4 | `ltv` 테이블에 `MULTI_HOUSE`/`DETACHED`/`LAND` 부재 + 폴백이 "전역 최소=0.00"이라 모든 다세대·단독이 무조건 대출 불가 | 행 추가 + 폴백을 "해당 유형 내 0.0 제외 최소 → 없으면 APARTMENT 프로필 + 경고"로 변경 |
| C5 | `room_deduction.region_map` 폴백 규칙 부재 → 미등재 지역에서 KeyError | `default_bucket: SEOUL`(최대 공제 = 가장 보수적) + 경고 |
| C6 | "금액 불명 특수권리 → `estimated_cost=0`"이 §4 필드 정의·P4와 정면 위반(가장 위험한 물건에서 가장 낙관적 숫자) | `appraisal_price × unknown_right_cost_ratio(0.10)` + UNCERTAIN + 경고 |
| C7 | "UNCERTAIN 항목 ≥ 2"의 계수 집합 미정의 + `TenantInfo.confidence` 기본값이 UNCERTAIN이라 자동 C 발생 | `uncertain_count = UNCERTAIN 인수권리 수 + (UNCERTAIN이면서 인수액>0인 임차인 수)` |
| C8 | `binding_constraint`가 step1(LOAN_FORBIDDEN)과 step7(argmin)에서 이중 결정 | LOAN_FORBIDDEN이면 step7을 건너뛴다 |
| C9 | 10만원 내림이 `min_bid_price` 미만을 만들 수 있음(응찰 불가 금액) | 내림 결과가 최저가 미만이면 `max_bid=0, CASH` |
| C10 | `vacancy_months_formula` 문자열 → eval 또는 P5 위반 | `vacancy_months: {per_fail, base, cap}` 계수로 분해. cap 36은 공용관리비 소멸시효 3년 반영 |
| C11 | 부가세율이 §2.4의 "비주거 실효 4.6%"와 불일치(4.3%) | 지방교육세를 본세율 연동(`local_edu_tax_of_main_rate`)으로, 비주거는 별도 정률. 농특세는 85m² 이하 비과세 반영 |

### 11.3 A2 zero-data 경로 (스펙 미정의)

§T3의 HEURISTIC 폴백도 데이터를 요구하므로 표본 0건에서는 `median()` → NaN → `int(NaN)` ValueError가 난다.
`method`는 동결된 Literal이라 세 번째 값을 넣을 수 없다. `sample_size`로 구별한다.

밴드의 근거는 통계가 아니라 **사건 구조 두 가지 사실**이다.
- 하한 p10 = 현재 회차 최저매각가격 (미만은 법적으로 응찰 불가)
- 상한 p90 = 직전 회차 최저매각가격 (그 가격에 팔리지 않았다는 관측된 사실)

```
n>=1 and 0<L<A : step = (L/A)**(1/n);  upper = ceil_unit(L/step)   # 사건에서 역산
n>=1 else      : upper = ceil_unit(L/(1-discount_per_fail))        # config는 폴백일 뿐
n==0           : upper = max(L, A)
p50 = 두 값의 산술 중앙 (어떤 확률분포도 가정하지 않음)
```

저감률은 **사건에서 역산한 값이 config 상수보다 우선**한다. case_001은 30% 저감인데
config 기본값 0.20을 쓰면 상한이 틀린다.

### 11.4 검증 방법론 교체

원안 DoD는 다수가 자기참조적이었다. fixture 기대값과 구현을 같은 사람이 만들면
스냅샷 테스트는 `impl(x) == impl(x)`라는 항진명제가 된다.

| 원안 | 실측 결과 | 교체 |
|---|---|---|
| §7.2 P5 하드코딩 grep | **오탐 3/3 · 미탐 11/11** (`0.045`,`0.001`,`3000` 등 실제 규제상수 0개 적발, `similarity 0.5`·버전 문자열만 적발). `grep -v config`가 `config_loader.py`를 통째로 면제 | (a) AST 숫자 리터럴 스캔 — 허용 집합 `{0,1,2,12,100,-1}` (b) **config 민감도 테스트** — 규제값을 섭동하면 출력이 반드시 변함 |
| §7.4 금지어 서브스트링 | 회피 9종 중 **8종 통과**. "대소문자 무시"는 한국어에 무의미. §1.4가 금지한 "이 금액이면 낙찰됩니다"가 목록에 부재 | NFKC + **전 공백 제거** 정규화 후 검사 + 어간 정규식 + **긍정 단언**(밴드 언급 블록에 "참고 정보" 필수). 적용 범위를 리포트 렌더에서 **FinalVerdict 전체 재귀 순회 + app/ 문자열 리터럴**로 확대 |
| §3.5 "동일 입력 2회 호출" 결정성 | 순수함수면 무조건 통과 → 아무것도 못 잡음. 실제 비결정성은 프로세스 경계 밖에 있음. 말소기준권리 후보를 `set`으로 모으자 `PYTHONHASHSEED`에 따라 MORTGAGE/SEIZURE/AUCTION_START로 **실제로 갈림** | 크로스프로세스 + 해시시드 5종 실행 후 출력 SHA-256 동일성. 말소기준권리 동점은 `(등기일, RIGHT_PRIORITY, 권리자)` 정렬로 결정화(§2.2b "같은 날이면 저당권 우선") |
| fixture 스냅샷 일치 | 자기채점 순환 | **골든값 없는 불변식**(정답 숫자 불요, 관계·부호·단조성·항등식만 검사) + **held-out `case_004_noise`**(파서 개발 중 미열람, case_002와 의미 동일·표기 전부 상이) |

### 11.5 fixture 재설계 (§T0 대체)

| id | 물건 | 지역 | 감정가 | 최저가 | 핵심 | 등급 |
|----|------|------|--------|--------|------|------|
| `case_001_clean` | 아파트 59.8㎡ | 대전 서구(30170) | 160,000,000 | 112,000,000 | 소유자 점유, 근저당 1건 | A |
| `case_002_tenant` | 다세대 45㎡ | 인천 부평(28237) | 180,000,000 | 126,000,000 | 임차인 전입 < 근저당, 배당요구 X, 보증금 60,000,000 | C |
| `case_003_lien` | 근생 상가 | 대전 유성(30200) | 300,000,000 | 210,000,000 | 유치권 신고 80,000,000 + 점유 불명 | D |
| `case_004_noise` | held-out | — | = case_002 | = case_002 | 의미 동일, 표기 전부 상이 | C |

| 유저 | 연소득 | 현금 | 보유 | 목적 | 나이 |
|------|--------|------|------|------|------|
| `user_young` | 42,000,000 | 80,000,000 | 0 | 실거주 | 28 |
| `user_invest` | 90,000,000 | 200,000,000 | 2 | 투자 | 45 |
| `user_tight` | 30,000,000 | 20,000,000 | 0 | 실거주 | 26 |

**데모 3종 (§T7 DoD와 §T10이 서로 다른 3종을 정의하던 충돌을 해소)**

| 시나리오 | 조합 | 신호 | 감당 상한 |
|---|---|---|---|
| GREEN | case_001 × user_young | GREEN | 152,700,000 |
| YELLOW(권리) | case_002 × user_invest | YELLOW (risk C 하향) | 191,500,000 |
| RED(권리) | case_003 × 전 유저 | RED | — (유치권 D) |
| 대비 장면 | case_001 × user_tight | RED(자금) | — (최저가 미달) |

> `case_002 × user_invest`의 상한이 단순 공식(207,800,000)이 아니라 191,500,000인 이유:
> `loan(bid) = ltv × min(bid, appraisal_price) − room_deduction`의 **담보 상한** 때문이다.
> 입찰가가 감정가 1.8억을 넘는 순간 대출이 60,000,000원에 고정되어 더는 비례해 늘지 않는다.

### 11.6 환경 위생 (실행 자체의 전제)

| 항목 | 문제 | 처방 |
|---|---|---|
| 인터프리터 | `python`/`pytest`/`streamlit`이 서로 다른 설치본을 가리켜 T0 DoD와 T8 DoD가 물리적으로 다른 환경에서 실행됨 | 레포 로컬 `.venv` + `scripts/*.cmd`가 `.venv\Scripts\python.exe`를 직접 호출 |
| 인코딩 | Windows 기본 cp949 → `yaml.safe_load(open(...))`가 한글에서 즉시 UnicodeDecodeError | `src/utils/io.py` 단일 IO 게이트(UTF-8 + NFKC + CRLF→LF), raw `open()` 금지를 AST 테스트로 강제, 스크립트가 `PYTHONUTF8=1` 설정 |
| sys.path | `streamlit run app/main.py`는 `app/`을 sys.path[0]로 잡아 `import src` 실패 | `app/main.py` 최상단에서 레포 루트 삽입 + `pyproject`의 `pythonpath = ["."]` |
| 지연 import | A2가 top-level `import lightgbm` 시 DLL 실패 하나로 A1/A3/A4/A5까지 전멸 | lightgbm·pdfplumber·pandas·anthropic 전부 함수 내부 지연 import. `import src.orchestrator`가 무거운 모듈을 끌지 않음을 테스트로 강제 |
