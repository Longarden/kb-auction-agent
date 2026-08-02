"""엔드투엔드: 스키마 검증 · held-out 일반화 · 크로스프로세스 결정성 · 데모 시나리오."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from src.schemas.core import PropertyCase, RiskGrade, Signal, UserProfile
from src.utils.io import REPO_ROOT, read_json
from tests.conftest import CASE_NAMES, USER_NAMES


# ── 스키마 ─────────────────────────────────────────────────────
def test_all_sample_cases_validate():
    for name in CASE_NAMES:
        case = PropertyCase.model_validate(read_json(f"data/samples/{name}/case.json"))
        assert case.min_bid_price <= case.appraisal_price, name
        assert case.min_bid_price > 0 and case.appraisal_price > 0, name
        assert (REPO_ROOT / case.documents.sale_spec_path).exists(), name


def test_all_sample_users_validate():
    raw = read_json("data/samples/users.json")
    assert set(raw) == set(USER_NAMES)
    for name, payload in raw.items():
        assert "label" in payload, f"{name}: UI 표시용 label 이 없다"
        user = UserProfile.model_validate(
            {k: v for k, v in payload.items() if k != "label"}
        )
        assert user.annual_income > 0 and user.cash_available >= 0, name


def test_policies_yaml_is_well_formed():
    from src.utils.io import read_yaml

    doc = read_yaml("data/policies.yaml")
    products = doc["products"] if isinstance(doc, dict) else doc
    assert len(products) >= 5
    ids = [p["product_id"] for p in products]
    assert len(ids) == len(set(ids)), "product_id 중복"
    for p in products:
        assert p["category"] in ("POLICY_LOAN", "BANK_LOAN", "GUARANTEE"), p["product_id"]
        # 조건을 확인하지 않았으면 확인했다고 표시하지 않는다
        assert "verified" in p and "source_url" in p and "checked_date" in p


# ── held-out 일반화 ────────────────────────────────────────────
def test_held_out_case_matches_semantically_identical_case(cfg, cases):
    """case_004_noise 는 case_002 와 의미가 같고 표기만 전부 다르다.

    파서 개발 중 이 파일을 열지 않았다. 통과한다면 정규식이 특정 표기가 아니라
    문서 구조를 잡았다는 증거다. 실패하면 파서를 일반화해야지 이 케이스를
    특수 처리하면 안 된다.

    표기 차이: 2022.09.05. vs 2022년 9월 5일 / 60,000,000원 vs 금6,000만원 /
              전각 숫자 / 셀 줄바꿈 / 페이지 머리말 / 구분선 문자
    """
    from src.agents.rights_analysis import run as rights_run

    ref = rights_run(cases["case_002_tenant"], cfg)
    heldout = rights_run(cases["case_004_noise"], cfg)

    assert heldout.risk_grade == ref.risk_grade == RiskGrade.C
    assert heldout.total_assumed_cost == ref.total_assumed_cost
    assert heldout.eviction_difficulty == ref.eviction_difficulty
    assert len(heldout.tenants) == len(ref.tenants) == 1

    a, b = heldout.tenants[0], ref.tenants[0]
    assert a.move_in_date == b.move_in_date
    assert a.deposit == b.deposit
    assert a.opposing_power is b.opposing_power is True
    assert a.dividend_demanded is b.dividend_demanded is False
    assert a.expected_assumed_deposit == b.expected_assumed_deposit
    assert heldout.base_right.registered_date == ref.base_right.registered_date


# ── 결정성 ─────────────────────────────────────────────────────
def _run_one(case_name: str, user_name: str, hash_seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONUTF8="1")
    r = subprocess.run(
        [sys.executable, "-m", "tests.helpers.run_one", case_name, user_name],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    return hashlib.sha256(r.stdout.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("case_name", ["case_002_tenant", "case_003_lien"])
def test_determinism_across_processes_and_hash_seeds(case_name):
    """같은 프로세스에서 순수함수를 두 번 부르는 테스트는 아무것도 못 잡는다.

    실제 비결정성은 프로세스 경계 밖에 있다. 실측에서 말소기준권리 후보를
    set 으로 모으자 PYTHONHASHSEED 에 따라 base_right 가 MORTGAGE/SEIZURE/
    AUCTION_START 로 갈렸다. 말소기준권리가 바뀌면 대항력 → 인수보증금 →
    등급 → 신호등이 전부 뒤집힌다.
    """
    digests = {_run_one(case_name, "user_young", str(seed)) for seed in range(5)}
    assert len(digests) == 1, f"{case_name}: 해시시드에 따라 결과가 달라진다 {digests}"


# ── 데모 시나리오 ──────────────────────────────────────────────
DEMO = [
    ("case_001_clean", "user_young", Signal.GREEN, "감당 가능 — 청년 실수요자"),
    ("case_002_tenant", "user_invest", Signal.YELLOW, "권리 리스크로 하향 — 보증금 인수"),
    ("case_003_lien", "user_young", Signal.RED, "유치권 D등급 — 입찰 비권장"),
    ("case_001_clean", "user_tight", Signal.RED, "자금 부족 — 최저가 미달"),
]


@pytest.mark.parametrize("case_name,user_name,expected,label", DEMO)
def test_demo_scenarios(cfg, cases, users, case_name, user_name, expected, label):
    from src.orchestrator import run as orch_run

    verdict = orch_run(cases[case_name], users[user_name], cfg)
    assert verdict.signal == expected, f"{label}: {verdict.signal} 이 나왔다"
    if expected == Signal.RED:
        assert verdict.recommended_max_bid is None
    else:
        assert verdict.recommended_max_bid is not None
        assert verdict.recommended_max_bid >= cases[case_name].min_bid_price


def test_full_matrix_has_no_exception(cfg, cases, users):
    """4 케이스 × 3 유저 전 조합. UI 없이 데모 실패 원인 대부분을 잡는다."""
    from src.orchestrator import run as orch_run

    results = {}
    for cn, case in cases.items():
        for un, user in users.items():
            v = orch_run(case, user, cfg)
            results[f"{cn}x{un}"] = v.signal.value
            assert v.report_markdown.strip()
            assert v.disclaimer
    assert len(results) == 12
