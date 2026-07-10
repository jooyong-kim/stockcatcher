# -*- coding: utf-8 -*-
"""
🎣 스톡캐쳐 수급 대시보드 수집 — flows.json 생성 (VOL21)

수집 내용
  + 투자자별(외국인 / 기관합계 / 연기금) 기간별(1일/1주/1개월/3개월) 순매수·순매도 상위 종목
  + 종목별 업종(KRX 업종분류: 철강금속, 전기전자, 서비스업 등)과 시가총액
  + 업종별 자금 흐름 집계(투자자·기간별 순매수 합계 상위/하위)

비중 산출
  + capPct  : 순매수 대금 ÷ 시가총액 × 100 (그 종목 몸집 대비 얼마나 샀는지)
  + share   : 해당 투자자의 기간 순매수(또는 순매도) 총액 중 이 종목 비중 %

주의
  + KRX는 '국민연금' 단독 수치를 공개하지 않아요. '연기금' 집계(국민연금이
    대부분을 차지)를 사용하고 프론트에 그렇게 표기해요.

사용: python3 flow_krx.py   (env: KRX_ID, KRX_PW)
출력: data/flows.json
"""
import datetime
import json
import os
import time

import pandas as pd
from pykrx import stock

SLEEP = 0.35
TOP_N = 30
SECTOR_TOP = 12
PERIODS = [("1D", 1, "1일"), ("1W", 5, "1주"), ("1M", 20, "1개월"), ("3M", 60, "3개월")]
INVESTORS = [("frg", "외국인", "외국인"),
             ("inst", "기관합계", "기관"),
             ("pension", "연기금", "연기금(국민연금 등)")]

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE_DIR, "data", "flows.json")
DETAIL_DAYS = 120
DETAIL_DIR = os.path.join(BASE_DIR, "data", "flows_detail")
META_PATH = os.path.join(DETAIL_DIR, "_meta.json")


def log(msg):
    print(msg, flush=True)


def trading_dates(back_days=210):
    """삼성전자 일봉으로 최근 거래일 목록 확보 (호출 1회)"""
    end = datetime.datetime.now()
    start = end - datetime.timedelta(days=back_days)
    df = stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "005930")
    ds = [idx.strftime("%Y%m%d") if hasattr(idx, "strftime") else str(idx) for idx in df.index]
    return sorted(ds)


def fetch_retry(fn, *args, **kwargs):
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            log(f"    재시도 {attempt+1}/3: {e}")
            time.sleep(2 + attempt * 3)
    return None


def collect_detail(dates_all):
    """🆕 [VOL22] 종목별 투자자 수급 추이 — 최근 DETAIL_DAYS 거래일 일자별 순매수.
    첫 실행만 전 기간 수집(투자자3×시장2×일수 호출), 이후엔 새 거래일만 증분 수집."""
    window = dates_all[-DETAIL_DAYS:]
    meta = {"dates": []}
    if os.path.exists(META_PATH):
        try:
            with open(META_PATH, encoding="utf-8") as fp:
                meta = json.load(fp)
        except Exception:
            pass
    have = [d for d in meta.get("dates", []) if d in window]
    missing = [d for d in window if d not in have]
    if not missing:
        log("  수급 추이: 새 거래일 없음 — 건너뜀")
        return
    est = len(missing) * len(INVESTORS) * 2
    log(f"  수급 추이 수집: 신규 {len(missing)}거래일 × 투자자3 × 시장2 = {est}콜 "
        f"(예상 약 {est*(SLEEP+0.8)/60:.0f}분)")

    day_data = {}
    for d in missing:
        per_inv = {}
        for ikey, iname, _ in INVESTORS:
            per = {}
            for mkt in ("KOSPI", "KOSDAQ"):
                df = fetch_retry(stock.get_market_net_purchases_of_equities_by_ticker,
                                 d, d, mkt, iname)
                time.sleep(SLEEP)
                if df is None or df.empty:
                    continue
                df = df.reset_index()
                code_col = "티커" if "티커" in df.columns else df.columns[0]
                for _, r in df.iterrows():
                    try:
                        per[str(r[code_col])] = per.get(str(r[code_col]), 0) + int(r["순매수거래대금"])
                    except Exception:
                        continue
            per_inv[ikey] = per
        day_data[d] = per_inv
        if len(day_data) % 10 == 0 or d == missing[-1]:
            log(f"    {len(day_data)}/{len(missing)}일 완료 (~{d})")

    # 유니버스: screener 종목 (스코어 대상과 동일)
    codes = []
    scr = os.path.join(BASE_DIR, "data", "screener.json")
    if os.path.exists(scr):
        try:
            with open(scr, encoding="utf-8") as fp:
                codes = [s["code"] for s in json.load(fp).get("stocks", [])]
        except Exception:
            pass
    if not codes:
        seen = set()
        for d in day_data.values():
            for per in d.values():
                seen.update(per.keys())
        codes = sorted(seen)

    os.makedirs(DETAIL_DIR, exist_ok=True)
    for code in codes:
        path = os.path.join(DETAIL_DIR, f"{code}.json")
        old = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fp:
                    j = json.load(fp)
                for i2, d in enumerate(j.get("d", [])):
                    old[d] = (j["frg"][i2], j["inst"][i2], j["pension"][i2])
            except Exception:
                old = {}
        for d in missing:
            dd = day_data.get(d, {})
            old[d] = (dd.get("frg", {}).get(code, 0),
                      dd.get("inst", {}).get(code, 0),
                      dd.get("pension", {}).get(code, 0))
        out = {"d": window,
               "frg": [old.get(d, (0, 0, 0))[0] for d in window],
               "inst": [old.get(d, (0, 0, 0))[1] for d in window],
               "pension": [old.get(d, (0, 0, 0))[2] for d in window]}
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(out, fp, ensure_ascii=False, separators=(",", ":"))
    with open(META_PATH, "w", encoding="utf-8") as fp:
        json.dump({"dates": window,
                   "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, fp)
    log(f"  수급 추이 저장: {len(codes)}종목 × {len(window)}거래일")


def main():
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        log("⚠️ KRX_ID/KRX_PW 환경변수가 없어요.")

    dates = trading_dates()
    if not dates or len(dates) < 61:
        log("❌ 거래일 목록 확보 실패"); raise SystemExit(1)
    base_date = dates[-1]
    log(f"📡 수급 수집 시작 — 기준일 {base_date}")

    # 업종분류 + 시가총액
    sectors, caps = {}, {}
    for mkt in ("KOSPI", "KOSDAQ"):
        df = fetch_retry(stock.get_market_sector_classifications, base_date, mkt)
        if df is not None and not df.empty:
            for code, row in df.iterrows():
                sectors[str(code)] = str(row.get("업종명", "") or "")
        time.sleep(SLEEP)
    dfc = fetch_retry(stock.get_market_cap_by_ticker, base_date, market="ALL")
    if dfc is not None and not dfc.empty:
        for code, row in dfc.iterrows():
            try:
                caps[str(code)] = int(row["시가총액"])
            except Exception:
                continue
    time.sleep(SLEEP)
    log(f"  업종 {len(sectors)}종목 · 시총 {len(caps)}종목 확보")

    out_periods = {}
    for pid, days, plabel in PERIODS:
        fromdate, todate = dates[-days], base_date
        inv_out = {}
        for ikey, iname, ilabel in INVESTORS:
            frames = []
            for mkt in ("KOSPI", "KOSDAQ"):
                df = fetch_retry(stock.get_market_net_purchases_of_equities_by_ticker,
                                 fromdate, todate, mkt, iname)
                time.sleep(SLEEP)
                if df is None or df.empty:
                    continue
                df = df.reset_index()
                frames.append(df)
            if not frames:
                inv_out[ikey] = {"label": ilabel, "buy": [], "sell": [], "sectors": []}
                continue
            alldf = pd.concat(frames, ignore_index=True)
            code_col = "티커" if "티커" in alldf.columns else alldf.columns[0]
            merged = {}
            for _, r in alldf.iterrows():
                try:
                    code = str(r[code_col])
                    net = int(r["순매수거래대금"])
                    vol = int(r.get("순매수거래량", 0))
                except Exception:
                    continue
                if code in merged:
                    merged[code]["net"] += net
                    merged[code]["vol"] += vol
                else:
                    merged[code] = {
                        "code": code,
                        "name": str(r.get("종목명", code)),
                        "sector": sectors.get(code, "기타"),
                        "net": net, "vol": vol,
                        "cap": caps.get(code),
                    }
            rows = list(merged.values())
            buys = sorted([x for x in rows if x["net"] > 0], key=lambda x: -x["net"])
            sells = sorted([x for x in rows if x["net"] < 0], key=lambda x: x["net"])
            buy_total = sum(x["net"] for x in buys) or 1
            sell_total = sum(-x["net"] for x in sells) or 1

            def pack(lst, total):
                out = []
                for x in lst[:TOP_N]:
                    cap_pct = round(abs(x["net"]) / x["cap"] * 100, 3) if x.get("cap") else None
                    out.append({"code": x["code"], "name": x["name"], "sector": x["sector"],
                                "net": x["net"], "capPct": cap_pct,
                                "share": round(abs(x["net"]) / total * 100, 2)})
                return out

            # 업종별 순매수 합계
            sec_sum = {}
            for x in rows:
                sec_sum[x["sector"]] = sec_sum.get(x["sector"], 0) + x["net"]
            sec_sorted = sorted(sec_sum.items(), key=lambda kv: -kv[1])
            sec_list = ([{"sector": s, "net": v} for s, v in sec_sorted[:SECTOR_TOP]] +
                        [{"sector": s, "net": v} for s, v in sec_sorted[-SECTOR_TOP:] if v < 0])
            # 중복 제거(양수 상위와 음수 하위가 겹칠 일은 없지만 안전하게)
            seen = set(); sec_final = []
            for s in sec_list:
                if s["sector"] in seen:
                    continue
                seen.add(s["sector"]); sec_final.append(s)

            inv_out[ikey] = {"label": ilabel, "buy": pack(buys, buy_total),
                             "sell": pack(sells, sell_total), "sectors": sec_final}
            log(f"  {plabel} · {ilabel}: 매수 {len(buys)}종 / 매도 {len(sells)}종")
        out_periods[pid] = {"label": plabel, "from": fromdate, "to": todate, "investors": inv_out}

    out = {"baseDate": base_date,
           "generatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "periods": out_periods,
           "note": "연기금 = 국민연금을 포함한 연기금 전체 집계 (KRX는 국민연금 단독 수치를 공개하지 않아요)"}
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, separators=(",", ":"))
    log(f"  flows.json 저장 완료 ({os.path.getsize(OUT_PATH)/1024:.0f}KB)")
    collect_detail(dates)
    log("🏁 수급 수집 전체 완료")


if __name__ == "__main__":
    main()
