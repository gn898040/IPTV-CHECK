import os
import re
import requests
import time
from datetime import datetime, timezone

# --- 設定區 ---
MAX_DAYS_OLD = 15               # 限制條件：30天內更新的檔案
OUTPUT_M3U = "Github_Collected.m3u"
OUTPUT_REPORT = "Report.txt"     # 統計報告輸出檔名
# 搜尋包含任一詞的結果 (如果是 GitHub API，支援 OR 語法)
SEARCH_QUERY = "直播源 OR 直播 -user:gn898040 -user:Phoenix89S"         # 限制條件：搜尋關鍵字

# 原有過濾條件
EXCLUDE_STREAM_PLATFORMS = [
    "鬥魚", "斗鱼", "douyu", "虎牙", "huya",
    "嗶哩嗶哩", "哔哩哔哩", "bilibili", "b站",
    "YY直播", "yy.com", "抖音", "douyin",
    "快手", "kuaishou", "twitch", "章魚直播", "企鵝電競", "花椒直播", "映客", "網易CC"
]

EXCLUDE_CHANNELS = [
    "測試頻道", "購物台", "購物",
    "🇨🇳虎牙直播", "🇨🇳 YY频道", "🇨🇳 NewTV"
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
    """ 帶有自動退避等待機制的請求函數 """
    for attempt in range(3):
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            return res
        elif res.status_code in [429, 403]:
            print(f"  └ 觸發API限制，冷卻 10 秒後重試 (第 {attempt+1} 次)...")
            time.sleep(10)
        else:
            return res # 交由外層處理錯誤
    return None

def is_file_updated_recently(owner, repo, path):
    """ 檢查單一檔案的最後 commit 時間是否在 30 天內 """
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
                    print(f"    └ [時間符合] 檔案更新於 {days_diff} 天前")
                    return True
                else:
                    print(f"    └ [時間過舊] 檔案更新於 {days_diff} 天前 (超過 {MAX_DAYS_OLD} 天)")
                    return False
    except Exception as e:
        print(f"    └ 取得時間失敗 ({e})，預設放行。")
        return True
    return False

def search_repositories_and_get_m3u():
    """ 搜尋 Repo -> 讀取根目錄 -> 驗證檔案 """
    valid_files = []
    
    print(f"1. 開始搜尋 GitHub 專案：關鍵字 '{SEARCH_QUERY}'，依最近更新排序...")
    search_url = f"https://api.github.com/search/repositories?q={SEARCH_QUERY}&sort=updated&order=desc&per_page=20"
    
    res = safe_github_request(search_url)
    if not res or res.status_code != 200:
        print("專案搜尋失敗，請檢查網路或 Token。")
        return valid_files
        
    repos = res.json().get("items", [])
    print(f"  └ 成功找到 {len(repos)} 個活躍專案，準備檢查根目錄...\n")
    
    for repo_item in repos:
        owner = repo_item["owner"]["login"]
        repo = repo_item["name"]
        print(f"檢查專案: [{owner}/{repo}]")
        
        contents_url = f"https://api.github.com/repos/{owner}/{repo}/contents"
        c_res = safe_github_request(contents_url)
        time.sleep(1) # 溫和爬取，防鎖 API
        
        if c_res and c_res.status_code == 200:
            contents = c_res.json()
            if isinstance(contents, list):
                m3u_found = False
                for item in contents:
                    if item["type"] == "file" and item["name"].lower().endswith(".m3u"):
                        m3u_found = True
                        path = item["path"]
                        raw_url = item["download_url"]
                        print(f"  └ 發現檔案: {path}")
                        
                        if is_file_updated_recently(owner, repo, path):
                            valid_files.append({
                                "repo": f"{owner}/{repo}",
                                "raw_url": raw_url,
                                "path": path
                            })
                
                if not m3u_found:
                    print("  └ [略過] 根目錄未發現任何 .m3u 檔案。")
        else:
            print("  └ [錯誤] 無法讀取此專案目錄。")
            
    return valid_files

def parse_playlist_content(raw_url):
    """ 原有解析邏輯 """
    temp_channels = []
    try:
        res = requests.get(raw_url, headers=HEADERS, timeout=10)
        res.encoding = 'utf-8'
        
        if res.status_code == 200:
            text = res.text
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            
            current_raw_title = ""
            current_group = "GitHub搜集"
            
            for line in lines:
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

            if temp_channels:
                print(f"    └ [成功提取] 提取出 {len(temp_channels)} 個頻道！")
            else:
                print(f"    └ [跳過] 無符合過濾條件的頻道。")
                
    except Exception as e:
        print(f"    └ 下載解析失敗: {e}")
        
    return temp_channels

def main():
    found_files = search_repositories_and_get_m3u()
    
    channel_dict = {}
    seen_urls = set()

    if found_files:
        print(f"\n2. 開始下載並驗證頻道清單 (共 {len(found_files)} 個檔案)...")
        for item in found_files:
            repo = item["repo"]
            raw_url = item["raw_url"]
            path = item["path"]
            print(f"\n讀取檔案 [{repo}] -> {path}...")
            
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
                print(f"    └ 新增不重複頻道網址: {new_count} 個")
    else:
        print("\n未搜尋到符合條件的專案與檔案。")
        return

    sorted_titles = sorted(channel_dict.keys())
    total_urls = sum(len(items) for items in channel_dict.values())

    print(f"\n3. 正在將相同名稱頻道歸類整理...")
    print(f"   └ 共有 {len(sorted_titles)} 個不重複頻道名稱，合計 {total_urls} 條來源網址。")
    print(f"4. 寫入整合完畢的 M3U 清單至 {OUTPUT_M3U}...")

    # 用來統計 Group Title 數量的字典
    group_counts = {}

    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for clean_t in sorted_titles:
            for group_t, stream_url in channel_dict[clean_t]:
                f.write(f'#EXTINF:-1 group-title="{group_t}" tvg-name="{clean_t}",{clean_t}\n{stream_url}\n')
                group_counts[group_t] = group_counts.get(group_t, 0) + 1

    # 製作統計報告內容
    sorted_groups = sorted(group_counts.items(), key=lambda x: x[1], reverse=True)
    report_lines = []
    report_lines.append("==========================================")
    report_lines.append("      📊 群組分類 (Group Title) 統計表")
    report_lines.append("==========================================")
    for g_name, count in sorted_groups:
        report_lines.append(f" ► {g_name} : {count} 條串流")
    report_lines.append("==========================================")
    report_lines.append(f"總計不重複頻道數: {len(sorted_titles)} 個")
    report_lines.append(f"總計串流來源網址: {total_urls} 條")
    report_lines.append(f"更新時間 (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append("==========================================")

    report_text = "\n".join(report_lines)

    # 1. 輸出至執行日誌
    print("\n" + report_text)

    # 2. 自動寫入 Report.txt 文字檔（方便在 GitHub 專案首頁隨時查看）
    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\n已將統計報告寫入至 {OUTPUT_REPORT}！")
    print("搜集、同名整理與統計完成！")

if __name__ == "__main__":
    main()
