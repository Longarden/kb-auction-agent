"""SafeBid 데모 UI (process.md §6 T8).

주의: streamlit 은 이 파일이 있는 app/ 을 sys.path[0] 에 넣는다. 레포 루트가
아니므로 아래 3줄 없이는 `import src...` 가 실패한다. 다른 import 보다 앞에 둔다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from src.orchestrator import run_detailed  # noqa: E402
from src.orchestrator.verdict import RED_REASON_PREFIX  # noqa: E402
from src.schemas.core import Confidence, PropertyCase, Signal, UserProfile  # noqa: E402
from src.utils.config import load_config  # noqa: E402
from src.utils.io import read_json, resolve  # noqa: E402

MAN = 10_000  # 만원 -> 원

SAMPLES_DIR = "data/samples"
USERS_JSON = "data/samples/users.json"

FONT = dict(family="Malgun Gothic, Apple SD Gothic Neo, sans-serif")

SIGNAL_STYLE = {
    Signal.GREEN: ("🟢", "#1a7f37", "#e6f4ea", "검토 가능"),
    Signal.YELLOW: ("🟡", "#9a6700", "#fff8e1", "조건부 검토"),
    Signal.RED: ("🔴", "#b42318", "#fdecea", "입찰 비권장"),
}

CONFIDENCE_STYLE = {
    Confidence.CONFIRMED: ("#1a7f37", "#e6f4ea", "확인됨"),
    Confidence.LIKELY: ("#9a6700", "#fff8e1", "추정"),
    Confidence.UNCERTAIN: ("#b54708", "#fff4ed", "판단 불가"),
}

PURPOSE_LABELS = {
    "OWNER_OCCUPY": "실거주",
    "INVEST": "투자(임대)",
    "BUSINESS": "사업장 확보",
}


# ── 데이터 로딩 (JSON 만 캐시한다. 에이전트 run() 은 절대 캐시하지 않는다 — P7) ──
@st.cache_data(show_spinner=False)
def load_users() -> dict:
    return read_json(USERS_JSON)


@st.cache_data(show_spinner=False)
def list_case_dirs() -> list[str]:
    """data/samples 아래에서 case.json 을 가진 디렉토리 이름. 정렬은 항상 결정적."""
    root = resolve(SAMPLES_DIR)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "case.json").is_file())


@st.cache_data(show_spinner=False)
def load_case_dict(name: str) -> dict:
    return read_json(f"{SAMPLES_DIR}/{name}/case.json")


def won(value) -> str:
    if value is None:
        return "정보 없음"
    return f"{int(value):,}원"


# ── 사이드바 ──────────────────────────────────────────────────────
def _apply_profile(raw: dict) -> None:
    """프리셋/기본값을 위젯 session_state 로 옮긴다(만원 단위로 환산해서 표시)."""
    st.session_state["income_man"] = raw["annual_income"] // MAN
    st.session_state["cash_man"] = raw["cash_available"] // MAN
    st.session_state["debt_man"] = raw["existing_annual_debt_payment"] // MAN
    st.session_state["owned"] = int(raw["owned_house_count"])
    st.session_state["purpose"] = raw["purpose"]
    st.session_state["age"] = int(raw["age"])
    st.session_state["first_time"] = bool(raw["is_first_time_buyer"])
    st.session_state["loan_years"] = int(raw["target_loan_years"])


def sidebar_user() -> UserProfile:
    users = load_users()

    # 위젯은 key 만 쓰고 value 는 넘기지 않는다. 두 경로로 값을 주면
    # streamlit 이 "default value + session_state" 경고를 낸다.
    if "income_man" not in st.session_state:
        first = next(iter(users.values())) if users else None
        _apply_profile(
            first
            or {
                "annual_income": 42_000_000, "cash_available": 80_000_000,
                "existing_annual_debt_payment": 0, "owned_house_count": 0,
                "purpose": "OWNER_OCCUPY", "age": 28,
                "is_first_time_buyer": True, "target_loan_years": 30,
            }
        )

    st.sidebar.header("내 자금 상황")
    st.sidebar.caption("프리셋으로 채운 뒤 값을 고쳐도 됩니다.")
    for key, raw in users.items():
        if st.sidebar.button(raw.get("label", key), key=f"preset_{key}", width="stretch"):
            _apply_profile(raw)
            st.rerun()

    st.sidebar.divider()
    st.sidebar.number_input("세전 연소득 (만원)", min_value=0, step=100, key="income_man")
    st.sidebar.number_input("동원 가능 현금 (만원)", min_value=0, step=100, key="cash_man")
    st.sidebar.number_input("기존 대출 연 원리금 (만원)", min_value=0, step=50, key="debt_man")
    st.sidebar.number_input("보유 주택 수", min_value=0, max_value=10, step=1, key="owned")
    st.sidebar.selectbox(
        "이용 목적",
        options=list(PURPOSE_LABELS),
        format_func=lambda k: PURPOSE_LABELS[k],
        key="purpose",
    )
    st.sidebar.number_input("나이", min_value=19, max_value=99, step=1, key="age")
    st.sidebar.checkbox("생애최초 구입", key="first_time")
    st.sidebar.number_input("희망 대출 기간 (년)", min_value=5, max_value=50, step=5, key="loan_years")

    s = st.session_state
    return UserProfile(
        annual_income=int(s["income_man"]) * MAN,
        cash_available=int(s["cash_man"]) * MAN,
        existing_annual_debt_payment=int(s["debt_man"]) * MAN,
        owned_house_count=int(s["owned"]),
        purpose=s["purpose"],
        age=int(s["age"]),
        is_first_time_buyer=bool(s["first_time"]),
        target_loan_years=int(s["loan_years"]),
    )


# ── 차트 ─────────────────────────────────────────────────────────
def band_chart(band, max_bid: int) -> go.Figure:
    """가로 밴드(p10~p90) + 감당 가능 상한 수직선. 밴드는 참고 정보다."""
    is_prior = getattr(band, "sample_size", None) == 0
    fig = go.Figure()

    lo, hi = band.p10, band.p90
    fig.add_shape(
        type="rect",
        x0=lo, x1=hi, y0=0.25, y1=0.75,
        fillcolor="rgba(66,133,244,0.16)",
        line=dict(color="#4285f4", width=2, dash="dash" if is_prior else "solid"),
        layer="below",
    )
    if max_bid < lo:
        fig.add_shape(
            type="rect",
            x0=max_bid, x1=lo, y0=0.25, y1=0.75,
            fillcolor="rgba(217,48,37,0.18)",
            line=dict(width=0),
            layer="below",
        )

    # 밴드 3분위 마커 (호버로 정확한 값을 읽게 한다)
    fig.add_trace(
        go.Scatter(
            x=[band.p10, band.p50, band.p90],
            y=[0.5, 0.5, 0.5],
            mode="markers+text",
            marker=dict(size=11, color="#1a73e8", symbol="line-ns-open", line=dict(width=3)),
            text=["p10", "p50", "p90"],
            textposition="top center",
            hovertemplate="%{text}: %{x:,.0f}원<extra>참고 정보</extra>",
            showlegend=False,
        )
    )
    fig.add_vline(
        x=max_bid,
        line=dict(color="#b42318", width=3),
        annotation_text=f"감당 가능 상한 {max_bid:,}원",
        annotation_position="top",
    )

    label = "참고용 구조 추정(관측 0건)" if is_prior else "예상 낙찰가 구간 (참고 정보)"
    fig.update_layout(
        title=label,
        font=FONT,
        height=260,
        margin=dict(l=20, r=20, t=70, b=30),
        xaxis=dict(title="금액(원)", tickformat=",", zeroline=False),
        yaxis=dict(visible=False, range=[0, 1]),
        showlegend=False,
    )
    return fig


def tco_chart(tco) -> go.Figure:
    """입찰가 -> 부대비용 누적 -> 실질 취득가 워터폴."""
    labels = ["가정 입찰가"]
    values = [tco.bid_price]
    measures = ["absolute"]

    bid_labels = {"낙찰가", "입찰가", "가정 입찰가", "매각대금"}
    for item in tco.breakdown:
        label = str(item.get("label", ""))
        amount = int(item.get("amount", 0) or 0)
        if label in bid_labels or amount == 0:
            continue
        labels.append(label)
        values.append(amount)
        measures.append("relative")

    labels.append("실질 취득가")
    values.append(tco.effective_acquisition_price)
    measures.append("total")

    fig = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=measures,
            x=labels,
            y=values,
            text=[f"{v:,}" for v in values],
            textposition="outside",
            connector=dict(line=dict(color="#9aa0a6")),
            increasing=dict(marker=dict(color="#d93025")),
            totals=dict(marker=dict(color="#1a73e8")),
        )
    )
    fig.update_layout(
        title="총비용 브레이크다운 — 낙찰가에서 실질 취득가까지",
        font=FONT,
        height=430,
        margin=dict(l=20, r=20, t=70, b=40),
        yaxis=dict(title="금액(원)", tickformat=","),
        showlegend=False,
    )
    return fig


# ── 결과 렌더 ─────────────────────────────────────────────────────
def render_signal(verdict) -> None:
    emoji, fg, bg, short = SIGNAL_STYLE[verdict.signal]
    st.markdown(
        f"""<div style="background:{bg};border-left:8px solid {fg};padding:18px 22px;border-radius:8px">
        <div style="font-size:1.4rem;font-weight:700;color:{fg}">{emoji} {verdict.signal.value} — {short}</div>
        </div>""",
        unsafe_allow_html=True,
    )

    if verdict.signal == Signal.RED:
        reason = next(
            (w[len(RED_REASON_PREFIX):] for w in verdict.warnings if w.startswith(RED_REASON_PREFIX)),
            "권리 또는 자금 조건상 이 물건은 입찰을 권하지 않습니다.",
        )
        st.markdown("#### 입찰 비권장 사유")
        st.error(reason)
    else:
        st.markdown("#### 감당 가능 상한")
        st.markdown(
            f"<div style='font-size:2.6rem;font-weight:800;color:#1a1a1a'>"
            f"{won(verdict.recommended_max_bid)}</div>",
            unsafe_allow_html=True,
        )
        st.info("이 금액은 감당 가능 상한이며 낙찰을 보장하지 않습니다.")


def render_risk_cards(rights) -> None:
    st.markdown("### 인수 리스크")
    c1, c2, c3 = st.columns(3)
    c1.metric("리스크 등급", f"{rights.risk_grade.value}등급")
    c2.metric("인수 예상 총액", won(rights.total_assumed_cost))
    c3.metric("명도 난이도", rights.eviction_difficulty)

    if not rights.assumed_rights and not rights.tenants:
        st.success("제출된 문서 범위에서 인수 예상 권리와 임차인이 확인되지 않았습니다.")
        return

    for r in rights.assumed_rights:
        fg, bg, txt = CONFIDENCE_STYLE[r.confidence]
        with st.container(border=True):
            st.markdown(
                f"**{r.right_type.value}** · {won(r.estimated_cost)} "
                f"<span style='background:{bg};color:{fg};padding:2px 8px;"
                f"border-radius:10px;font-size:0.8rem'>{txt}</span>",
                unsafe_allow_html=True,
            )
            st.write(r.description)
            st.caption(f"근거: {r.evidence}")

    for t in rights.tenants:
        fg, bg, txt = CONFIDENCE_STYLE[t.confidence]
        with st.container(border=True):
            st.markdown(
                f"**임차인 {t.name_masked}** · 인수 예상 보증금 {won(t.expected_assumed_deposit)} "
                f"<span style='background:{bg};color:{fg};padding:2px 8px;"
                f"border-radius:10px;font-size:0.8rem'>{txt}</span>",
                unsafe_allow_html=True,
            )
            op = "판단 불가" if t.opposing_power is None else ("있음" if t.opposing_power else "없음")
            dd = "판단 불가" if t.dividend_demanded is None else ("함" if t.dividend_demanded else "안 함")
            st.caption(
                f"전입일 {t.move_in_date or '-'} · 확정일자 {t.fixed_date or '-'} · "
                f"보증금 {won(t.deposit)} · 대항력 {op} · 배당요구 {dd}"
            )


def render_products(policy) -> None:
    st.markdown("### 추천 금융 시나리오")
    products = list(policy.products) if policy is not None else []
    if not products:
        st.warning("매칭된 금융 상품이 없습니다. 금융기관 창구에서 경락잔금대출 가능 여부를 확인하세요.")
        return
    for p in sorted(products, key=lambda x: (not x.applicable, x.name)):
        with st.container(border=True):
            badges = []
            if p.applicable:
                badges.append("✅ 신청 가능")
            else:
                badges.append("⛔ 요건 미충족")
            if p.rate_note.startswith("[확인 필요]"):
                badges.append("⚠️ 확인 필요")
            st.markdown(f"**{p.name}** ({p.category}) — " + " · ".join(badges))
            st.caption(
                f"한도 {won(p.max_amount)} · 금리 {p.rate_note or '정보 없음'} · "
                f"경락잔금 용도 "
                + ("판단 불가" if p.applicable_to_auction is None else ("가능" if p.applicable_to_auction else "불가"))
            )
            if p.requirements_met:
                st.caption("충족: " + ", ".join(p.requirements_met))
            if p.requirements_unmet:
                st.caption("미충족: " + ", ".join(p.requirements_unmet))
            if p.source_url:
                st.caption(f"출처: {p.source_url} (확인일 {p.checked_date or '-'})")
    if policy is not None and policy.summary_note:
        st.caption(policy.summary_note)


# ── 메인 ─────────────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(page_title="SafeBid — 경매 안전입찰 에이전트", page_icon="🏠", layout="wide")
    st.title("🏠 SafeBid — 경매 안전입찰 에이전트")
    st.caption("숨은 인수 리스크를 찾아내고, 내가 감당할 수 있는 입찰 상한을 계산합니다.")

    cfg = load_config()
    if not cfg.verified:
        st.warning(f"규제 수치는 예시값이며 최신 확인 전입니다. (설정 버전 {cfg.version})")

    user = sidebar_user()

    case_names = list_case_dirs()
    if not case_names:
        st.error(f"{SAMPLES_DIR} 아래에서 case.json 을 가진 케이스를 찾지 못했습니다.")
        return

    left, right = st.columns([3, 1])
    case_name = left.selectbox("분석할 사건", options=case_names, index=0)
    case = PropertyCase.model_validate(load_case_dict(case_name))
    run_clicked = right.button("분석 실행", type="primary", width="stretch")

    with st.container(border=True):
        a, b, c, d = st.columns(4)
        a.metric("감정가", won(case.appraisal_price))
        b.metric("최저매각가격", won(case.min_bid_price))
        c.metric("유찰", f"{case.failed_count}회")
        d.metric("매각기일", str(case.sale_date) if case.sale_date else "-")
        st.caption(f"{case.court} · {case.case_number} · {case.address}")

    if not run_clicked:
        st.info("왼쪽에서 자금 상황을 입력하고 [분석 실행] 을 눌러 주세요. 데모는 사전 수집 케이스만 사용합니다.")
        return

    # 실행 결과는 캐시하지 않는다(P7 — 전역 상태 금지).
    with st.spinner("권리분석 → 밴드 추정 → 자금 상한 → 총비용 → 상품 매칭 실행 중..."):
        try:
            verdict, detail = run_detailed(case, user, cfg, as_of=case.sale_date)
        except Exception as exc:  # 오케스트레이터가 중단시킨 경우(A1/A3 실패)
            st.error(f"분석을 완료하지 못했습니다: {exc}")
            st.caption("매각물건명세서 없이는 권리분석을 수행할 수 없습니다.")
            return

    st.divider()
    render_signal(verdict)

    k = verdict.key_numbers
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("인수 예상 금액", won(k.get("assumed_cost")))
    m2.metric("실질 취득가(상한 기준)", won(k.get("effective_price_at_max")))
    m3.metric("월 원리금", won(k.get("monthly_payment")))
    m4.metric("참고 구간 중앙값", won(k.get("p50")))

    st.divider()

    band = detail["band"]
    financing = detail["financing"]
    tab1, tab2, tab3 = st.tabs(["요약 차트", "리포트 전문", "유의사항"])

    with tab1:
        if band is not None:
            st.plotly_chart(band_chart(band, financing.max_bid_price), width="stretch")
            st.caption("위 구간은 참고 정보이며, 응찰 여부의 근거가 아닙니다.")
        else:
            st.info("예상 낙찰가 구간을 추정하지 못했습니다. 이 구간은 참고 정보일 뿐입니다.")
        st.plotly_chart(tco_chart(detail["tco"]), width="stretch")
        render_risk_cards(detail["rights"])
        render_products(detail["policy"])

    with tab2:
        st.markdown(verdict.report_markdown)

    with tab3:
        for w in verdict.warnings:
            st.write(f"- {w}")
        for n in cfg.notes:
            st.caption(f"설정 해석: {n}")

    st.divider()
    st.caption(verdict.disclaimer)


if __name__ == "__main__":
    main()
