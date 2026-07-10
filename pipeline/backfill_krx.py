# -*- coding: utf-8 -*-
"""
🎣 스톡캐쳐 백필 — 종목별 차트를 '상장 시점까지' 거슬러 확장 (VOL20)

동작 방식
  + data/charts/{code}.json      : 매일 수집기가 쓰는 롤링 3년치 (건드리지 않음)
  + data/charts_hist/{code}.json : 이 스크립트가 쓰는 '상장~기준일' 전체 과거분.
                                   한 종목이 완성되면 다시 건드리지 않아 저장소 부담이 적음.
  + data/backfill_state.json     : 완료 종목 목록. 프론트가 이 정보(및 hist 파일의
                                   complete 플래그)로 '전체 확보 완료' 배지를 표시.

  실행할 때마다 미완료 종목을 이어서 처리하고, 시간 예산이 다 되면 상태를 저장하고
  종료해요. 다음 실행이 그 지점부터 계속 진행해 결국 전 종목이 완료돼요.

사용
  export KRX_ID='아이디'; export KRX_PW='비밀번호'
  python3 backfill_krx.py                # 기본 300분 예산
  python3 backfill_krx.py --minutes 60   # 60분만
  python3 backfill_krx.py --limit 50     # 이번 실행에서 최대 50종목만
  python3 backfill_krx.py --codes 005930,000660  # 특정 종목 우선 처리
"""
import argparse
import datetime
import json
import os
import sys
import time

from pykrx import stock

SLEEP = 0.35
CHUNK_YEARS = 3
EMPTY_STOP = 2            # 빈 청크 연속 N회 = 상장 이전 도달로 판정
COMMON_ONLY = True
EXCLUDE_KEYWORDS = ("스팩", "리츠")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
HIST_DIR = os.path.join(DATA_DIR, "charts_hist")
STATE_PATH = os.path.join(DATA_DIR, "backfill_state.json")
SCREENER_PATH = os.path.join(DATA_DIR, "screener.json")


def log(msg):
    print(msg, flush=True)


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as fp:
                return json.load(fp)
        except Exception:
            pass
    return {"done": {}, "updated": None}


def save_state(st):
    os.makedirs(DATA_DIR, exist_ok=True)
    st["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(STATE_PATH, "w", encoding="utf-8") as fp:
        json.dump(st, fp, ensure_ascii=False, separators=(",", ":"))


def get_universe():
    """screener.json이 있으면 그 종목 순서(스코어 데이터가 있는 종목 우선), 없으면 전 종목"""
    if os.path.exists(SCREENER_PATH):
        try:
            with open(SCREENER_PATH, encoding="utf-8") as fp:
                j = json.load(fp)
            codes = [s["code"] for s in j.get("stocks", []) if s.get("code")]
            if codes:
                return codes
        except Exception:
            pass
    today = datetime.datetime.now().strftime("%Y%m%d")
    codes = []
    for mkt in ("KOSPI", "KOSDAQ"):
        for t in stock.get_market_ticker_list(today, market=mkt):
            if COMMON_ONLY and not t.endswith("0"):
                continue
            name = stock.get_market_ticker_name(t)
            if any(k in str(name) for k in EXCLUDE_KEYWORDS):
                continue
            codes.append(t)
        time.sleep(SLEEP)
    return codes


def fetch_range(code, fromdate, todate):
    for attempt in range(3):
        try:
            return stock.get_market_ohlcv_by_date(fromdate, todate, code)
        except Exception as e:
            log(f"    재시도 {attempt+1}/3 ({code} {fromdate}~{todate}): {e}")
            time.sleep(2 + attempt * 3)
    return None


def fetch_full_history(code, anchor):
    """anchor(yyyymmdd)부터 3년 청크로 거슬러 상장 시점까지 전체 조회.
    반환: {yyyymmdd: (o,h,l,c,v)} 또는 서버 오류 지속 시 None"""
    rows = {}
    end = datetime.datetime.strptime(anchor, "%Y%m%d")
    empty_streak = 0
    while empty_streak < EMPTY_STOP:
        start = end - datetime.timedelta(days=365 * CHUNK_YEARS)
        df = fetch_range(code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        if df is None:
            return None  # 서버 문제 — 이 종목은 이번 실행에서 건너뜀
        got = 0
        if not df.empty:
            for idx, row in df.iterrows():
                d = idx.strftime("%Y%m%d") if hasattr(idx, "strftime") else str(idx)
                try:
                    rows[d] = (int(row["시가"]), int(row["고가"]), int(row["저가"]),
                               int(row["종가"]), int(row["거래량"]))
                    got += 1
                except Exception:
                    continue
        empty_streak = empty_streak + 1 if got == 0 else 0
        end = start - datetime.timedelta(days=1)
        time.sleep(SLEEP)
        if end.year < 1980:
            break
    return rows


def write_hist(code, rows, anchor):
    os.makedirs(HIST_DIR, exist_ok=True)
    ds = sorted(rows)
    out = {
        "d": ds,
        "o": [rows[d][0] for d in ds],
        "h": [rows[d][1] for d in ds],
        "l": [rows[d][2] for d in ds],
        "c": [rows[d][3] for d in ds],
        "v": [rows[d][4] for d in ds],
        "anchor": anchor,
        "complete": True,
    }
    with open(os.path.join(HIST_DIR, f"{code}.json"), "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, separators=(",", ":"))
    return len(ds), (ds[0] if ds else None), (ds[-1] if ds else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=300, help="이번 실행 시간 예산(분)")
    ap.add_argument("--limit", type=int, default=0, help="이번 실행 최대 처리 종목 수 (0=제한 없음)")
    ap.add_argument("--codes", type=str, default="", help="우선 처리할 종목코드 (쉼표 구분)")
    args = ap.parse_args()

    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        log("⚠️ KRX_ID/KRX_PW 환경변수가 없어요. 'Expecting value' 오류가 나면 이 누락이 원인이에요.")

    deadline = time.time() + args.minutes * 60
    state = load_state()
    done = state.setdefault("done", {})

    universe = get_universe()
    priority = [c.strip() for c in args.codes.split(",") if c.strip()]
    ordered = priority + [c for c in universe if c not in priority]
    todo = [c for c in ordered if c not in done]
    log(f"📡 백필 시작 — 전체 {len(universe)}종목 중 완료 {len(done)} · 남은 {len(todo)} · 예산 {args.minutes}분")

    anchor = datetime.datetime.now().strftime("%Y%m%d")
    processed = 0
    for code in todo:
        if time.time() > deadline:
            log("⏱️ 시간 예산 소진 — 다음 실행에서 이어서 진행돼요.")
            break
        if args.limit and processed >= args.limit:
            log("📦 종목 수 제한 도달 — 다음 실행에서 이어서 진행돼요.")
            break
        t0 = time.time()
        rows = fetch_full_history(code, anchor)
        if rows is None:
            log(f"  ⚠️ {code}: 서버 응답 문제 — 이번 실행에서는 건너뛰어요.")
            continue
        if not rows:
            log(f"  ⚠️ {code}: 데이터 없음 (상장폐지/거래정지 가능) — 완료 처리하지 않아요.")
            continue
        bars, d_from, d_to = write_hist(code, rows, anchor)
        done[code] = {"bars": bars, "from": d_from, "to": d_to, "anchor": anchor}
        processed += 1
        save_state(state)  # 종목 단위 저장 — 중단돼도 진행분 보존
        log(f"  ✅ {code}: {bars:,}봉 ({d_from}~{d_to}) · {time.time()-t0:.0f}초 "
            f"[누적 완료 {len(done)}/{len(universe)}]")

    save_state(state)
    log(f"🏁 이번 실행 완료 — 처리 {processed}종목, 누적 완료 {len(done)}/{len(universe)}")


if __name__ == "__main__":
    main()
