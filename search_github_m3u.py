import os
import re
import requests
import time
from datetime import datetime, timedelta, timezone

# --- 設定區 ---
MAX_DAYS_OLD = 30
OUTPUT_M3U = "Github_Collected.m3u"

# 廣義關鍵字 (保持原本正常版的搜尋語法)
SEARCH_QUERIES = [
    "filename:playlist.m3u",
    "filename:playlist.txt",
    "filename:live.m3u",
    "filename:tv.m3u",
    "extm3u IPTV"
]

# 僅排除「網路遊戲/娛樂直播平台」（不影響傳統電視台）
EXCLUDE_STREAM_PLATFORMS = [
    "鬥魚", "斗鱼", "douyu",
    "虎牙", "huya",
    "嗶哩嗶哩", "哔哩哔哩", "bilibili", "b站",
    "YY直播", "yy.com",
    "抖音", "douyin",
    "快手", "kuaishou",
    "twitch", "章魚直播", "企鵝電競", "花椒直播", "映客"
]

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "") 

HEADERS = {
    "User-Agent": "VLC/3.0.9 LibVLC/3.0.9",
    "Accept": "application/vnd.github.v3+json"
}

if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"

def is_stream_platform(title, url):
    """ 僅過濾鬥魚/虎牙等網路直播平台 """
    check_text = f"{title} {url}".lower()
    for kw in EXCLUDE_STREAM_PLATFORMS:
        if kw in check_text:
            return True
    return False

def is_file_updated_recently(owner, repo, path):
    """ 用 Python 精準檢查檔案最後 Commit 是否在最近 MAX_DAYS_OLD 天內 """
    url = f"https://api.github.com/repos/{owner}/{repo}/commits?path={path}&page=1&per_page=1"
    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        if res.status_code == 200:
            commits = res.json()
            if commits:
                commit_date_str = commits[0]["commit"]["committer"]["date"]
                commit_date = datetime.strptime(commit_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                
                days_diff = (datetime.now(timezone.utc) - commit_date).days
                if days_diff <= MAX_DAYS_OLD:
                    print(f"  └ [時間符合] 檔案更新於 {days_diff} 天前 ({commit_date_str[:10]})")
                    return True
                else:
                    print(f"  └ [時間跳過] 檔案更新於 {days_diff} 天前 (超過 {MAX_DAYS_OLD} 天)")
                    return False
    except Exception as e:
        print(f"  └ 無法取得時間 ({e})，預設放行檢測。")
        return True
    return False

def search_github_playlist_files():
    """ 完全沿用正常版的搜尋邏輯 """
    valid_files = []
    seen_paths = set()
    
    print(f"1. 開始從 GitHub 搜尋熱門 IPTV 清單...")
    
    for query in SEARCH_QUERIES:
        print(f"\n[執行搜尋]: {query}")
        for page in range(1, 3):
            url = f"https://api.github.com/search/code?q={query}&per_page=30&page={page}"
            try:
                res = requests.get(url, headers=HEADERS, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    items = data.get("items", [])
                    if not items:
                        break
                        
                    print(f"  └ 第 {page} 頁找到 {len(items)} 個檔案，開始驗證更新時間...")
                    
                    for item in items:
                        repo_full_name = item.get("repository", {}).get("full_name", "")
                        path = item.get("path", "")
                        unique_key = f"{repo_full_name}/{path}"
                        
                        if not repo_full_name or not path or unique_key in seen_paths:
                            continue
                        
                        seen_paths.add(unique_key)
                        owner, repo_name = repo_full_name.split("/")
                        
                        print(f"檢查: [{repo_full_name}] -> {path}")
                        if is_file_updated_recently(owner, repo_name, path):
                            html_url = item.get("html_url", "")
                            raw_url = html_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                            valid_files.append({
                                "repo": repo_full_name,
                                "raw_url": raw_url,
                                "path": path
                            })
                        time.sleep(0.3)
                elif res.status_code == 403:
                    print("  └ 觸發 GitHub API Rate Limit，跳過此查詢。")
                    break
                else:
                    break
            except Exception as e:
                print(f"  └ 搜尋發生錯誤: {e}")
                break
                
    return valid_files

def parse_playlist_content(raw_url):
    """ 完全沿用正常版的解析邏輯，只加上鬥魚/虎牙過濾 """
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
                        # 排除鬥魚/虎牙等直播平台
                        if not is_stream_platform(current_title, line):
                            temp_channels.append((current_title, current_inf, line))
                        current_inf = ""
                        current_title = ""

                # TXT 格式 (頻道名,網址)
                elif "," in line and "://" in line and not line.startswith("#"):
                    parts = line.split(",", 1)
                    title = parts[0].strip()
                    url = parts[1].strip()
                    
                    if re.search(r'[\u4e00-\u9fa5]', title):
                        has_chinese_title = True
                    
                    # 排除鬥魚/虎牙等直播平台
                    if not is_stream_platform(title, url):
                        extinf = f'#EXTINF:-1 group-title="GitHub搜集",{title}'
                        temp_channels.append((title, extinf, url))

            if has_chinese_title:
                print(f"  └ [含有中文] 成功提取 {len(temp_channels)} 個頻道！")
                return temp_channels
            else:
                print(f"  └ [無中文] 跳過此檔案。")
                return []
                
    except Exception as e:
        print(f"  └ 下載/解析失敗: {e}")
        
    return channels

def main():
    found_files = search_github_playlist_files()
    
    # 字典： {"頻道名稱": [ (inf, url), (inf, url) ]} 用於同名頻道歸類整理
    channel_dict = {}
    seen_urls = set()

    if found_files:
        print(f"\n2. 開始下載並驗證內容 (共 {len(found_files)} 個符合 30 天條件的檔案)...")
        for item in found_files:
            repo = item["repo"]
            raw_url = item["raw_url"]
            path = item["path"]
            print(f"\n正在讀取 [{repo}] -> {path}...")
            
            channels = parse_playlist_content(raw_url)
            new_count = 0
            for title, inf, stream_url in channels:
                if stream_url not in seen_urls:
                    seen_urls.add(stream_url)
                    
                    if title not in channel_dict:
                        channel_dict[title] = []
                    channel_dict[title].append((inf, stream_url))
                    new_count += 1
                    
            if channels:
                print(f"  └ 新增不重複頻道: {new_count} 個")
    else:
        print("\n未搜尋到符合條件的檔案。")

    # 排序頻道名稱
    sorted_titles = sorted(channel_dict.keys())
    total_urls = sum(len(items) for items in channel_dict.values())

    print(f"\n3. 正在將相同名稱頻道歸類整理...")
    print(f"   └ 共有 {len(sorted_titles)} 個不重複頻道名稱，合計 {total_urls} 條來源網址。")
    print(f"4. 寫入整合完畢的 M3U 清單至 {OUTPUT_M3U}...")

    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        # 同名頻道會連續集中寫出
        for title in sorted_titles:
            for inf, stream_url in channel_dict[title]:
                f.write(f"{inf}\n{stream_url}\n")

    print("搜集與同名整理完成！")

if __name__ == "__main__":
    main()
