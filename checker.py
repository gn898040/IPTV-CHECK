import os
import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from opencc import OpenCC

# 初始化 OpenCC 轉換器 (s2twp: 簡體轉繁體台灣正體用語)
cc = OpenCC('s2twp')

# --- 設定區 ---
M3U_SOURCES = [
    {
        "url": "https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/live.m3u",
        "group": None  # 保留原分類 (自動轉繁體)
    },
    {
        "url": "https://iptv-org.github.io/iptv/countries/tw.m3u",
        "group": "TW"  # 強制分類為 TW
    },
    {
        "url": "https://raw.githubusercontent.com/imDazui/Tvlist-awesome-m3u-m3u8/master/m3u/%E5%8F%B0%E6%B9%BE%E9%A6%99%E6%B8%AF%E6%BE%B3%E9%97%A8202506.m3u",
        "group": "TW2頻道"
    },
    {
        "url": "https://gist.githubusercontent.com/tony881025/4ed30002f87b9e4231f47a0a6334d110/raw/4ef0d06fdd1f10411b700957aed714ee94318919/gistfile1.txt",
        "group": "X頻道"
    },
    {
        "url": "https://github.com/hujingguang/ChinaIPTV/raw/refs/heads/main/xxx.m3u8",
        "group": "X頻道"
    },
    {
        "url": "https://raw.githubusercontent.com/xiongjian83/TvBox/refs/heads/main/18.txt",
        "group": "X頻道"
    }
]

OUTPUT_M3U = "Playlist.m3u"     # 輸出的存活 M3U 檔名
MAX_WORKERS = 8                 # 併發執行緒數 (GitHub Actions 建議 8-10)
TIMEOUT = 10                    # 連線超時時間 (秒)

HEADERS = {
    "User-Agent": "VLC/3.0.9 LibVLC/3.0.9",
    "Accept": "*/*"
}

def clean_channel_title(title):
    """ 清理頻道名稱（轉換繁體並移除畫質/雜訊標記） """
    title = cc.convert(title)
    # 移除如 [720p], (1080p), [HD], - 備用 等字樣
    title = re.sub(r'\[.*?\]|\(.*?\)', '', title)
    title = re.sub(r'(HD|SD|FHD|4K|1080p|720p|480p)', '', title, flags=re.IGNORECASE)
    # 移除常見的無效字元與前後空白
    title = title.replace("-", "").replace("_", "").strip()
    return title

def parse_m3u_text(text, default_group):
    """ 解析 M3U 或 TXT 內容 """
    channels = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    
    current_title = "未知頻道"
    current_group = cc.convert(default_group) if default_group else "Default"
    current_logo = ""
    
    for line in lines:
        if line.startswith("#EXTINF:"):
            # 1. 抓取 tvg-logo
            logo_match = re.search(r'tvg-logo="([^"]*)"', line)
            current_logo = logo_match.group(1) if logo_match else ""

            # 2. 抓取 group-title (使用正則避免切割例外)
            if not default_group:
                group_match = re.search(r'group-title="([^"]*)"', line)
                if group_match:
                    current_group = cc.convert(group_match.group(1))
            else:
                current_group = cc.convert(default_group)

            # 3. 抓取頻道名稱
            parts = line.split(",", 1)
            if len(parts) > 1:
                current_title = clean_channel_title(parts[1].strip())

        elif line.startswith("#genre#"):
            pass

        elif line.startswith("http://") or line.startswith("https://"):
            final_group = cc.convert(default_group) if default_group else current_group
            channels.append({
                "title": current_title,
                "url": line,
                "group": final_group,
                "logo": current_logo
            })
            current_title = "未知頻道"
            current_logo = ""

        elif "," in line and "://" in line: # 支援 TXT 格式 (頻道名,網址)
            parts = line.split(",", 1)
            raw_title = clean_channel_title(parts[0].strip())
            channels.append({
                "title": raw_title,
                "url": parts[1].strip(),
                "group": cc.convert(default_group) if default_group else "Default",
                "logo": ""
            })

    return channels

def check_channel_deep(ch, retry=1):
    """ 深度串流檢測 (含失敗重試) """
    url = ch["url"]
    for attempt in range(retry + 1):
        try:
            with requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=True) as response:
                if response.status_code in [200, 206]:
                    for chunk in response.iter_content(chunk_size=1024):
                        if chunk:
                            print(f"[OK - 串流正常] [{ch['group']}] {ch['title']}")
                            return ch
                        break
        except Exception:
            pass
        
        # 失敗時稍作停頓後重試
        if attempt < retry:
            time.sleep(0.5)
            
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

    # 全域 Logo 收集字典 (讓同名頻道共享台標圖示)
    logo_dict = {}
    for ch in all_channels:
        if ch["logo"] and ch["title"] not in logo_dict:
            logo_dict[ch["title"]] = ch["logo"]

    # 針對 URL 去重
    seen_urls = set()
    unique_channels = []
    for ch in all_channels:
        if ch["url"] not in seen_urls:
            seen_urls.add(ch["url"])
            # 若無台標則補上同名頻道的 Logo
            if not ch["logo"] and ch["title"] in logo_dict:
                ch["logo"] = logo_dict[ch["title"]]
            unique_channels.append(ch)

    print(f"\n2. 合計不重複頻道 {len(unique_channels)} 個，開始進行深度串流連通性檢測...")

    alive_channels = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(check_channel_deep, ch) for ch in unique_channels]
        for future in as_completed(futures):
            res = future.result()
            if res:
                alive_channels.append(res)

    # 按 分類 -> 頻道名稱 排序，讓輸出更美觀
    alive_channels.sort(key=lambda x: (x["group"], x["title"]))

    print(f"\n3. 檢測完成！真存活數量: {len(alive_channels)} / {len(unique_channels)}")

    print(f"4. 寫入標準 M3U 清單至 {OUTPUT_M3U}...")
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in alive_channels:
            logo_attr = f' tvg-logo="{ch["logo"]}"' if ch.get("logo") else ""
            f.write(f'#EXTINF:-1 group-title="{ch["group"]}" tvg-name="{ch["title"]}"{logo_attr},{ch["title"]}\n{ch["url"]}\n')

    print("完成！")

if __name__ == "__main__":
    main()
