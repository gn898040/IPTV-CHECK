import os
import re
import requests
import time
from datetime import datetime, timedelta, timezone

# --- 設定區 ---
MAX_DAYS_OLD = 30
OUTPUT_M3U = "Github_Collected.m3u"

# 自動計算 30 天前的 UTC 日期字串 (YYYY-MM-DD)
thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=MAX_DAYS_OLD)).strftime('%Y-%m-%d')

# 放寬搜尋語法：搜尋包含 m3u 或 txt 關鍵字且 30 天內有 push 的專案
SEARCH_QUERY = f"(filename:playlist.m3u OR filename:playlist.txt OR filename:live.m3u OR filename:tv.m3u) pushed:>{thirty_days_ago}"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "") 

HEADERS = {
    "User-Agent": "VLC/3.0.9 LibVLC/3.0.9",
    "Accept": "application/vnd.github.v3+json"
}

if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"

def is_file_updated_recently(owner, repo, path):
    """ 檢查檔案最後 Commit 是否在最近 MAX_DAYS_OLD 天內 """
    url = f"https://api.github.com/repos/{owner}/{repo}/commits?path={path}&page=1&per_page=1"
    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        if res.status_code == 200:
            commits = res.json()
            if commits:
                commit_date_str = commits[0]["commit"]["committer"]["date"]
                commit_date = datetime.strptime(commit_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                
                now = datetime.now(timezone.utc)
                days_diff = (now - commit_date).days
                
                if days_diff <= MAX_DAYS_OLD:
                    print(f"  └ 檔案更新於 {days_diff} 天前 ({commit_date_str[:10]}) -> 符合近一個月條件！")
                    return True
                else:
                    print(f"  └ 檔案最後更新於 {days_diff} 天前 -> 太舊，跳過。")
                    return False
    except Exception as e:
        print(f"  └ 無法取得 Commit 時間 ({e})，預設跳過。")
    return False

def search_github_playlist_files():
    """ 搜尋近一個月有更新的 IPTV 清單檔案 """
    url = f"https://api.github.com/search/code?q={SEARCH_QUERY}&per_page=30"
    valid_files = []
    
    print(f"1. 正在搜尋 GitHub 上最近 {MAX_DAYS_OLD} 天內 (pushed:>{thirty_days_ago}) 的 IPTV 清單...")
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            data = res.json()
            items = data.get("items", [])
            print(f"搜尋成功！找到 {len(items)} 個潛在檔案，開始比對最後更新時間...\n")
            
            for item in items:
                repo_full_name = item.get("repository", {}).get("full_name", "")
                path = item.get("path", "")
                
                if not repo_full_name or not path:
                    continue
                    
                owner, repo_name = repo_full_name.split("/")
                print(f"檢查專案: [{repo_full_name}] 檔案: {path}")
                
                if is_file_updated_recently(owner, repo_name, path):
                    html_url = item.get("html_url", "")
                    raw_url = html_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                    valid_files.append({
                        "repo": repo_full_name,
                        "raw_url": raw_url,
                        "path": path
                    })
                time.sleep(0.5)
        elif res.status_code == 403:
            print("觸發 GitHub API 速率限制！請確認已帶入 GITHUB_TOKEN。")
        else:
            print(f"API 請求失敗，狀態碼: {res.status_code}")
    except Exception as e:
        print(f"搜尋發生錯誤: {e}")
        
    return valid_files

def parse_playlist_content(raw_url):
    """ 支援 M3U 與 TXT 格式，並精準過濾頻道名稱含有中文的來源 """
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
                # M3U 格式解析
                if line.startswith("#EXTINF:"):
                    current_inf = line
                    parts = line.split(",", 1)
                    if len(parts) > 1:
                        current_title = parts[1].strip()
                        if re.search(r'[\u4e00-\u9fa5]', current_title):
                            has_chinese_title = True

                elif line.startswith("http://") or line.startswith("https://"):
                    if current_inf: # M3U 格式
                        temp_channels.append((current_title, current_inf, line))
                        current_inf = ""
                        current_title = ""

                # TXT 格式解析 (例如: TVBS新聞台,http://...)
                elif "," in line and "://" in line and not line.startswith("#"):
                    parts = line.split(",", 1)
                    title = parts[0].strip()
                    url = parts[1].strip()
                    
                    if re.search(r'[\u4e00-\u9fa5]', title):
                        has_chinese_title = True
                    
                    extinf = f'#EXTINF:-1 group-title="GitHub搜集",{title}'
                    temp_channels.append((title, extinf, url))

            if has_chinese_title:
                print(f"  └ [符合] 找到包含中文名稱的頻道，提取全數頻道...")
                return [(inf, url) for title, inf, url in temp_channels]
            else:
                print(f"  └ [跳過] 檔案內所有頻道的名稱均無中文字。")
                return []
                
    except Exception as e:
        print(f"  └ 下載/解析失敗: {e}")
        
    return channels

def main():
    found_files = search_github_playlist_files()
    
    all_channels = []
    seen_urls = set()

    if found_files:
        print(f"\n2. 開始下載並驗證內容 ({len(found_files)} 個檔案)...")
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
                print(f"  └ 成功提取 {new_count} 個新頻道")
    else:
        print("\n未搜尋到符合條件的檔案。")

    # 關鍵防呆：無論是否找到頻道，強制建立/更新檔案，避免 Actions 找不到檔案報錯
    print(f"\n3. 寫入搜集成果至 {OUTPUT_M3U} (共 {len(all_channels)} 個不重複頻道)...")
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for inf, stream_url in all_channels:
            f.write(f"{inf}\n{stream_url}\n")

    print("搜集完成！")

if __name__ == "__main__":
    main()
