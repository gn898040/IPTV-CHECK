import os
import re
import requests
import time
from datetime import datetime, timedelta, timezone

# --- 設定區 ---
MAX_DAYS_OLD = 30
OUTPUT_M3U = "Github_Collected.m3u"

# 使用涵蓋面最廣的搜尋關鍵字，避免過於複雜的 Qualifier 被 GitHub API 拒絕
SEARCH_QUERIES = [
    "filename:playlist.m3u",
    "filename:playlist.txt",
    "filename:live.m3u",
    "m3u tvg-name"
]

# 專門排除「網路直播平台」關鍵字
EXCLUDE_KEYWORDS = [
    "鬥魚", "斗鱼", "douyu",
    "虎牙", "huya",
    "嗶哩嗶哩", "哔哩哔哩", "bilibili", "b站",
    "YY直播", "yy.com",
    "抖音", "douyin",
    "快手", "kuaishou",
    "twitch", "章魚", "zhangyu", "企鵝電競", "egame",
    "花椒", "huajiao", "映客", "inke", "網易CC"
]

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/vnd.github.v3+json"
}

# 確保 Token 格式正確傳入
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    print("已成功載入 GITHUB_TOKEN進行身份驗證！")
else:
    print("警告：未檢測到 GITHUB_TOKEN，將以匿名模式請求（極易被限流）。")

def clean_title(title):
    """ 安全地清理頻道名稱雜訊 """
    original = title
    title = re.sub(r'\[.*?\]|\(.*?\)', '', title)
    title = re.sub(r'(HD|SD|FHD|4K|1080p|720p|480p)', '', title, flags=re.IGNORECASE)
    title = title.replace("_", " ").strip()
    return title if title else original

def is_stream_platform_channel(title, url):
    """ 判斷是否為鬥魚、虎牙等網路直播平台的頻道 """
    text_to_check = f"{title} {url}".lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in text_to_check:
            return True
    return False

def is_file_updated_recently(owner, repo, path):
    """ 用 Python 精準檢查檔案最後 Commit 是否在最近 30 天內 """
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
    """ 搜尋 GitHub 上的 IPTV 清單檔案 """
    valid_files = []
    seen_paths = set()
    
    print(f"\n1. 開始從 GitHub 搜尋熱門 IPTV 清單...")
    
    for query in SEARCH_QUERIES:
        print(f"\n[執行搜尋]: {query}")
        url = f"https://api.github.com/search/code?q={query}&per_page=30&page=1"
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                data = res.json()
                items = data.get("items", [])
                print(f"  └ 搜尋成功！共找到 {len(items)} 個潛在檔案，開始驗證更新時間...")
                
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
                    time.sleep(0.5)
            else:
                print(f"  └ API 請求未成功 (HTTP 狀態碼: {res.status_code})，原因: {res.text[:150]}")
        except Exception as e:
            print(f"  └ 搜尋發生錯誤: {e}")
            
    return valid_files

def parse_playlist_content(raw_url):
    """ 解析內容並過濾中文頻道與直播平台 """
    channels = []
    try:
        res = requests.get(raw_url, headers=HEADERS, timeout=10)
        res.encoding = 'utf-8'
        
        if res.status_code == 200:
            text = res.text
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            
            temp_channels = []
            has_chinese_title = False
            current_raw_title = ""
            
            for line in lines:
                # M3U 格式解析
                if line.startswith("#EXTINF:"):
                    parts = line.split(",", 1)
                    if len(parts) > 1:
                        current_raw_title = parts[1].strip()
                        if re.search(r'[\u4e00-\u9fa5]', current_raw_title):
                            has_chinese_title = True

                elif line.startswith("http://") or line.startswith("https://"):
                    if current_raw_title:
                        if not is_stream_platform_channel(current_raw_title, line):
                            final_title = clean_title(current_raw_title)
                            temp_channels.append((final_title, line))
                        current_raw_title = ""

                # TXT 格式解析
                elif "," in line and "://" in line and not line.startswith("#"):
                    parts = line.split(",", 1)
                    raw_title = parts[0].strip()
                    url = parts[1].strip()
                    
                    if re.search(r'[\u4e00-\u9fa5]', raw_title):
                        has_chinese_title = True
                    
                    if not is_stream_platform_channel(raw_title, url):
                        final_title = clean_title(raw_title)
                        temp_channels.append((final_title, url))

            if has_chinese_title:
                print(f"  └ [符合條件] 成功提取 {len(temp_channels)} 個頻道！")
                return temp_channels
            else:
                print(f"  └ [無中文] 跳過此檔案。")
                return []
                
    except Exception as e:
        print(f"  └ 下載/解析失敗: {e}")
        
    return channels

def main():
    found_files = search_github_playlist_files()
    
    channel_dict = {}
    seen_urls = set()

    if found_files:
        print(f"\n2. 開始下載並驗證內容 (共 {len(found_files)} 個檔案)...")
        for item in found_files:
            repo = item["repo"]
            raw_url = item["raw_url"]
            path = item["path"]
            print(f"\n正在讀取 [{repo}] -> {path}...")
            
            extracted = parse_playlist_content(raw_url)
            new_count = 0
            for title, stream_url in extracted:
                if stream_url not in seen_urls:
                    seen_urls.add(stream_url)
                    if title not in channel_dict:
                        channel_dict[title] = []
                    channel_dict[title].append(stream_url)
                    new_count += 1
                    
            if extracted:
                print(f"  └ 新增不重複頻道網址: {new_count} 個")
    else:
        print("\n未搜尋到符合條件的檔案。")

    sorted_titles = sorted(channel_dict.keys())
    total_urls = sum(len(urls) for urls in channel_dict.values())

    print(f"\n3. 正在將相同名稱頻道歸類整理...")
    print(f"   └ 共有 {len(sorted_titles)} 個不重複頻道名稱，合計 {total_urls} 條來源網址。")
    print(f"4. 寫入整合完畢的 M3U 清單至 {OUTPUT_M3U}...")

    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title in sorted_titles:
            for url in channel_dict[title]:
                f.write(f'#EXTINF:-1 group-title="GitHub搜集" tvg-name="{title}",{title}\n{url}\n')

    print("整理與寫入完成！")

if __name__ == "__main__":
    main()
