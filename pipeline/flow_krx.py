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


def log(msg):
    print(msg, flush=True)


def trading_dates(back_days=140):
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
    log(f"🏁 flows.json 저장 완료 ({os.path.getsize(OUT_PATH)/1024:.0f}KB)")


if __name__ == "__main__":
    main()
