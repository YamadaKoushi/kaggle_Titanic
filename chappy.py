import os
import time
import requests
from datetime import datetime, timezone
from collections import defaultdict

# ========= 設定 =========
ALCHEMY_API_KEY = os.getenv("S1sjcnA4LhvSRUFzG2oE-", "S1sjcnA4LhvSRUFzG2oE-")  # ← 環境変数 or 直書き
CHAIN = os.getenv("CHAIN", "eth-mainnet")  # 例: "eth-mainnet", "polygon-mainnet"
NFT_BASE = f"https://{CHAIN}.g.alchemy.com/nft/v3/{ALCHEMY_API_KEY}"
RPC_URL  = f"https://{CHAIN}.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

# 取得スケールと閾値
RECENT_SALES_LIMIT = 400      # 市場全体から拾う最新売買件数
MAX_NFTS_TO_CHECK  = 60       # ユニークNFTの上限（取りすぎ防止）
DETAIL_LIMIT_PER_NFT = 200    # 1NFTの履歴取得上限
QUICK_FLIP_WINDOW_SEC = 48 * 3600  # 48時間以内なら短期保有

REQUEST_TIMEOUT = 30
REQUEST_DELAY   = 0.15        # 連続叩きすぎ防止
RETRY = 3
# ========================

block_ts_cache = {}  # {blockNumber:int_ts}

def log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def http_get(url, params=None, max_retry=RETRY):
    for i in range(1, max_retry+1):
        try:
            r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                log(f"HTTP {r.status_code} GET {url} params={params}")
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            log(f"[WARN] GET失敗({i}/{max_retry}): {e}. 再試行まで待機...")
            time.sleep(0.5 * i)
    log("[ERROR] GETリトライ上限到達")
    return None

def rpc_get_block_ts(block_number: int) -> int | None:
    """eth_getBlockByNumberでブロック時刻(UNIX秒)を取得（キャッシュあり）"""
    if block_number in block_ts_cache:
        return block_ts_cache[block_number]
    payload = {
        "jsonrpc": "2.0",
        "id": block_number,
        "method": "eth_getBlockByNumber",
        "params": [hex(block_number), False]
    }
    for i in range(1, RETRY+1):
        try:
            r = requests.post(RPC_URL, json=payload, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            ts_hex = data.get("result", {}).get("timestamp")
            if ts_hex:
                ts = int(ts_hex, 16)
                block_ts_cache[block_number] = ts
                return ts
            else:
                log(f"[WARN] ブロック時刻なし block={block_number} resp_keys={list(data.keys())}")
        except requests.RequestException as e:
            log(f"[WARN] RPC失敗({i}/{RETRY}): {e}")
            time.sleep(0.5 * i)
    return None

def get_recent_sales(limit=RECENT_SALES_LIMIT):
    """市場全体の最新売買を取得（v3仕様: limit/pageKey/order/…）"""
    log(f"最新売買を取得開始 limit={limit}")
    url = f"{NFT_BASE}/getNFTSales"
    params = {"limit": min(limit, 1000), "order": "desc"}
    all_sales, got = [], 0
    while True:
        res = http_get(url, params=params)
        if not res:
            break
        sales = res.get("nftSales", []) or []
        all_sales.extend(sales)
        got += len(sales)
        log(f"…ページ取得 {len(sales)}件 累計={got} pageKey={res.get('pageKey')}")
        if got >= limit or not res.get("pageKey"):
            break
        params["pageKey"] = res["pageKey"]
        time.sleep(REQUEST_DELAY)
    log(f"最新売買の最終件数: {len(all_sales)}")
    return all_sales

def get_sales_for_nft(contract: str, token_id: str, limit=DETAIL_LIMIT_PER_NFT):
    """指定NFTの売買履歴を昇順で取得（v3仕様）"""
    url = f"{NFT_BASE}/getNFTSales"
    params = {
        "contractAddress": contract,
        "tokenId": token_id,
        "limit": min(limit, 1000),
        "order": "asc"
    }
    events, got = [], 0
    log(f"  ↳ 売買履歴取得 start {contract[:8]}…/{token_id}")
    while True:
        res = http_get(url, params=params)
        if not res:
            break
        page = res.get("nftSales", []) or []
        events.extend(page)
        got += len(page)
        log(f"    …{len(page)}件 追加 (累計 {got}) pageKey={res.get('pageKey')}")
        if got >= limit or not res.get("pageKey"):
            break
        params["pageKey"] = res["pageKey"]
        time.sleep(REQUEST_DELAY)
    return events

def detect_quick_flips(events: list, window_sec=QUICK_FLIP_WINDOW_SEC):
    """
    連続する売買で、
      前トランザクションの購入者 == 次トランザクションの売り手
    かつ ブロック時刻差 <= window_sec を“短期保有”と判定。
    """
    if len(events) < 2:
        return []

    # 必要データを抽出し、ブロック時刻を付与
    enriched = []
    for ev in events:
        bn = ev.get("blockNumber")
        buyer = (ev.get("buyerAddress") or "").lower()
        seller = (ev.get("sellerAddress") or "").lower()
        if bn is None:
            continue
        ts = rpc_get_block_ts(bn)
        if ts is None:
            log(f"    [SKIP] blockNumber={bn} の時刻取得不可")
            continue
        enriched.append({"bn": bn, "ts": ts, "buyer": buyer, "seller": seller})

    enriched.sort(key=lambda x: x["bn"])
    flips = []
    for i in range(1, len(enriched)):
        prev, curr = enriched[i-1], enriched[i]
        if prev["buyer"] and curr["seller"] and prev["buyer"] == curr["seller"]:
            dt = curr["ts"] - prev["ts"]
            if 0 <= dt <= window_sec:
                flips.append({
                    "hold_sec": dt,
                    "buy_block": prev["bn"], "sell_block": curr["bn"],
                    "buyer_seller": prev["buyer"]
                })
    return flips

def main():
    if not ALCHEMY_API_KEY or ALCHEMY_API_KEY == "YOUR_API_KEY_HERE":
        log("[ERROR] ALCHEMY_API_KEY を設定してください")
        return

    # 1) 市場横断の最新売買を取得
    sales = get_recent_sales(limit=RECENT_SALES_LIMIT)
    if not sales:
        log("[ERROR] 市場の最新売買が0件。ネットワーク/キー/パラメータを確認してください。")
        return

    # 2) 対象NFTをユニーク化して上限までチェック
    seen = set()
    targets = []
    for s in sales:
        c = s.get("contractAddress")
        t = s.get("tokenId")
        if not c or t is None:
            continue
        key = (c.lower(), str(t))
        if key in seen:
            continue
        seen.add(key)
        targets.append(key)
        if len(targets) >= MAX_NFTS_TO_CHECK:
            break

    log(f"ユニークNFT数: {len(targets)}（上限 {MAX_NFTS_TO_CHECK}）")

    quick_results = []
    for idx, (contract, token_id) in enumerate(targets, 1):
        log(f"[{idx}/{len(targets)}] NFT {contract[:8]}…/{token_id} を解析")
        evs = get_sales_for_nft(contract, token_id, limit=DETAIL_LIMIT_PER_NFT)
        if not evs or len(evs) < 2:
            log("  ↳ 売買履歴が2件未満のためスキップ")
            continue

        flips = detect_quick_flips(evs, window_sec=QUICK_FLIP_WINDOW_SEC)
        if flips:
            for f in flips:
                hrs = f["hold_sec"] / 3600
                addr = f["buyer_seller"]
                log(f"  ✅ 短期保有検出: {hrs:.2f}h  addr={addr}  blocks={f['buy_block']}→{f['sell_block']}")
                quick_results.append({
                    "contract": contract,
                    "tokenId": token_id,
                    "addr": addr,
                    "hold_hours": round(hrs, 2)
                })
        else:
            log("  ↳ 短期保有は検出されず")

        time.sleep(REQUEST_DELAY)

    # 3) サマリ
    log("\n===== 結果サマリ =====")
    if not quick_results:
        log("短期保有は検出されませんでした。閾値/件数/対象期間を調整してください。")
    else:
        for r in quick_results:
            print(f"{r['contract']}\t{r['tokenId']}\t{r['addr']}\t{r['hold_hours']}h")

if __name__ == "__main__":
    main()
