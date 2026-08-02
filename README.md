# SafeBid — 경매 안전입찰 에이전트

> 부동산 경매 참가자에게 **① 숨은 인수 리스크(권리분석)** 를 자동 분석하고,
> **② 소득·자산·규제 기준으로 감당 가능한 입찰 상한** 을 계산하며,
> **③ 경락잔금대출·정책금융을 매칭** 하는 금융 에이전트.

KB AI Challenge 출품작 · 주제 2(AI 데이터 기반 컨설팅) 메인 + 1(청년 주거 금융) + 4(금융소비자 보호)

---

## 이 서비스가 다른 이유

```
┌─────────────────────────────────────────────────────────────────────┐
│  기존 서비스            │  SafeBid                                   │
├─────────────────────────┼───────────────────────────────────────────┤
│  "얼마에 낙찰될까"       │  "내가 얼마까지 감당할 수 있나"              │
│  낙찰가 예측            │  감당 가능 입찰 상한 (affordability ceiling) │
│  낙찰가만 표시          │  실질 취득가 = 낙찰가 + 인수금액 + 부대비용    │
│  권리분석은 사용자 몫    │  문서에서 자동 추출 → 룰로 판정              │
└─────────────────────────┴───────────────────────────────────────────┘
```

**핵심 설계 원칙 3가지**

| | 원칙 | 구현 방식 |
|---|---|---|
| P1 | 1차 출력은 "감당 가능 상한"이지 "낙찰 가능성"이 아니다 | 금지 표현 가드가 리포트·UI 전 문자열을 재귀 검사 |
| P2 | 금액 계산에는 LLM을 쓰지 않는다 | A3/A4는 순수 결정적 룰 엔진. AST 스캔으로 LLM import 0건 강제 |
| P4 | 불확실하면 보수적으로(인수된다고) 가정한다 | 대항력·배당요구·보증금이 불명이면 전액 인수 + `UNCERTAIN` |

---

## 빠른 시작 (VSCode)

### 1. 프로젝트 열기

```
File → Open Folder → kb-auction-agent
```

### 2. 최초 1회 환경 구축

VSCode 통합 터미널(PowerShell)에서:

```powershell
scripts\setup.cmd
```

레포 안에 `.venv`(Python 3.12)를 만들고 의존성을 설치한다.
**전역 Python이나 conda 환경은 건드리지 않는다.**

> Python 3.12가 없다면: https://www.python.org/downloads/release/python-3129/
> 설치 후 `py -0p`로 `-V:3.12`가 보이는지 확인.

### 3. 인터프리터 선택 (VSCode)

`Ctrl+Shift+P` → `Python: Select Interpreter` → `.\.venv\Scripts\python.exe`

이걸 해야 VSCode의 자동완성·정의이동·테스트 탐색기가 올바른 환경을 본다.

### 4. 테스트

```powershell
scripts\test.cmd
```

기대 출력: `80 passed`

특정 파일만 돌리려면:

```powershell
scripts\test.cmd tests\test_invariants.py -v
```

### 5. 데모 실행

```powershell
scripts\demo.cmd
```

브라우저가 자동으로 http://localhost:8501 을 연다.
사이드바에서 **프리셋 유저**를 고르고, 메인에서 **케이스**를 선택한 뒤 `분석 실행`.

> 종료는 터미널에서 `Ctrl+C`.

### 데모 시나리오 3종

| 신호 | 조합 | 보여주는 것 |
|------|------|-------------|
| 🟢 **GREEN** | `case_001_clean` × `청년 실수요자` | 인수 리스크 없음 → 감당 상한 1억 5,270만원 제시 + 정책금융 매칭 |
| 🟡 **YELLOW** | `case_002_tenant` × `임대 투자자` | 대항력 있는 임차인 보증금 6,000만원 **인수** → 실질 취득가 급등 경고 |
| 🔴 **RED** | `case_003_lien` × 아무 유저 | 유치권 신고 → D등급 → 자금과 무관하게 **입찰 비권장** |

보너스 대비 장면: `case_002_tenant` × `청년 실수요자` → 같은 물건인데 RED(자금).
인수 보증금이 순현금 지출이라 청년은 감당할 수 없다는 것을 보여준다.

---

## 아키텍처

```
[입력] PropertyCase (사건번호·감정가·최저가·문서)  +  UserProfile (소득·현금·보유주택)
   │
   ├─ A1 rights_analysis ── 문서 파싱 → 인수권리·임차인·리스크 등급
   │     LLM 허용(추출만) · 판정은 100% 룰
   │
   ├─ A2 price_band ─────── 낙찰가 참고 구간 p10/p50/p90
   │     LLM 금지 · 데이터 없으면 사건 구조에서 유도한 사전 구간
   │
   ├─ A4 tco ────────────── 입찰가 함수형 비용 계산 cost_function(bid)
   │     LLM 금지 · 순수 결정적
   │
   ├─ A3 financing ──────── LTV/DSR/방공제 → ★ max_bid_price
   │     LLM 금지 · A4를 함수 주입으로 받아 이분탐색 (순환 의존 차단)
   │
   ├─ A5 policy_match ───── 자격 룰 필터 → 상품 카드
   │
   └─ ORCH ──────────────── 신호등 판정 + 리포트 생성 → FinalVerdict
```

**감당 상한이 결정되는 방식**

```
loan(bid) = max(0, min( ltv × min(bid, 감정가) − 방공제,  DSR한도,  상품한도 ))
f(bid)    = 현금 + loan(bid) − 부대비용(bid) − bid
max_bid   = f(bid) ≥ 0 을 만족하는 최대 bid          ← 이분탐색
```

`f`는 `bid`에 대해 단조감소한다(`f' = loan' − C' − 1 ≤ 0.70 − 0.025 − 1 < 0`).
LTV가 1 이상이면 이 성질이 깨지므로 solver가 명시적으로 assert 한다.

---

## 프로젝트 구조

```
kb-auction-agent/
├── process.md              단일 진실 원천(SSOT). §11에 v1 교정 사항
├── config/
│   ├── regulation.yaml     규제 파라미터 — 코드는 여기서만 읽는다
│   └── heuristics.yaml     명도비·관리비 등 추정 파라미터
├── data/
│   ├── samples/            데모·테스트 케이스 4종 + 유저 3종
│   └── policies.yaml       정책·상품 카탈로그 7종
├── src/
│   ├── schemas/core.py     공통 스키마 SSOT
│   ├── utils/              io(UTF-8 게이트) · config · guard(금지어) · errors · env
│   ├── llm/client.py       provider 중립 추상화 (룰 기본 / LLM 선택)
│   ├── agents/             A1~A5
│   ├── orchestrator/       신호등 판정 + Jinja 리포트
│   └── collectors/         온비드·국토부 실거래가 수집기
├── app/main.py             Streamlit 데모
├── tests/                  불변식 80개
└── scripts/                setup / test / demo
```

---

## 검증 방식

이 프로젝트의 테스트는 **"정답 숫자"에 의존하지 않는다.** 기대값 JSON과 구현을 같은 사람이
만들면 스냅샷 테스트는 `impl(x) == impl(x)`라는 항진명제가 되기 때문이다.
대신 **관계·부호·단조성·항등식**을 검사한다.

| 분류 | 검사하는 것 | 예 |
|------|-------------|-----|
| 자금 상한 | 상한 최대성 · f 단조감소 · 예산 상계 · 담보 상한 포화 · DSR 왕복 · 비교정역학 | `f(max_bid) ≥ 0 > f(max_bid + 10만원)` |
| 총비용 | 합계 정합 · breakdown 합계 · 실질취득가 정의 · pass-through · 세율 경계 연속성 | `effective = bid + upfront`, 6억·9억에서 세액 점프 0원 |
| 권리분석 | 대항력 strict 경계 · 보수성 강제 · 특수권리→D · 마스킹 · 인수총액 정합 | 전입일 == 근저당일이면 대항력 **없음** |
| 오케스트레이션 | 추천 상·하한 · D등급 전파 · key_numbers 충실성 · 면책 렌더 | `min_bid ≤ rec ≤ max_bid` |
| 원칙 강제 | **config 민감도** · AST 리터럴 · LLM 미의존 · 크로스프로세스 결정성 | 규제값을 바꿨는데 결과가 안 변하면 실패 |

**일반화 검증**: `case_004_noise`는 파서 개발 중 열지 않은 held-out 케이스다.
`case_002`와 의미는 같고 표기가 전부 다르다(`2022.09.05.` vs `2022년 9월 5일`,
`60,000,000원` vs `금6,000만원`, 전각 숫자, 셀 줄바꿈, 페이지 머리말).
동일한 판정을 산출한다 — 정규식이 특정 표기가 아니라 문서 구조를 잡았다는 증거다.

**결정성 검증**: `PYTHONHASHSEED` 5종에서 별도 프로세스로 실행해 출력 SHA-256이 같은지 본다.
같은 프로세스에서 두 번 호출하는 방식으로는 집합 순회 순서에 따른 비결정성을 잡을 수 없다.

---

## 로드맵

| 항목 | 현재 | 다음 |
|------|------|------|
| 낙찰가 구간 | 사건 구조(최저가·유찰횟수·감정가)에서 유도한 참고 구간. `sample_size`로 표본 수를 함께 노출 | 공공데이터포털 인증키 확보 → 온비드·실거래가 수집 → LightGBM 분위수 회귀(코드 완비, `python -m src.agents.price_band.train`) |
| 문서 추출 | 규칙 기반 파서(오프라인·무비용·재현 가능) | `SAFEBID_LLM_PROVIDER=auto` + API 키만 넣으면 LLM 경로로 자동 승격. downstream 코드는 한 줄도 바뀌지 않는다 |
| 임차인 배당 | 대항력 있는 보증금을 전액 인수로 가정(P4 보수) | 배당표 시뮬레이션으로 실제 미회수분만 인수 |
| 문서 입력 | 텍스트 추출 가능한 PDF·txt | 스캔본 OCR |
| 규제 수치 | `config/regulation.yaml`에 예시값 + 미확인 표시 | 최신 확인 후 `verified: true` + 근거 URL 기록 |

### 데이터 수집 활성화 (인증키 확보 후)

```powershell
# 1) 레포 루트에 .env 생성 — 공공데이터포털 마이페이지의 "Decoding" 키를 쓸 것
#    (Encoding 키를 쓰면 %2F가 이중 인코딩되어 SERVICE_KEY_IS_NOT_REGISTERED_ERROR)
echo DATA_GO_KR_SERVICE_KEY=<Decoding키> > .env

# 2) 수집
.\.venv\Scripts\python.exe -m src.collectors.onbid --region 30170 --months 24
.\.venv\Scripts\python.exe -m src.collectors.molit_trades --region 30170 --months 24

# 3) 학습 (300건 이상일 때)
.\.venv\Scripts\python.exe -m src.agents.price_band.train
```

수집 데이터가 들어오면 A2가 자동으로 `HEURISTIC(표본 n)` → `MODEL` 경로로 승격된다.
파이프라인 코드는 수정할 필요가 없다.

---

## 면책

> 본 분석은 법률·투자 자문이 아니며 정보 제공 목적입니다.
> 입찰 전 매각물건명세서 원문 확인과 법률 전문가 상담이 필요합니다.

`config/regulation.yaml`의 LTV·DSR·취득세·방공제는 **예시값**이며 `verified: false` 상태다.
데모 화면 상단에 이 사실이 배너로 표시된다.
