import os
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- 設定區 ---
M3U_SOURCES = [
    {
        "url": "https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/live.m3u",
        "group": None  # 保留原分類
    },
    {
        "url": "https://iptv-org.github.io/iptv/countries/tw.m3u",
        "group": "TW"  # 強制分類為 TW
    },
    {
        "url": "https://gist.githubusercontent.com/tony881025/4ed30002f87b9e4231f47a0a6334d110/raw/4ef0d06fdd1f10411b700957aed714ee94318919/gistfile1.txt",
        "group": "X頻道"  # 強制分類為 X頻道
    }
]

OUTPUT_M3U = "Playlist2.m3u"     # 輸出的存活 M3U 檔名 (建議設為 Playlist.m3u 配合 actions)
MAX_WORKERS = 8                 # 併發執行緒數 (GitHub Actions 建議 8-10)
TIMEOUT = 8                     # 連線超時時間 (秒)

# 模擬搬瓦工採用的 VLC 播放器 User-Agent
HEADERS = {
    "User-Agent": "VLC/3.0.9 LibVLC/3.0.9",
    "Accept": "*/*"
}

def parse_m3u_text(text, default_group):
    """ 解析 M3U 或 TXT 內容 """
    channels = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    
    current_title = "未知頻道"
    current_group = default_group if default_group else "Default"
    current_logo = ""
    
    for line in lines:
        if line.startswith("#EXTINF:"):
            # 抓取 tvg-logo
            logo_match = re.search(r'tvg-logo="([^"]*)"', line)
            current_logo = logo_match.group(1) if logo_match else ""

            if "group-title=" in line:
                try:
                    start = line.index('group-title="') + 13
                    end = line.index('"', start)
                    current_group = line[start:end]
                except:
                    pass
            parts = line.split(",", 1)
            if len(parts) > 1:
                current_title = parts[1].strip()
        elif line.startswith("#genre#"):
            pass
        elif line.startswith("http://") or line.startswith("https://"):
            final_group = default_group if default_group else current_group
            channels.append({
                "title": current_title,
                "url": line,
                "group": final_group,
                "logo": current_logo
            })
            current_title = "未知頻道"
            current_logo = ""
        elif "," in line and "://" in line: # 支援 TXT 格式
            parts = line.split(",", 1)
            channels.append({
                "title": parts[0].strip(),
                "url": parts[1].strip(),
                "group": default_group if default_group else "Default",
                "logo": ""
            })

    return channels

def check_channel_deep(ch):
    """ 
    【搬瓦工級別的深度檢測】
    模擬播放器請求，並嘗試讀取前幾 KB 的影片串流封包，確定真的有影片數據輸出
    """
    url = ch["url"]
    try:
        with requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=True) as response:
            if response.status_code in [200, 206]:
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk: # 只要能成功接收到第一塊數據封包，即代表是真的活源！
                        print(f"[OK - 串流正常] [{ch['group']}] {ch['title']}")
                        return ch
                    break
    except Exception:
        pass
    return None

def main():
    print("1. 下載並解析多組頻道清單...")
    all_channels = []
    
    for src in M3U_SOURCES:
        url = src["url"]
        forced_group = src["group"]
        print(f"正在處理網址: {url} (指定分類: {forced_group if forced_group else '維持原樣'})")
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            res.encoding = 'utf-8'
            if res.status_code == 200:
                parsed = parse_m3u_text(res.text, forced_group)
                all_channels.extend(parsed)
                print(f"成功載入 {len(parsed)} 個頻道")
            else:
                print(f"下載失敗，HTTP 狀態碼: {res.status_code}")
        except Exception as e:
            print(f"下載發生錯誤: {e}")

    # 針對 URL 去重
    seen_urls = set()
    unique_channels = []
    for ch in all_channels:
        if ch["url"] not in seen_urls:
            seen_urls.add(ch["url"])
            unique_channels.append(ch)

    print(f"\n2. 合計不重複頻道 {len(unique_channels)} 個，開始進行深度串流連通性檢測...")

    alive_channels = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(check_channel_deep, ch) for ch in unique_channels]
        for future in as_completed(futures):
            res = future.result()
            if res:
                alive_channels.append(res)

    print(f"\n3. 檢測完成！真存活數量: {len(alive_channels)} / {len(unique_channels)}")

    print(f"4. 寫入 {OUTPUT_M3U}...")
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in alive_channels:
            logo_attr = f' tvg-logo="{ch["logo"]}"' if ch.get("logo") else ""
            f.write(f'#EXTINF:-1 group-title="{ch["group"]}"{logo_attr},{ch["title"]}\n{ch["url"]}\n')

    print("完成！")

if __name__ == "__main__":
    main()
