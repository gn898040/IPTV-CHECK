import os
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- 設定區 ---
M3U_SRC = "playlists/demo.txt"  # 你的原始 M3U 或頻道清單檔案路徑 (可改為你的 URL 或本地檔)
OUTPUT_M3U = "Playlist.m3u"     # 輸出的存活 M3U 檔名
MAX_WORKERS = 10                # 併發執行緒數 (GitHub Actions 建議 5-10，避免丟包)
TIMEOUT = 8                     # 連線超時時間 (秒)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def parse_m3u(file_path):
    """ 解析 M3U 或 TXT 格式清單，返回 (title, url) 列表 """
    channels = []
    if not os.path.exists(file_path):
        print(f"找不到檔案: {file_path}")
        return channels

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [line.strip() for line in f if line.strip()]

    current_title = "未知頻道"
    for line in lines:
        if line.startswith("#EXTINF:"):
            # 擷取逗號後面的頻道名稱
            parts = line.split(",", 1)
            if len(parts) > 1:
                current_title = parts[1].strip()
        elif line.startswith("http://") or line.startswith("https://"):
            channels.append((current_title, line))
            current_title = "未知頻道"
        elif "," in line and "://" in line: # 支援名稱,URL格式 (TXT)
            parts = line.split(",", 1)
            channels.append((parts[0].strip(), parts[1].strip()))

    return channels

def check_channel(channel):
    """ 檢查單一頻道是否存活 """
    title, url = channel
    try:
        # 使用 stream=True 只讀取 Response Header，不下載整個影片流，速度極快
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=True)
        if response.status_code in [200, 206, 301, 302]:
            print(f"[OK] {title} -> {url}")
            return (title, url)
        else:
            print(f"[FAIL {response.status_code}] {title}")
    except Exception:
        print(f"[TIMEOUT/ERROR] {title}")
    return None

def main():
    print("1. 讀取頻道清單中...")
    channels = parse_m3u(M3U_SRC)
    print(f"共讀取到 {len(channels)} 個頻道，準備開始檢測...")

    alive_channels = []
    print("2. 開始多執行緒連通性檢測...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(check_channel, ch) for ch in channels]
        for future in as_completed(futures):
            res = future.result()
            if res:
                alive_channels.append(res)

    print(f"\n3. 檢測完成！存活數量: {len(alive_channels)} / {len(channels)}")

    print(f"4. 寫入 {OUTPUT_M3U}...")
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in alive_channels:
            f.write(f"#EXTINF:-1,{title}\n{url}\n")

    print("完成！")

if __name__ == "__main__":
    main()
