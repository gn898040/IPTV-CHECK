import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from opencc import OpenCC

# 初始化簡轉繁
cc = OpenCC('s2twp')

# --- 設定區 ---
BASE_URL = "http://15.204.105.50:25461/live/G2s9zK2n9m/xDtwVfWM8T"
START_ID = 0
END_ID = 100
OUTPUT_M3U = "Xtream_Scan.m3u"
MAX_WORKERS = 10    # 併發執行緒數
TIMEOUT = 5         # 超時時間 (秒)

HEADERS = {
    "User-Agent": "VLC/3.0.9 LibVLC/3.0.9",
    "Accept": "*/*"
}

def check_ts_stream(stream_id):
    """ 檢測單一 TS 頻道 ID 是否存活 """
    url = f"{BASE_URL}/{stream_id}.ts"
    try:
        # 使用 stream=True 進行串流讀取
        with requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=True) as response:
            if response.status_code in [200, 206]:
                # 嘗試讀取第一個 1KB 數據包，確認真的有影片串流流出
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        title = f"Xtream 頻道 {stream_id}"
                        print(f"[OK] 找到有效頻道！ID: {stream_id}")
                        return (stream_id, title, url)
                    break
    except Exception:
        pass
    return None

def main():
    print(f"1. 開始掃描 {START_ID} 到 {END_ID} 的頻道 ID...")
    
    # 產生 0 ~ 100 的測試任務
    tasks = list(range(START_ID, END_ID + 1))
    alive_channels = []

    print("2. 正在多執行緒進行深度檢測...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(check_ts_stream, sid) for sid in tasks]
        for future in as_completed(futures):
            res = future.result()
            if res:
                alive_channels.append(res)

    # 按照 ID 數字排序
    alive_channels.sort(key=lambda x: x[0])

    print(f"\n3. 掃描完成！成功找到 {len(alive_channels)} 個可播放頻道！")

    print(f"4. 寫入 {OUTPUT_M3U}...")
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for sid, title, url in alive_channels:
            f.write(f'#EXTINF:-1 group-title="掃描頻道",{title}\n{url}\n')

    print("完成！")

if __name__ == "__main__":
    main()
