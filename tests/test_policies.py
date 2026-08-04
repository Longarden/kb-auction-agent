"""원칙(P1~P8)을 문장이 아니라 실행 가능한 검사로 강제한다.

process.md §7.2 가 제안한 grep 검사는 실측 결과 오탐 3/3 · 미탐 11/11 로
완전히 역전되어 있었다(0.045, 0.001, 3000 같은 실제 규제상수를 하나도 못 잡고,
similarity 0.5 나 버전 문자열 "0.7.0" 만 잡았다). 그래서 두 가지로 교체한다.
  (a) AST 리터럴 스캔 — 주석·문자열 오탐이 구조적으로 불가능
  (b) config 민감도 — 규제값을 바꿨는데 결과가 안 변하면 코드가 config를 안 읽는 것
"""
from __future__ import annotations

import ast
import copy
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.utils.io import REPO_ROOT

# 인덱싱·단위환산에만 쓰이는 구조적 상수. 규제 수치가 아니다.
ALLOWED_LITERALS = {0, 1, 2, 12, 100, -1}
PURE_RULE_PACKAGES = ("src/agents/financing", "src/agents/tco")


def _py_files(rel: str) -> list[Path]:
    return sorted((REPO_ROOT / rel).rglob("*.py"))


# ── P5: 규제 수치 하드코딩 금지 ────────────────────────────────
def test_p5_no_numeric_literals_in_rule_engines():
    """(a) AST 리터럴 스캔. 문자열 매칭이 아니라 구문 구조를 본다."""
    offenders: list[str] = []
    for pkg in PURE_RULE_PACKAGES:
        for path in _py_files(pkg):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant):
                    continue
                if isinstance(node.value, bool) or not isinstance(
                    node.value, (int, float)
                ):
                    continue
                if node.value in ALLOWED_LITERALS:
                    continue
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{node.lineno} → {node.value!r}")
    assert not offenders, (
        "규제 수치로 보이는 숫자 리터럴이 룰 엔진에 있다. config 에서 읽어라:\n"
        + "\n".join(offenders)
    )


# (key_path, 섭동값, 그 값이 실제로 결과에 영향을 주는 케이스)
# 섭동 방향에 주의: LTV 를 1 이상으로 올리면 이분탐색 단조성 가드가 먼저 걸리고,
# DSR 을 올리면 LTV 가 계속 바인딩이라 결과가 안 변한다. 각 파라미터가
# 실제로 지배적이 되는 방향으로 흔들어야 의미 있는 검사가 된다.
SENSITIVITY_CASES = [
    (("ltv", "APARTMENT", "NONE", "house_0_1"), 0.30, "case_001_clean"),
    (("loan", "dsr_limit"), 0.02, "case_001_clean"),
    (("loan", "assumed_rate_annual"), 0.12, "case_001_clean"),
    (("room_deduction", "METRO"), 5_000_000, "case_001_clean"),
    (("acquisition_tax", "house", "tier_1_rate"), 0.05, "case_001_clean"),
    (("acquisition_tax", "commercial_rate"), 0.10, "case_003_lien"),
]


@pytest.mark.parametrize("key_path,new_value,case_name", SENSITIVITY_CASES)
def test_p5_config_sensitivity(cfg, cases, users, key_path, new_value, case_name):
    """(b) 규제값을 섭동하면 출력이 반드시 변한다.

    'if zone == "SPECULATION": ltv = 0.4' 같은 분기 하드코딩은 AST 스캔도
    문자열 grep 도 못 잡지만 이 테스트는 잡는다. "config 를 읽는다"를
    선언이 아니라 관측 가능한 행동으로 정의하는 것이다.
    """
    from src.agents.rights_analysis import run as rights_run
    from src.agents.tco import cost_function
    from src.utils.config import Config

    case = cases[case_name]
    user = users["user_invest"] if case_name == "case_003_lien" else users["user_young"]

    def snapshot(config) -> str:
        rights = rights_run(case, config)
        from functools import partial

        from src.agents.financing import run as fin_run

        cost_fn = partial(cost_function, case=case, user=user, rights=rights, cfg=config)
        fin = fin_run(case, user, rights, cost_fn, config)
        tco = cost_function(max(fin.max_bid_price, case.min_bid_price),
                            case, user, rights, config)
        return (f"{fin.max_bid_price}|{fin.max_loan}|{fin.max_loan_by_dsr}"
                f"|{fin.monthly_payment_at_max}|{tco.total_upfront_excl_bid}")

    base = snapshot(cfg)

    reg = copy.deepcopy(cfg.regulation)
    node = reg
    for k in key_path[:-1]:
        node = node[k]
    original = node[key_path[-1]]
    node[key_path[-1]] = new_value

    perturbed = Config(regulation=reg, heuristics=copy.deepcopy(cfg.heuristics))
    after = snapshot(perturbed)

    assert after != base, (
        f"{'.'.join(map(str, key_path))} 를 {original} → {new_value} 로 바꿨는데 "
        f"결과가 그대로다({base}). 코드가 config 를 읽지 않고 있을 가능성이 높다."
    )


# ── P2: 금액 계산 모듈에 LLM 금지 ──────────────────────────────
def test_p2_no_llm_in_money_modules():
    """AST 임포트 스캔. 주석·문서 오탐 없이 실제 import 만 본다."""
    banned = {"anthropic", "openai", "src.llm"}
    offenders: list[str] = []
    for pkg in list(PURE_RULE_PACKAGES) + ["src/agents/price_band"]:
        for path in _py_files(pkg):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if any(name == b or name.startswith(b + ".") for b in banned):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} → {name}")
    assert not offenders, "금액 계산 모듈에 LLM 의존이 있다:\n" + "\n".join(offenders)


def _imported_modules(path) -> list[tuple[int, str]]:
    """AST 로 실제 import 만 뽑는다. 주석·독스트링 오탐이 구조적으로 불가능하다."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.lineno, node.module))
    return out


def test_p2_financing_does_not_import_tco():
    """A3 는 A4 를 import 하지 않고 cost_fn 을 주입받는다(순환 의존 차단)."""
    offenders: list[str] = []
    for path in _py_files("src/agents/financing"):
        for lineno, mod in _imported_modules(path):
            if "agents.tco" in mod:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} → {mod}")
    assert not offenders, "A3 가 A4 를 직접 import 한다:\n" + "\n".join(offenders)


# ── 인코딩 정책 ────────────────────────────────────────────────
def test_encoding_no_bare_open():
    """Windows 기본이 cp949 라 encoding 없는 open() 은 한글 파일에서 즉시 죽는다.

    AST 로 호출 노드만 본다. 문자열 매칭은 독스트링의 'open()' 이라는
    단어까지 잡아서 쓸 수 없다(실제로 io.py 자기 독스트링에 걸렸다).
    """
    offenders: list[str] = []
    for rel in ("src", "app"):
        for path in _py_files(rel):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # 내장 open() 만 대상. pdfplumber.open() 같은 속성 호출은
                # 텍스트 파일 opener 가 아니므로 제외한다.
                if getattr(node.func, "id", None) != "open":
                    continue
                if any(kw.arg == "encoding" for kw in node.keywords):
                    continue
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        "encoding 을 지정하지 않은 open() 이 있다. src.utils.io 를 써라:\n"
        + "\n".join(offenders)
    )


def test_scripts_force_utf8():
    """콘솔 인코딩 사고의 실제 해법은 실행 스크립트가 UTF-8 을 강제하는 것이다.

    Windows 기본 콘솔은 cp949 라, 한글 assert 메시지는 모지바케가 되고
    이모지 한 글자에는 프로세스가 죽는다. PYTHONUTF8=1 이 그 근본 해결이다.
    """
    for name in ("test.cmd", "demo.cmd"):
        body = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "PYTHONUTF8=1" in body, f"scripts/{name} 에 PYTHONUTF8=1 이 없다"
        assert ".venv" in body, f"scripts/{name} 이 레포 로컬 .venv 를 쓰지 않는다"


def test_no_emoji_in_src():
    """이모지는 cp949 로 인코딩이 불가능해 로그 한 줄로 프로세스를 죽인다.

    반면 ★ · — 같은 기호는 cp949 에 있거나 UTF-8 강제로 해결되는 표기 문제라
    금지하지 않는다. 실제로 프로세스를 죽이는 이모지 블록만 대상으로 한다.
    """
    emoji = re.compile("[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF️]")
    offenders: list[str] = []
    for path in _py_files("src"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if emoji.search(node.value):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        "src/ 문자열에 이모지가 있다(app/ 에서만 허용):\n" + "\n".join(offenders)
    )


def test_no_print_in_src():
    """print() 는 인코딩 사고의 주 경로이자 로그 레벨 제어가 안 된다."""
    offenders: list[str] = []
    for path in _py_files("src"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, "src/ 에 print() 가 있다. logging 을 써라:\n" + "\n".join(offenders)


# ── import 위생 ────────────────────────────────────────────────
def test_orchestrator_import_stays_light():
    """무거운/네이티브 의존이 오케스트레이터 import 만으로 딸려오면 안 된다.

    A2 가 top-level 로 lightgbm 을 import 하면 DLL 로드 실패 하나로
    A1/A3/A4/A5 까지 전멸한다. '파이프라인은 A2 때문에 멈추지 않는다'가
    import 시점에 이미 깨지는 것이다.
    """
    code = (
        "import sys, src.orchestrator;"
        "heavy=('lightgbm','pdfplumber','streamlit','plotly','sklearn','anthropic');"
        "leaked=[m for m in heavy if m in sys.modules];"
        "print(','.join(leaked));"
        "raise SystemExit(1 if leaked else 0)"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT,
        capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode == 0, f"무거운 모듈이 딸려왔다: {r.stdout.strip()}"


def test_src_never_imports_ui():
    """streamlit/plotly 는 app/ 에서만. src/ 가 UI 에 의존하면 테스트가 UI 를 요구하게 된다."""
    offenders: list[str] = []
    for path in _py_files("src"):
        src = path.read_text(encoding="utf-8")
        for lib in ("streamlit", "plotly"):
            if re.search(rf"^\s*(import|from)\s+{lib}", src, re.MULTILINE):
                offenders.append(f"{path.relative_to(REPO_ROOT)} → {lib}")
    assert not offenders, "src/ 가 UI 라이브러리를 import 한다:\n" + "\n".join(offenders)


def test_no_today_in_deterministic_modules():
    """A1/A3/A4 가 시계를 읽으면 스냅샷이 날짜와 함께 조용히 썩는다."""
    offenders: list[str] = []
    for pkg in ("src/agents/rights_analysis", "src/agents/financing",
                "src/agents/tco", "src/orchestrator"):
        for path in _py_files(pkg):
            src = path.read_text(encoding="utf-8")
            if re.search(r"\.today\(\)|datetime\.now\(", src):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, "결정적이어야 할 모듈이 시계를 읽는다:\n" + "\n".join(offenders)
