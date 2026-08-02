# Deep Dive Trace: kb-safebid

생성일: 2026-08-03
대상: `process.md` (SSOT, 초판 2026-08-02)
레인: L1 스펙 내부 모순(critic/opus) · L2 외부 의존 실행가능성(architect/opus) · L3 검증 전제 붕괴(verifier/opus)

---

## Observed Result

process.md는 T0~T10 태스크와 DoD가 촘촘히 정의된 SSOT다. "이대로 구현하면 병합 충돌 없이 완성된다"는 것이 문서의 주장. 이 주장이 성립하는지 착수 전에 검증했다.

---

## Ranked Hypotheses

| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | L3: 검증 방법론 붕괴 — DoD를 만족해도 정확성이 전혀 보장되지 않음 | High | Strong (3건 코드 실행 재현) | DoD 25개 절 중 9개가 항진명제, 7개가 측정 불가. §7.2 grep은 오탐 3/3·미탐 11/11로 완전 역전. §7.4 금지어는 회피 9종 중 8종 통과 |
| 2 | L1: 스펙 내부 모순 — 문자 그대로 구현하면 깨짐 | High | Strong (산술 증명) | BLOCKER 5건 확정. 특히 데모 대표 시나리오가 산술적으로 성립 불가 |
| 3 | L2: 외부 의존 부재로 데모 불가 | **Low (반증됨)** | Strong (실측 반증) | 네트워크·pip·lightgbm·streamlit 전부 정상 기동 확인. 진짜 블로커는 외부가 아니라 내부(인코딩·sys.path·인터프리터 분열) |

---

## Evidence Summary by Hypothesis

### L1 (스펙 내부 모순) — 22건 발견, BLOCKER 5

| ID | 결함 | 심각도 |
|----|------|--------|
| F1 | `user_young × case_001 = GREEN`이 §T4 공식상 **약 4,361만원 부족**해 산술적으로 불가능 → 실제로는 RED(자금) | BLOCKER |
| F2 | `heuristics.eviction_cost` 키 5개를 만들어낼 필드가 `RightsAnalysisReport`에 없음(비단사 매핑, 역함수 부존재) | BLOCKER |
| F3 | config `null` 3곳(`product_cap.default`, `multi_house_none.h2`, `eviction_cost.LIEN_CLAIMED`)이 기본값 그대로 돌리면 100% TypeError | BLOCKER |
| F4 | `risk_grade` 판정표에 미커버 조합 최소 4개(등급 미결정 구멍). 가장 흔한 실무 케이스가 구멍에 빠짐 | BLOCKER |
| F5 | `ltv` 테이블에 `MULTI_HOUSE`/`DETACHED`/`LAND` 없음 + 폴백이 "전역 최소=0.00"으로 귀결 → **모든 다세대·단독이 무조건 대출 불가** | BLOCKER |
| F6 | `room_deduction.region_map`에 대전 동구 1건뿐 + 폴백 규칙 부재 → case_002/003 KeyError | HIGH |
| F7 | `A3.run()` 시그니처에 `cfg`가 없는데 config 4종을 읽고 `config_version`을 출력해야 함 → P7 위반 강제 | HIGH |
| F8 | `decide()`의 `band.p90` 캡이 P1 위반 + `min_bid_price`를 인자로 안 받아 **법정 최저가 미만 입찰가 추천 가능** | HIGH |
| F9 | §T10 "RED(권리): case_002"는 고정된 `decide()`로 발생 불가. §T7 DoD와 §T10이 서로 다른 데모 3종을 정의 | HIGH |
| F10 | "금액 불명 → `estimated_cost=0`"이 §4 필드 정의("보수 추정치")·P4와 정면 위반. 가장 위험한 물건에서 가장 낙관적 숫자 | HIGH |
| F11 | "UNCERTAIN 항목 ≥ 2"의 계수 집합 미정의 + `TenantInfo.confidence` 기본값이 UNCERTAIN이라 자동 C 발생 | HIGH |
| F12~F22 | binding_constraint 이중 규칙, key_numbers 4 vs 5 불일치, 주석 불일치, 부가세율 4.3% vs §2.4의 4.6%, 10만원 내림이 최저가 미만 유발, TCO에 대출이자 누락, fixture 필수 필드 미기재, 저감률 30% vs config 20%, `vacancy_months_formula` 문자열 eval, `eviction_difficulty` 자기모순, 단조성 assert 부재 | MEDIUM~LOW |

**F1 산술 증명 (문서 내 수치만 사용)**
```
f(bid) = cash + loan(bid) − cost(bid) − bid
       = 60,000,000 + (0.7·bid − 28,000,000) − (0.027·bid + 2,364,000) − bid
       = 29,636,000 − 0.327·bid
f(224,000,000) = 29,636,000 − 73,248,000 = −43,612,000  < 0
→ §T4 step6 예외분기 → max_bid_price = 0 → decide()에서 0 < p10 → RED, None
```
**핵심**: `max_bid`가 물건 가격과 무관하게 `현금`과 `방공제`로만 결정된다.
user_young(현금 6천만·대전 방공제 2,800만) → **감당 상한 ≈ 9,060만원 고정**.
어떤 config 조정으로도(방공제 0, LTV 0.80) GREEN이 나오지 않는다.

### L2 (외부 의존) — 가설 반증, 그러나 내부 블로커 4건 발견

**반증 실측 증거**
```
✓ pip/네트워크 정상        lightgbm whl 다운로드 성공
✓ lightgbm 3.13 무관       py3-none-win_amd64 (컴파일 불필요), fit/predict 성공
✓ streamlit 실제 기동      HTTP 200, /_stcore/health = ok
✓ plotly 6.9 ↔ st 1.37     상호운용 확인, 한글은 브라우저 렌더라 폰트 문제 없음
✓ parquet 한글 왕복        무손실
✓ gh private push 가능     repo 스코프 확인
```

**대신 발견된 진짜 블로커 (전부 내부 문제)**

| ID | 결함 | 심각도 |
|----|------|--------|
| F1 | **인터프리터 3중 분열** — `python`=3.13.3, `pytest`=conda base 3.13.11, `streamlit`=Python312. T0 DoD와 T8 DoD가 **물리적으로 다른 환경**에서 실행됨. 게다가 Python312 전역은 사용자의 HF/torch 작업환경이라 전역 설치 시 기존 프로젝트 파괴 | BLOCKER |
| F2 | **cp949 인코딩** — `yaml.safe_load(open(...))` / `Path.read_text()` 즉시 `UnicodeDecodeError` 재현. `print("🟢")`는 프로세스 사망. pytest 한글 assert 전부 모지바케 | BLOCKER |
| F3 | `streamlit run app/main.py`는 `app/`을 sys.path[0]로 잡음 → `ModuleNotFoundError: No module named 'src'`. T8 DoD 첫 줄에서 사망 | BLOCKER |
| F8 | A2 **zero-data 경로 미정의** — HEURISTIC 폴백도 데이터를 요구. 0행이면 `median()` → NaN → `int(NaN)` ValueError. "파이프라인은 절대 중단되지 않는다"가 정면으로 깨짐 | BLOCKER |
| F5 | pdfplumber가 pillow 12를 끌어와 `streamlit requires pillow<11` 충돌 실측. 그런데 v1 기본 입력은 이미 `.txt` | HIGH |
| F6 | ORCH가 A2를 import → A2 top-level `import lightgbm` → DLL 실패 시 A1/A3/A4/A5까지 전멸 | HIGH |
| F9 | `region_map` 미등재 → A3 KeyError (L1-F6와 동일 발견, 교차 확인됨) | HIGH |
| F12 | **A1 룰 파서 자기채점 함정** — 우리가 문서를 쓰고 우리가 정규식을 쓰면 recall 100%는 항등식 | HIGH |
| F4 | Python 3.13은 pandas 3.0 세계라 `streamlit requires pandas<3` 충돌 → **3.12 고정 권장** | MEDIUM |
| F7 | pyarrow는 streamlit 강제 의존이라 이미 설치됨(비용 sunk). 단 parquet 파일 부재 방어 필요 | MEDIUM |
| F10 | `discount_per_fail=0.20` vs fixture의 30% 저감 모순 (L1-F19와 교차 확인) | HIGH |
| F13 | 디스크 여유 9.4GB / 455GB(98% 사용). venv 약 1GB 필요 | LOW |

### L3 (검증 전제 붕괴) — 실측으로 확정, BLOCKER 4

**실측 재현 결과**

| 검증 항목 | 결과 |
|---|---|
| §7.2 P5 하드코딩 grep | **오탐 3/3, 미탐 11/11 — 완전 역전.** `0.045/0.001/0.002/0.004/0.01/0.04/0.12/3000/45/300000000` 등 실제 규제상수 11개 중 **0개 적발**. 반면 `SIMILARITY_THRESHOLD=0.5`, `__version__="0.7.0"` 등 무관한 3줄 전부 적발. 게다가 `grep -v config`가 `config_loader.py`를 통째로 면제 |
| §7.4 금지어 검사 | 회피 9종 중 **8종 통과(미탐)**. `낙찰확률`(공백제거), `낙찰 될 확률`(어미분리), `낙찰가능성`, **`이 금액이면 낙찰됩니다`(§1.4가 직접 금지한 문구인데 목록에 없음)** 전부 통과. "대소문자 무시"는 한국어에 무의미 |
| §T2 `min(set)` 결정성 | `PYTHONHASHSEED` 1~5에서 **base_right가 MORTGAGE / SEIZURE / AUCTION_START로 갈림.** 등기일 동점 시 집합 순회 순서 의존. §2.2(b)가 "같은 날" 케이스를 스스로 인정하므로 실제 발생함. 말소기준권리가 바뀌면 대항력→인수보증금→등급→신호등이 전부 뒤집힘. §3.5의 "2회 호출" 테스트로는 **영원히 못 잡음** |
| §5.1 취득세 경계 연속성 | 6억·9억 모두 **연속 확인**(6e8: 6,000,000 일치 / 9e8: 27,000,000 일치) → 골든값 없이 테스트 가능한 진짜 불변식 |

**DoD 피해 정량 (약 25개 절 기준)**
```
자기채점 순환으로 공허(vacuous) ████████████████████  9개 (36%)
현재 측정 불가                  ███████████████       7개 (28%)
사람 손 필요/자동화 불가         █████████             4개 (16%)
실제로 기계적 의미가 있음        ███████               5개 (20%)
```
→ **DoD의 80%가 정확성에 대해 아무것도 말하지 않는다.**

특히 §T0-6의 mock이 fixture JSON을 그대로 로드해 반환하므로, §T7 "mock 기반 E2E 스냅샷 일치"는 *fixture를 읽어서 fixture와 같은지 비교*하는 완전한 항진명제다.

**대체 산출물**: 골든값 없는 불변식 카탈로그 **50개(I1~I50)** 도출. 이것이 이 트레이스의 최대 수확이다.

---

## Evidence Against / Missing Evidence

**L1에 대한 반증 — 견고해서 손대면 안 되는 것들**

| 검증 대상 | 결과 |
|---|---|
| §T4 f(bid) 단조감소 주장 | **참.** `f' = loan' − C' − 1 ≤ 0.70 − 0.017 − 1 = −0.317 < 0`. 어디서도 안 깨짐 |
| 6~9억 슬라이딩 세율이 기울기 1 초과? | **없음.** `d(tax)/d(bid)`가 6억 0.05, 9억 0.09로 최대 9% |
| 6억/9억 경계 불연속? | **연속.** 양쪽 정확히 일치(6,000,000 / 27,000,000) |
| 이분탐색 상한 `cash×20+appraisal×2`에서 f(upper)≥0 가능? | **불가능.** cash=0 최악에서도 `f(2A) ≤ 0.7A − 2A = −1.3A < 0`. 전제 충족 |
| `total_assumed_cost` 이중 계상 | **없음.** §T5-6 "재계산 금지" + §T4-5 주석이 경계를 정확히 규정 |
| A3↔A4 순환 의존 | **차단됨.** `cost_fn` 주입이 4곳에서 일관 기술 |
| `method` Literal에 3번째 값 필요? | **불필요.** band 실패는 `band=None`으로 처리 |

**L2에 대한 반증**: 위 실측 증거 블록 참조. **외부 의존 부재로 데모가 안 도는 것이 아니다.** 다만 살아남는 부분 하나 — §T3의 "홀드아웃 커버리지 ≥75%"와 §T9의 골든셋 교차검증은 데이터 없이 **측정 자체가 불가능**하며 폴백으로 우회 불가.

---

## Per-Lane Critical Unknowns

- **Lane 1**: **T0 fixture의 기대값이 규범(normative)인가, §T4/§T5 공식의 파생물(derived)인가?** §0의 우선순위 규칙(§4 > §1.3 > §6)은 §6 내부의 T0 vs T4 충돌에 적용되지 않는다 — 둘 다 §6이다. 문서만으로는 판정 불가.
- **Lane 2**: **데모 시점까지 공공데이터 인증키로 실제 낙찰 사례를 확보할 것인가, 아니면 §T3 DoD(커버리지·MAPE)를 공식적으로 스코프 아웃할 것인가?** 이 결정이 A2의 정체성을 바꾼다 — 데이터가 오면 "LightGBM 분위수 회귀", 안 오면 "사건 구조 사전(prior)"이며 후자는 ML 요소가 0이다.
- **Lane 3**: **마감 전까지 실 매각물건명세서를 몇 건 확보 가능한가 + 법원경매정보 약관이 허용하는가.** 이것 없이는 자기채점 순환을 **외부에서** 깰 수단이 없다. 내부 산술감사·불변식만으로는 "규칙 자체가 법리적으로 맞는가"를 절대 검증 못 한다.

---

## Rebuttal Round

- **선두(L3)에 대한 최선의 반박**: "검증이 허술해도 코드는 돌아간다. 공모전은 데모가 핵심이지 테스트 커버리지가 아니다."
- **왜 선두가 버텼는가**: 반박이 성립하지 않는다. 이 프로젝트의 **차별점 ②가 "판정은 룰 — 신뢰성"**이고 **주제 4가 금융소비자 보호**다. 검증할 수 없는 신뢰성 주장은 심사에서 가장 먼저 찔린다. 게다가 L3가 발견한 `PYTHONHASHSEED` 비결정성은 **데모 중 같은 물건이 다른 등급으로 나오는** 실제 사고로 이어진다 — 검증 문제가 아니라 실행 문제다.
- **L1 vs L3 수렴**: 두 레인이 독립적으로 같은 결론에 도달했다 — `region_map` KeyError(L1-F6 = L2-F9), 저감률 모순(L1-F19 = L2-F10), 자기채점(L2-F12 = L3-F1). 3중 교차 확인된 항목은 확정으로 취급한다.

---

## Convergence / Separation Notes

- L1과 L3은 **같은 뿌리**의 다른 증상이다: "정의를 적어두면 강제된다"는 착각. `total_assumed_cost`는 주석으로 공식이 적혀 있지만 평범한 `int` 필드이고, 금지어는 목록이 있지만 정규화가 없고, P5는 규칙이 있지만 grep이 역전돼 있다. → **모든 규칙은 실행 가능한 테스트로 번역되어야 한다**가 단일 처방.
- L2는 분리된다. 외부 의존 문제가 아니라 **환경 위생(인터프리터/인코딩/sys.path)** 문제로 재분류.

---

## Most Likely Explanation

**process.md는 설계 문서로서는 우수하나, 검증 계약(DoD)이 자기참조적이라 "완료"를 선언해도 정확성이 보장되지 않는다. 동시에 T0 fixture와 T4 공식이 산술적으로 모순되어, 문자 그대로 구현하면 대표 데모 시나리오가 RED로 나온다.**

원인은 단일하다 — **문서가 "값"을 규범으로 적었지만(fixture 기대값·예시 config), 그 값들이 문서 안의 "공식"과 대조된 적이 없다.** 값과 공식이 처음 만나는 순간이 구현 시점이고, 그때 충돌이 드러난다.

---

## Critical Unknown

**T0 fixture(값)와 T4/T5(공식) 중 어느 쪽을 규범으로 삼을 것인가.**

- fixture가 규범 → §T4의 계산식(방공제 기본적용, LTV 0.70, 비용비율 0.027)과 §2.3의 보수 가정까지 재검토 대상
- 공식이 규범 → §T0-4/7 fixture 수치 전부 + §T7 DoD + §T10 데모 대본 3종이 재작성 대상

이 결정 없이는 T0을 시작할 수 없고, T0 없이는 T2~T7 어느 것도 병합 기준이 없다.

---

## Recommended Discriminating Probe

`user_young`의 감당 상한을 **물건과 무관하게** 닫힌형으로 계산해 보는 것:
```
max_bid = (cash − room_deduction − 정액비용) / (1 + 비용비율 − ltv)
        = (60,000,000 − 28,000,000 − 2,364,000) / 0.327 ≈ 90,600,000원
```
이 한 줄이 "어떤 fixture 물건을 만들어야 GREEN이 나오는가"를 즉시 결정한다. 물건 가격을 낮추는 것으로는 부족하고(p50 ≤ max_bid 조건이 추가로 필요), **현금 또는 방공제 가정 중 하나를 건드려야 한다**는 사실도 이 식에서 바로 읽힌다.
