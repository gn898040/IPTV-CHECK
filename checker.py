import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- 設定區 ---
# 這裡改用字典清單，你可以為每一組指定專屬的分類名稱 (group_title)
# 如果不想指定分類（維持原本來源的分類），可以填 None
M3U_SOURCES = [
#    {
#        "url": "https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/live.m3u",
#        "group": None  # 使用來源原本的分類
#    },
    {
        "url": "https://iptv-org.github.io/iptv/countries/tw.m3u",
        "group": None  # 使用來源原本的分類
    },
    {
        "url": "https://gist.githubusercontent.com/tony881025/4ed30002f87b9e4231f47a0a6334d110/raw/4ef0d06fdd1f10411b700957aed714ee94318919/gistfile1.txt",
        "group": "X頻道"  # <-- 這組網址掃出來的全部強制歸類到 "X頻道" 分類夾！
    }
]

OUTPUT_M3U = "Playlist.m3u"     # 輸出的存活 M3U 檔名
MAX_WORKERS = 10                # 併發執行緒數
TIMEOUT = 8                     # 連線超時時間 (秒)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def parse_m3u_text(text, default_group):
    """ 解析 M3U 或 TXT 內容，並可強制指定預設分類 """
    channels = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    
    current_title = "未知頻道"
    current_group = default_group if default_group else "Default"
    
    for line in lines:
        if line.startswith("#EXTINF:"):
            # 嘗試抓取原本的 group-title
            if "group-title=" in line:
                try:
                    start = line.index('group-title="') + 13
                    end = line.index('"', start)
                    current_group = line[start:end]
                except:
                    pass
            
            # 抓取頻道名稱
            parts = line.split(",", 1)
            if len(parts) > 1:
                current_title = parts[1].strip()
                
        elif line.startswith("#genre#"):
            # 處理 TXT 格式的分類標題
            pass
            
        elif line.startswith("http://") or line.startswith("https://"):
            # 如果有指定強制分類，就覆蓋掉原本的分類
            final_group = default_group if default_group else current_group
            channels.append({
                "title": current_title,
                "url": line,
                "group": final_group
            })
            current_title = "未知頻道"
            
        elif "," in line and "://" in line: # 支援 名稱,URL 格式 (TXT)
            parts = line.split(",", 1)
            channels.append({
                "title": parts[0].strip(),
                "url": parts[1].strip(),
                "group": default_group if default_group else "Default"
            })

    return channels

def check_channel(ch):
    """ 檢查單一頻道是否存活 """
    try:
        response = requests.get(ch["url"], headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=True)
        if response.status_code in [200, 206, 301, 302]:
            print(f"[OK] [{ch['group']}] {ch['title']}")
            return ch
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

    print(f"\n2. 合計不重複頻道 {len(unique_channels)} 個，開始多執行緒連通性檢測...")

    alive_channels = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(check_channel, ch) for ch in unique_channels]
        for future in as_completed(futures):
            res = future.result()
            if res:
                alive_channels.append(res)

    print(f"\n3. 檢測完成！存活數量: {len(alive_channels)} / {len(unique_channels)}")

    print(f"4. 寫入帶有分類的 {OUTPUT_M3U}...")
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in alive_channels:
            f.write(f'#EXTINF:-1 group-title="{ch["group"]}",{ch["title"]}\n{ch["url"]}\n')

    print("完成！")

if __name__ == "__main__":
    main()
