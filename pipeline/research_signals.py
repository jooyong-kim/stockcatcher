# -*- coding: utf-8 -*-
"""
🔬 스톡캐쳐 지표 연구 — '어떤 신호가 이후 실제로 올랐나'를 통계로 검증 (VOL27)

무엇을 하나요
  + data/charts/*.json (일일 수집기가 만든 종목별 3년 일봉)을 읽어
    후보 신호 10여 종의 발생일을 전 종목·전 기간에서 찾고,
    발생 후 5/20/60거래일 수익률을 전수 계산해요.
  + 신호별: 표본수 · 승률 · 평균/중앙값 수익률 · 시장 평균 대비 초과(엣지)
  + 2개 신호 조합(동시 충족) 상위도 탐색해요.
  + 결과는 data/research_report.json 으로 저장되고 앱의
    '🏆 스코어 안내' 탭에서 표로 볼 수 있어요.

정직 원칙
  + 과거 통계일 뿐 미래 보장이 아니에요. 표본수(n)가 적은 결과는 제외해요.
  + 마지막 60거래일은 미래 수익률을 알 수 없어 표본에서 빠져요(선견 편향 방지).

사용:  python3 research_signals.py [--min-n 300] [--horizon-check 20]
"""
import argparse
import datetime
import glob
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHART_DIR = os.path.join(BASE_DIR, "data", "charts")
FLOW_DIR = os.path.join(BASE_DIR, "data", "flows_detail")
OUT_PATH = os.path.join(BASE_DIR, "data", "research_report.json")

HORIZONS = [5, 20, 60]


def log(m):
    print(m, flush=True)


def sma(arr, p, i):
    if i + 1 < p:
        return None
    return sum(arr[i - p + 1:i + 1]) / p


def rsi14(c):
    n = len(c)
    out = [None] * n
    if n < 15:
        return out
    g = l2 = 0.0
    for k in range(1, 15):
        d = c[k] - c[k - 1]
        g += max(d, 0)
        l2 += max(-d, 0)
    ag, al = g / 14, l2 / 14
    out[14] = 100 - 100 / (1 + (ag / al if al > 1e-9 else 999))
    for k in range(15, n):
        d = c[k] - c[k - 1]
        ag = (ag * 13 + max(d, 0)) / 14
        al = (al * 13 + max(-d, 0)) / 14
        out[k] = 100 - 100 / (1 + (ag / al if al > 1e-9 else 999))
    return out


def ema_arr(c, p):
    out = [None] * len(c)
    k = 2 / (p + 1)
    e = c[0]
    for i, v in enumerate(c):
        e = v * k + e * (1 - k)
        out[i] = e
    return out


def detect_box(h, l2, c, end):
    """프론트 detectBox와 동일 철학의 축약판 — end 시점에서 끝나는 박스"""
    n = end + 1
    if n < 35:
        return None
    for w in range(min(n, 90), 19, -5):
        s = n - w
        hs, ls, cs = h[s:n], l2[s:n], c[s:n]
        sh = sorted(hs)
        sl = sorted(ls)
        hi = sh[max(0, int(len(sh) * 0.97) - 1)]
        lo = sl[min(len(sl) - 1, int(len(sl) * 0.03))]
        mid = (hi + lo) / 2
        if mid <= 0:
            continue
        width = (hi - lo) / mid
        if width > 0.25 or width <= 0.01:
            continue
        inc = sum(1 for x in cs if lo * 0.99 <= x <= hi * 1.01)
        if inc / w < 0.92:
            continue
        sx = sy = sxy = sxx = 0.0
        for k, v in enumerate(cs):
            sx += k
            sy += v
            sxy += k * v
            sxx += k * k
        slope = (w * sxy - sx * sy) / ((w * sxx - sx * sx) or 1e-9)
        if abs(slope * w / ((sy / w) or 1e-9)) > 0.07:
            continue
        return {"hi": hi, "lo": lo}
    return None


def build_signals(o, h, l2, c, v, flows):
    """각 신호의 발생 인덱스 집합 반환 {signal_id: set(idx)}"""
    n = len(c)
    ma5 = [sma(c, 5, i) for i in range(n)]
    ma20 = [sma(c, 20, i) for i in range(n)]
    ma60 = [sma(c, 60, i) for i in range(n)]
    rsi = rsi14(c)
    e12, e26 = ema_arr(c, 12), ema_arr(c, 26)
    macd = [e12[i] - e26[i] for i in range(n)]
    sig = ema_arr(macd, 9)
    v20 = [sma(v, 20, i) for i in range(n)]

    S = {k: set() for k in ["gcross", "above20", "newHigh20", "volSurge",
                             "rsiRebound", "macdGC", "bbBreakUp", "boxBreakUp",
                             "obvAccum", "trendAlign", "frgStreak5"]}
    # 볼린저 상단
    bbU = [None] * n
    for i in range(19, n):
        m = ma20[i]
        var = sum((c[q] - m) ** 2 for q in range(i - 19, i + 1)) / 20
        bbU[i] = m + 2 * (var ** 0.5)
    obv = [0] * n
    for i in range(1, n):
        obv[i] = obv[i - 1] + (v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0)

    box_cache = {}
    for i in range(60, n):
        if ma5[i] and ma20[i] and ma5[i - 1] and ma20[i - 1]:
            if ma5[i - 1] <= ma20[i - 1] and ma5[i] > ma20[i]:
                S["gcross"].add(i)
            if c[i - 1] <= ma20[i - 1] and c[i] > ma20[i]:
                S["above20"].add(i)
            if ma5[i] > ma20[i] > (ma60[i] or 0) and c[i] > ma5[i]:
                S["trendAlign"].add(i)
        if i >= 21 and c[i] > max(h[i - 20:i]):
            S["newHigh20"].add(i)
        if v20[i - 1] and v[i] >= v20[i - 1] * 2.5 and c[i] > o[i]:
            S["volSurge"].add(i)
        if rsi[i] is not None and rsi[i - 1] is not None and rsi[i - 1] < 30 <= rsi[i]:
            S["rsiRebound"].add(i)
        if macd[i - 1] <= sig[i - 1] and macd[i] > sig[i] and macd[i] < 0:
            S["macdGC"].add(i)
        if bbU[i] and bbU[i - 1] and c[i - 1] <= bbU[i - 1] and c[i] > bbU[i]:
            S["bbBreakUp"].add(i)
        if i >= 40 and obv[i] > obv[i - 20] and abs(c[i] / c[i - 20] - 1) < 0.03:
            S["obvAccum"].add(i)
        if i % 5 == 0:  # 박스 계산 비용 절감 (5일 간격 캐시)
            box_cache[i] = detect_box(h, l2, c, i - 1)
        bx = box_cache.get(i - (i % 5))
        if bx and c[i - 1] <= bx["hi"] and c[i] > bx["hi"] * 1.01:
            S["boxBreakUp"].add(i)
    if flows:
        frg = flows.get("frg") or []
        m2 = min(n, len(frg))
        for i in range(5, m2):
            if all(frg[i - k] > 0 for k in range(5)):
                S["frgStreak5"].add(i)
    return S


SIGNAL_LABELS = {
    "gcross": "골든크로스(5>20)", "above20": "20일선 재안착", "newHigh20": "20일 신고가",
    "volSurge": "거래량 급증(2.5배↑, 양봉)", "rsiRebound": "RSI 30 상향 반등",
    "macdGC": "MACD 골든(0선 아래)", "bbBreakUp": "볼린저 상단 돌파",
    "boxBreakUp": "박스권 상단 돌파", "obvAccum": "OBV 매집(가격 횡보+OBV 상승)",
    "trendAlign": "정배열+5일선 위", "frgStreak5": "외국인 5일 연속 순매수",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=300, help="신호 최소 표본수")
    ap.add_argument("--max-stocks", type=int, default=0, help="테스트용 종목 수 제한")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(CHART_DIR, "*.json")))
    if args.max_stocks:
        files = files[:args.max_stocks]
    if not files:
        log("❌ data/charts 에 차트가 없어요 — 일일 수집을 먼저 실행해 주세요.")
        raise SystemExit(1)
    log(f"🔬 지표 연구 시작 — {len(files):,}종목")

    maxH = max(HORIZONS)
    sums = {k: {h2: [] for h2 in HORIZONS} for k in SIGNAL_LABELS}
    base = {h2: [0.0, 0] for h2 in HORIZONS}          # 시장 평균(모든 날)
    pair_keys = list(SIGNAL_LABELS.keys())
    pair_sums = {}

    done = 0
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                j = json.load(f)
            o, h, l2, c, v = j["o"], j["h"], j["l"], j["c"], j["v"]
        except Exception:
            continue
        n = len(c)
        if n < 120:
            continue
        code = os.path.basename(fp)[:-5]
        flows = None
        flp = os.path.join(FLOW_DIR, f"{code}.json")
        if os.path.exists(flp):
            try:
                with open(flp, encoding="utf-8") as f:
                    fj = json.load(f)
                # flows_detail 은 최근 120일 — 차트 뒤쪽에 정렬해 매핑
                off = n - len(fj.get("d", []))
                if off >= 0:
                    flows = {"frg": [0] * off + fj["frg"]}
            except Exception:
                pass
        S = build_signals(o, h, l2, c, v, flows)
        for i in range(60, n - maxH):
            for h2 in HORIZONS:
                r = c[i + h2] / c[i] - 1
                base[h2][0] += r
                base[h2][1] += 1
            active = [k for k in pair_keys if i in S[k]]
            for k in active:
                for h2 in HORIZONS:
                    sums[k][h2].append(c[i + h2] / c[i] - 1)
            for a_i in range(len(active)):
                for b_i in range(a_i + 1, len(active)):
                    pk = active[a_i] + "+" + active[b_i]
                    d = pair_sums.setdefault(pk, {h2: [] for h2 in HORIZONS})
                    for h2 in HORIZONS:
                        d[h2].append(c[i + h2] / c[i] - 1)
        done += 1
        if done % 200 == 0:
            log(f"  {done}/{len(files)} 종목 처리")

    def stats(lst):
        if not lst:
            return None
        s = sorted(lst)
        n2 = len(s)
        return {"n": n2, "win": round(sum(1 for x in s if x > 0) / n2 * 100, 1),
                "avg": round(sum(s) / n2 * 100, 2), "med": round(s[n2 // 2] * 100, 2)}

    base_avg = {h2: (base[h2][0] / base[h2][1] * 100 if base[h2][1] else 0) for h2 in HORIZONS}
    sig_out = []
    for k, lab in SIGNAL_LABELS.items():
        row = {"id": k, "label": lab, "h": {}}
        ok = False
        for h2 in HORIZONS:
            st = stats(sums[k][h2])
            if st and st["n"] >= args.min_n:
                st["edge"] = round(st["avg"] - base_avg[h2], 2)
                row["h"][str(h2)] = st
                ok = True
        if ok:
            sig_out.append(row)
    sig_out.sort(key=lambda r: -(r["h"].get("20", {}).get("edge", -999)))

    pair_out = []
    for pk, d in pair_sums.items():
        st20 = stats(d[20])
        if not st20 or st20["n"] < max(120, args.min_n // 3):
            continue
        a, b = pk.split("+")
        st20["edge"] = round(st20["avg"] - base_avg[20], 2)
        st60 = stats(d[60]) or {}
        pair_out.append({"label": SIGNAL_LABELS[a] + " + " + SIGNAL_LABELS[b],
                         "h20": st20, "avg60": st60.get("avg"), "win60": st60.get("win")})
    pair_out.sort(key=lambda r: -r["h20"]["edge"])
    pair_out = pair_out[:8]

    out = {"generatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "stocks": done, "horizons": HORIZONS, "minN": args.min_n,
           "baseline": {str(h2): round(base_avg[h2], 2) for h2 in HORIZONS},
           "signals": sig_out, "pairs": pair_out,
           "note": "과거 수집 데이터 통계이며 미래 수익을 보장하지 않아요. edge=시장 평균 대비 초과 평균수익률(%p)."}
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"🏁 research_report.json 저장 — 신호 {len(sig_out)}종 / 조합 {len(pair_out)}종 / 표본기준 n≥{args.min_n}")
    for r in sig_out[:5]:
        h20 = r["h"].get("20")
        if h20:
            log(f"  TOP {r['label']}: 20일 승률 {h20['win']}% · 평균 {h20['avg']}% · 엣지 {h20['edge']}%p (n={h20['n']:,})")


if __name__ == "__main__":
    main()
