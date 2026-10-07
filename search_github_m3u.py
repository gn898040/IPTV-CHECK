import os
import re
import requests
import time
from datetime import datetime, timedelta, timezone

# --- 設定區 ---
MAX_DAYS_OLD = 30
OUTPUT_M3U = "Github_Collected.m3u"

# 30 天前的時間基準字串 (YYYY-MM-DD)
thirty_days_ago_str = (datetime.now(timezone.utc) - timedelta(days=MAX_DAYS_OLD)).strftime('%Y-%m-%d')

# 多組涵蓋廣泛的搜尋語法 (含時間過濾)
SEARCH_QUERIES = [
    f"filename:playlist.m3u pushed:>{thirty_days_ago_str}",
    f"filename:playlist.txt pushed:>{thirty_days_ago_str}",
    f"filename:live.m3u pushed:>{thirty_days_ago_str}",
    f"filename:tv.m3u pushed:>{thirty_days_ago_str}",
    f"extm3u pushed:>{thirty_days_ago_str}"
]

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "") 

HEADERS = {
    "User-Agent": "VLC/3.0.9 LibVLC/3.0.9",
    "Accept": "application/vnd.github.v3+json"
}

if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"

def search_github_playlist_files():
    """ 輪詢搜尋條件並進行多頁翻頁 (Pagination)，大幅增加搜尋量 """
    valid_files = []
    seen_paths = set()
    
    print(f"1. 開始從 GitHub 搜尋近 {MAX_DAYS_OLD} 天內 (pushed:>{thirty_days_ago_str}) 的 IPTV 清單...")
    
    for query in SEARCH_QUERIES:
        print(f"\n[搜尋關鍵字]: {query}")
        # 翻頁：抓取第 1 到第 3 頁，每頁 30 筆，總共可搜尋近百個專案
        for page in range(1, 4):
            url = f"https://api.github.com/search/code?q={query}&per_page=30&page={page}"
            try:
                res = requests.get(url, headers=HEADERS, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    items = data.get("items", [])
                    if not items:
                        break  # 後續沒頁數了，換下一個關鍵字
                        
                    print(f"  └ 第 {page} 頁找到 {len(items)} 個潛在檔案")
                    
                    for item in items:
                        repo_full_name = item.get("repository", {}).get("full_name", "")
                        path = item.get("path", "")
                        unique_key = f"{repo_full_name}/{path}"
                        
                        if not repo_full_name or not path or unique_key in seen_paths:
                            continue
                        
                        seen_paths.add(unique_key)
                        html_url = item.get("html_url", "")
                        raw_url = html_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                        
                        valid_files.append({
                            "repo": repo_full_name,
                            "raw_url": raw_url,
                            "path": path
                        })
                    time.sleep(0.5)
                elif res.status_code == 403:
                    print("  └ 觸發 GitHub API 速率限制，停止當前查詢。")
                    break
                else:
                    break
            except Exception as e:
                print(f"  └ 搜尋發生錯誤: {e}")
                break
                
    return valid_files

def parse_playlist_content(raw_url):
    """ 解析 M3U / TXT 內容並檢查頻道標題是否含有中文字 """
    channels = []
    try:
        res = requests.get(raw_url, headers=HEADERS, timeout=10)
        res.encoding = 'utf-8'
        
        if res.status_code == 200:
            text = res.text
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            
            temp_channels = []
            has_chinese_title = False
            current_inf = ""
            current_title = ""
            
            for line in lines:
                # M3U 格式
                if line.startswith("#EXTINF:"):
                    current_inf = line
                    parts = line.split(",", 1)
                    if len(parts) > 1:
                        current_title = parts[1].strip()
                        if re.search(r'[\u4e00-\u9fa5]', current_title):
                            has_chinese_title = True

                elif line.startswith("http://") or line.startswith("https://"):
                    if current_inf:
                        temp_channels.append((current_title, current_inf, line))
                        current_inf = ""
                        current_title = ""

                # TXT 格式 (例如: TVBS新聞台,http://...)
                elif "," in line and "://" in line and not line.startswith("#"):
                    parts = line.split(",", 1)
                    title = parts[0].strip()
                    url = parts[1].strip()
                    
                    if re.search(r'[\u4e00-\u9fa5]', title):
                        has_chinese_title = True
                    
                    extinf = f'#EXTINF:-1 group-title="GitHub搜集",{title}'
                    temp_channels.append((title, extinf, url))

            if has_chinese_title:
                print(f"  └ [符合中文] 成功提取 {len(temp_channels)} 個頻道！")
                return [(inf, url) for title, inf, url in temp_channels]
            else:
                print(f"  └ [跳過中文] 檔案內頻道名稱均無中文標題。")
                return []
                
    except Exception as e:
        print(f"  └ 下載/解析失敗: {e}")
        
    return channels

def main():
    found_files = search_github_playlist_files()
    
    all_channels = []
    seen_urls = set()

    if found_files:
        print(f"\n2. 開始下載並驗證內容 (共找到 {len(found_files)} 個候選檔案)...")
        for item in found_files:
            repo = item["repo"]
            raw_url = item["raw_url"]
            path = item["path"]
            print(f"\n正在讀取 [{repo}] -> {path}...")
            
            channels = parse_playlist_content(raw_url)
            new_count = 0
            for inf, stream_url in channels:
                if stream_url not in seen_urls:
                    seen_urls.add(stream_url)
                    all_channels.append((inf, stream_url))
                    new_count += 1
                    
            if channels:
                print(f"  └ 新增不重複頻道: {new_count} 個")
    else:
        print("\n未搜尋到符合條件的檔案。")

    print(f"\n3. 寫入搜集成果至 {OUTPUT_M3U} (共 {len(all_channels)} 個不重複頻道)...")
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for inf, stream_url in all_channels:
            f.write(f"{inf}\n{stream_url}\n")

    print("搜集完成！")

if __name__ == "__main__":
    main()
