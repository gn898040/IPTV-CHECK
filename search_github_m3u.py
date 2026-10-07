import os
import re
import requests
import time
from datetime import datetime, timedelta, timezone

# --- 設定區 ---
MAX_DAYS_OLD = 30
OUTPUT_M3U = "Github_Collected.m3u"

SEARCH_QUERIES = [
    "filename:playlist.m3u",
    "filename:playlist.txt",
    "filename:live.m3u",
    "filename:tv.m3u",
    "extm3u IPTV"
]

EXCLUDE_STREAM_PLATFORMS = [
    "鬥魚", "斗鱼", "douyu",
    "虎牙", "huya",
    "嗶哩嗶哩", "哔哩哔哩", "bilibili", "b站",
    "YY直播", "yy.com",
    "抖音", "douyin",
    "快手", "kuaishou",
    "twitch", "章魚直播", "企鵝電競", "花椒直播", "映客", "網易CC"
]

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/vnd.github.v3+json"
}

if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"

def clean_title(title):
    original = title
    title = re.sub(r'\[.*?\]|\(.*?\)', '', title)
    title = re.sub(r'(HD|SD|FHD|4K|1080p|720p|480p)', '', title, flags=re.IGNORECASE)
    title = title.replace("_", " ").strip()
    return title if title else original

def extract_group_title(inf_line):
    """ 從原始 #EXTINF 行提取 group-title，若無則回傳預設值 """
    match = re.search(r'group-title="([^"]+)"', inf_line, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "GitHub搜集"

def is_stream_platform(title, url):
    check_text = f"{title} {url}".lower()
    for kw in EXCLUDE_STREAM_PLATFORMS:
        if kw in check_text:
            return True
    return False

def is_valid_chinese_or_tv_channel(title, full_text):
    if re.search(r'[\u4e00-\u9fa5]', title):
        return True
    if re.search(r'\b(CCTV|TVBS|HBO|CTV|CTS|FTV|TTV|凤凰|衛視)\b', title, re.IGNORECASE):
        return True
    if re.search(r'[\u4e00-\u9fa5]', full_text):
        return True
    return False

def safe_github_request(url):
    for attempt in range(3):
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            return res
        elif res.status_code in [429, 403]:
            print(f"  └ 觸發限制 (HTTP {res.status_code})，冷卻 10 秒後重試 (第 {attempt+1} 次)...")
            time.sleep(10)
        else:
            print(f"  └ API 請求失敗，HTTP 狀態碼: {res.status_code}")
            break
    return res

def is_file_updated_recently(owner, repo, path):
    url = f"https://api.github.com/repos/{owner}/{repo}/commits?path={path}&page=1&per_page=1"
    try:
        res = safe_github_request(url)
        if res and res.status_code == 200:
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
    valid_files = []
    seen_paths = set()
    
    print(f"1. 開始從 GitHub 搜尋熱門 IPTV 清單...")
    
    for query in SEARCH_QUERIES:
        print(f"\n[執行搜尋]: {query}")
        for page in range(1, 3):
            url = f"https://api.github.com/search/code?q={query}&sort=indexed&order=desc&per_page=20&page={page}"
            try:
                res = safe_github_request(url)
                if res and res.status_code == 200:
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
                        time.sleep(0.8)
                else:
                    break
            except Exception as e:
                print(f"  └ 搜尋發生錯誤: {e}")
                break
                
    return valid_files

def parse_playlist_content(raw_url):
    channels = []
    try:
        res = requests.get(raw_url, headers=HEADERS, timeout=10)
        res.encoding = 'utf-8'
        
        if res.status_code == 200:
            text = res.text
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            
            temp_channels = []
            current_raw_title = ""
            current_group = "GitHub搜集"
            
            for line in lines:
                # M3U 格式解析
                if line.startswith("#EXTINF:"):
                    current_group = extract_group_title(line)
                    parts = line.split(",", 1)
                    if len(parts) > 1:
                        current_raw_title = parts[1].strip()

                elif line.startswith("http://") or line.startswith("https://"):
                    if current_raw_title:
                        if not is_stream_platform(current_raw_title, line):
                            if is_valid_chinese_or_tv_channel(current_raw_title, text):
                                final_title = clean_title(current_raw_title)
                                temp_channels.append((final_title, current_group, line))
                        current_raw_title = ""
                        current_group = "GitHub搜集"

                # TXT 格式解析 (頻道名,網址)
                elif "," in line and "://" in line and not line.startswith("#"):
                    parts = line.split(",", 1)
                    raw_title = parts[0].strip()
                    url = parts[1].strip()
                    
                    if not is_stream_platform(raw_title, url):
                        if is_valid_chinese_or_tv_channel(raw_title, text):
                            final_title = clean_title(raw_title)
                            temp_channels.append((final_title, "GitHub搜集", url))

            if temp_channels:
                print(f"  └ [成功提取] 找到 {len(temp_channels)} 個頻道！")
                return temp_channels
            else:
                print(f"  └ [跳過] 無符合條件頻道。")
                return []
                
    except Exception as e:
        print(f"  └ 下載/解析失敗: {e}")
        
    return channels

def main():
    found_files = search_github_playlist_files()
    
    # 字典： {"標準頻道名": [ (group_title, stream_url), ... ]}
    channel_dict = {}
    seen_urls = set()

    if found_files:
        print(f"\n2. 開始下載並驗證內容 (共 {len(found_files)} 個符合條件的檔案)...")
        for item in found_files:
            repo = item["repo"]
            raw_url = item["raw_url"]
            path = item["path"]
            print(f"\n正在讀取 [{repo}] -> {path}...")
            
            channels = parse_playlist_content(raw_url)
            new_count = 0
            for clean_t, group_t, stream_url in channels:
                if stream_url not in seen_urls:
                    seen_urls.add(stream_url)
                    
                    if clean_t not in channel_dict:
                        channel_dict[clean_t] = []
                    channel_dict[clean_t].append((group_t, stream_url))
                    new_count += 1
                    
            if channels:
                print(f"  └ 新增不重複頻道: {new_count} 個")
    else:
        print("\n未搜尋到符合條件的檔案。")

    sorted_titles = sorted(channel_dict.keys())
    total_urls = sum(len(items) for items in channel_dict.values())

    print(f"\n3. 正在將相同名稱頻道歸類整理...")
    print(f"   └ 共有 {len(sorted_titles)} 個不重複頻道名稱，合計 {total_urls} 條來源網址。")
    print(f"4. 寫入整合完畢的 M3U 清單至 {OUTPUT_M3U}...")

    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for clean_t in sorted_titles:
            for group_t, stream_url in channel_dict[clean_t]:
                # 動態帶入抓取到的 group_t，完美保留原本的頻道分類！
                f.write(f'#EXTINF:-1 group-title="{group_t}" tvg-name="{clean_t}",{clean_t}\n{stream_url}\n')

    print("搜集與同名整理完成！")

if __name__ == "__main__":
    main()
