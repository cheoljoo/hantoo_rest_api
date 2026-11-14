"""일일 리포트의 분석 단계: 계좌 통계(코드) + 매크로·뉴스·조언(LLM CLI).

통계는 LLM 없이 코드로 계산하고(항상 저장됨), 서술형 분석(매크로 단계 판단, 뉴스 요약,
조언)은 `claude -p`로 만들고 실패하면 `agy -p`로 대체한다. 둘 다 실패해도 통계만으로
리포트는 저장된다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

MACRO_PHASES = {
    1: "회복", 2: "확장 초기", 3: "확장 후기", 4: "정점·둔화", 5: "수축", 6: "바닥",
}

_ASSET_KINDS = ("주식", "채권", "펀드", "금", "현금", "기타")


def compute_stats(accounts: list[dict], prev: dict | None) -> dict:
    """계좌 통계: 총자산, 증권사/자산군/통화 비중, 집중도, 손익, 전일 대비."""
    ok = [a for a in accounts if not a["error"]]
    total = sum(a["total"] for a in ok)
    by_broker: dict[str, float] = {}
    by_kind = dict.fromkeys(_ASSET_KINDS, 0.0)
    usd = 0.0
    holdings: dict[str, float] = {}
    for a in ok:
        by_broker[a["broker"]] = by_broker.get(a["broker"], 0) + a["total"]
        for p in a["positions"]:
            kind = p["kind"] if p["kind"] in by_kind else "기타"
            by_kind[kind] += p["eval"]
            if p["currency"] == "USD":
                usd += p["eval"]
            if p["kind"] in ("주식", "금", "펀드"):
                holdings[p["name"]] = holdings.get(p["name"], 0) + p["eval"]
    # 계좌 합계에는 채권 매수금액이 들어 있으나 positions의 채권 항목과 같은 값이라 일치한다.
    inv = sum(v for k, v in by_kind.items() if k != "현금")
    top = sorted(holdings.items(), key=lambda x: -x[1])[:10]
    hs = sum(holdings.values()) or 1
    hhi = sum((v / hs) ** 2 for v in holdings.values())
    purchase = sum(a["purchase"] for a in ok if a["broker"] != "미래에셋")
    pl = sum(a["pl"] for a in ok)
    stats = {
        "total": total,
        "accounts": {a["label"]: a["total"] for a in ok},
        "by_broker": by_broker,
        "by_kind": by_kind,
        "cash_ratio": by_kind["현금"] / total * 100 if total else 0,
        "usd_ratio": usd / total * 100 if total else 0,
        "top_holdings": [{"name": n, "eval": v, "weight": v / total * 100 if total else 0} for n, v in top],
        "hhi": hhi,
        "holding_count": len(holdings),
        "pl": pl,
        "purchase_ex_manual": purchase,
        "pl_rate": pl / purchase * 100 if purchase else 0,
        "errors": {a["label"]: a["error"] for a in accounts if a["error"]},
        "invested": inv,
    }
    if prev:
        stats["prev_date"] = prev["date"]
        stats["prev_total"] = prev["total"]
        stats["change"] = total - prev["total"]
        stats["change_pct"] = (total - prev["total"]) / prev["total"] * 100 if prev["total"] else 0
        stats["account_change"] = {
            k: v - prev["accounts"][k] for k, v in stats["accounts"].items() if k in prev["accounts"]
        }
    return stats


def _fmt(v, nd=2):
    return "-" if v is None else f"{v:,.{nd}f}"


def build_prompt(date: str, accounts: list[dict], indicators: list, stats: dict) -> str:
    ind_lines = "\n".join(
        f"- [{i.macro}] {i.name}: {_fmt(i.value)} {i.unit} (전 관측 대비 {_fmt(i.change_pct)}%, 1주 {i.trend or '-'}, "
        f"기준일 {i.as_of}){' ' + i.flag if i.flag else ''}"
        for i in indicators if not i.error
    )
    acct_lines = []
    for a in accounts:
        if a["error"]:
            acct_lines.append(f"## {a['label']} — 조회 실패: {a['error']}")
            continue
        acct_lines.append(f"## {a['label']} ({a['broker']}) 총 {a['total']:,.0f}원, 손익 {a['pl']:,.0f}, 현금 {a['cash']:,.0f}")
        for p in a["positions"]:
            acct_lines.append(
                f"- {p['kind']} {p['name']} {p['currency']} 수량 {p['quantity']:g} 평가 {p['eval']:,.0f} "
                f"손익 {p['pl']:,.0f} ({p['pl_rate']:.1f}%)"
            )
    phases = ", ".join(f"{k}.{v}" for k, v in MACRO_PHASES.items())
    return f"""당신은 개인 투자자의 일일 포트폴리오 리포트를 쓰는 애널리스트입니다. 기준일은 {date}입니다.
아래 데이터(내 계좌 + 오늘 수집한 거시·시장 지표)와 웹 검색으로 찾은 오늘의 금융/거시 뉴스를 바탕으로 한국어 마크다운 리포트를 쓰세요.

[출력 규칙]
- 첫 줄은 반드시 `MACRO_PHASE: <1~6>|<단계 이름>|<근거>` 형식 (근거는 줄바꿈 없이 3~5문장, 성장·물가·금리·신용·시장 지표의 수치와 기준 범위를 인용하고 왜 앞뒤 단계가 아닌지도 한 문장으로 설명) (단계: {phases}). 이 줄 외에 머리말·코드블록 없이 바로 본문을 쓴다.
- 본문은 다음 `##` 섹션 순서를 지킨다:
  1. 매크로 위치 — 경기 사이클 6단계 중 지금 위치와 근거(성장·물가·금리·신용·환율·원자재 축별로 지표 수치를 인용)
  2. 오늘의 주요 금융 뉴스 — 5~8건, 출처 매체와 날짜, 내 자산과의 관련성 한 줄
  3. 매크로 뉴스 — 중앙은행·물가·고용·지정학·유가 등
  4. 내 계좌 통계 해석 — 자산군/증권사 비중, 현금 비중, 달러 노출, 집중도(HHI·상위 종목), 손익, 전일 대비
  5. 현재 매크로에서 내 투자가 잘 되고 있는가 — 단계별로 유리/불리한 자산군과 내 배분을 비교, 좋은 점과 위험 요인
  6. 조언 — 구체적·실행 가능한 제안 3~6개 (리밸런싱, 현금 비중, 헤지, 주시할 지표/일정 포함). 매수·매도 확정 지시가 아니라 판단 근거를 곁들인 제안으로 쓰고, 불확실한 부분은 불확실하다고 밝힌다.
- 모르는 사실을 지어내지 않는다. 뉴스는 실제로 검색해서 확인한 것만 쓴다. 검색이 불가능하면 그 사실을 한 줄로 밝히고 지표 기반 분석만 한다.
- 미래에셋 계좌는 수동 입력 장부금액이라 평가손익이 없다.
- 'KODEX CD금리액티브' 등 CD금리 상품은 사용자 기준으로 현금(파킹)으로 취급하며, 통계의 현금 항목에 이미 포함돼 있다. 주식으로 해석하지 않는다.

[내 계좌 통계(JSON)]
{json.dumps(stats, ensure_ascii=False, indent=1)}

[계좌별 보유]
{chr(10).join(acct_lines)}

[오늘 수집한 지표]
{ind_lines}
"""


_PHASE_RE = re.compile(r"MACRO_PHASE:\s*([1-6])\s*\|\s*([^|\n]*)\|\s*([^\n]*)")


def _run_llm(prompt: str, timeout: int) -> tuple[str, str]:
    """(출력, 엔진 이름). claude 실패(토큰 부족 등) 시 agy로 대체."""
    env = dict(os.environ)
    env["PATH"] = "/home/cheoljoo/.claude-wrapper/bin:/home/cheoljoo/.local/bin:" + env.get("PATH", "")
    errors = []
    if shutil.which("claude", path=env["PATH"]):
        try:
            r = subprocess.run(
                ["claude", "-p", "--model", "sonnet", "--no-session-persistence",
                 "--allowedTools", "WebSearch", "WebFetch"],
                input=prompt, capture_output=True, text=True, timeout=timeout, env=env,
            )
            if r.returncode == 0 and _PHASE_RE.search(r.stdout):
                return r.stdout.strip(), "claude-sonnet"
            errors.append(f"claude rc={r.returncode}: {(r.stdout + r.stderr)[-300:]}")
        except Exception as e:
            errors.append(f"claude 실패: {e}")
    if shutil.which("agy", path=env["PATH"]):
        try:
            r = subprocess.run(["agy", "-p", prompt, "--mode", "accept-edits"], capture_output=True, text=True,
                               timeout=timeout, env=env)
            if r.returncode == 0 and _PHASE_RE.search(r.stdout):
                return r.stdout.strip(), "agy"
            errors.append(f"agy rc={r.returncode}: {(r.stdout + r.stderr)[-300:]}")
        except Exception as e:
            errors.append(f"agy 실패: {e}")
    raise RuntimeError(" / ".join(errors) or "LLM CLI(claude, agy)를 찾을 수 없음")


def analyze(date: str, accounts: list[dict], indicators: list, stats: dict, timeout: int = 1500):
    """(분석 마크다운, 엔진, 단계 번호|None, 단계 이름, 근거)."""
    try:
        text, engine = _run_llm(build_prompt(date, accounts, indicators, stats), timeout)
    except Exception as e:
        return (f"> ⚠️ AI 분석 생성 실패 — 통계만 저장했습니다.\n>\n> {e}", "none", None, "", "")
    m = _PHASE_RE.search(text)
    body = _PHASE_RE.sub("", text, count=1).strip()
    return body, engine, int(m.group(1)), m.group(2).strip(), m.group(3).strip()
