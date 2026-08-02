"""금지어 가드. process.md §7.4 의 원안이 실제로 무엇을 놓쳤는지 고정해 둔다.

원안은 "대소문자 무시" 서브스트링 검사였다. 한국어에 대소문자는 없고,
실제 회피면은 띄어쓰기와 어미 변화다. 아래 EVASIONS 는 원안 검사를
통과했던 실측 목록이며, 지금 가드는 전부 잡아야 한다.
"""
from __future__ import annotations

import pytest

from src.utils.guard import (
    ForbiddenPhraseError,
    assert_clean,
    find_band_modifier_violations,
    find_violations,
    normalize,
)

# 원안 서브스트링 검사를 통과했던 회피 표현들
EVASIONS = [
    "낙찰확률은 87%입니다",              # 띄어쓰기 제거
    "낙찰 될 확률이 높습니다",            # 어미 분리
    "낙찰가능성이 매우 큽니다",
    "낙찰될 가능성이 높습니다",
    "이 금액이면 낙찰됩니다",             # §1.4가 직접 금지했으나 §7.4 목록에는 없던 문구
    "낙찰  확률",                        # 이중 공백
    "낙찰\n확률",                        # 개행 삽입
    "낙  찰  확  률",
    "winning bid probability",           # 원안이 유일하게 잡던 것
]

CLEAN = [
    "감당 가능 입찰 상한은 1억 5,270만원입니다.",
    "실질 취득가는 낙찰가에 인수 금액을 더한 값입니다.",
    "이 물건에는 인수되는 권리가 없습니다.",
    "월 예상 원리금은 39만원입니다.",
]


@pytest.mark.parametrize("text", EVASIONS)
def test_evasions_are_caught(text):
    assert find_violations(text), f"회피 표현을 놓쳤다: {text!r}"


@pytest.mark.parametrize("text", CLEAN)
def test_clean_text_passes(text):
    assert not find_violations(text), f"정상 문구를 오탐했다: {text!r}"
    assert_clean(text, check_band=False)


def test_normalize_removes_all_whitespace():
    assert normalize("낙 찰\t확\n률") == "낙찰확률"
    assert normalize("Ｗｉｎｎｉｎｇ Ｂｉｄ") == "winningbid"


def test_band_mention_requires_reference_modifier():
    """밴드를 언급하면서 '참고' 수식어가 없으면 시장 예측처럼 읽힌다."""
    bad = "예상 낙찰가 구간은 1.12억 ~ 1.60억입니다."
    good = "예상 낙찰가 구간은 1.12억 ~ 1.60억입니다. 이는 참고 정보이며 예측이 아닙니다."
    assert find_band_modifier_violations(bad)
    assert not find_band_modifier_violations(good)


def test_assert_clean_raises_with_location():
    with pytest.raises(ForbiddenPhraseError) as exc:
        assert_clean("낙찰확률 87%", where="report_markdown")
    assert "report_markdown" in str(exc.value)


def test_every_user_facing_string_is_clean(cfg, cases, users):
    """FinalVerdict 전체를 재귀 순회한다.

    원안은 report.py 렌더 결과만 검사했다. rationale, caveats, warnings,
    상품 rate_note 등 LLM 이나 자유 문자열이 들어가는 경로가 전부 무검사였다.
    """
    from src.utils.guard import assert_object_clean
    from src.orchestrator import run as orch_run

    for cn, case in cases.items():
        for un, user in users.items():
            verdict = orch_run(case, user, cfg)
            assert_object_clean(verdict, where=f"{cn}x{un}")


def test_ui_source_strings_are_clean():
    """app/ 의 하드코딩 UI 문구도 사용자 노출이다."""
    import ast

    from src.utils.io import REPO_ROOT

    tree = ast.parse((REPO_ROOT / "app" / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            violations = find_violations(node.value)
            assert not violations, f"app/main.py:{node.lineno} → {violations}"
