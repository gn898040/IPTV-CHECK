import os
import re
import time
import json
import asyncio
import aiohttp
import shutil
from dataclasses import dataclass, field
from opencc import OpenCC

# 初始化 OpenCC 轉換器 (s2twp: 簡體轉繁體台灣正體用語)
cc = OpenCC('s2twp')

# --- 設定區 ---
M3U_SOURCES = [
    {
        "url": "https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/live.m3u",
        "group": None
    },
    {
        "url": "https://raw.githubusercontent.com/gn898040/IPTV-CHECK/refs/heads/main/Github_Collected.m3u",
        "group": None
    },
    {
        "url": "https://iptv-org.github.io/iptv/countries/tw.m3u",
        "group": "TW"
    }
]

OUTPUT_M3U = "Playlist.m3u"

# 效能與檢測設定
MAX_CONCURRENT_HTTP = 50       # HTTP 檢測最大併發數
MAX_CONCURRENT_FFPROBE = 10    # FFprobe 檢測最大併發數 (避免 CPU 爆炸)
HTTP_TIMEOUT = 5.0             # HTTP 連線超時 (秒)
FFPROBE_TIMEOUT = 8.0          # FFprobe 解析超時 (秒)
ENABLE_DEEP_CHECK = True       # 是否啟用 FFprobe 深度畫質檢測

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*"
}

# --- 資料結構 ---
@dataclass
class StreamInfo:
    url: str
    latency_ms: float = 99999.0
    is_alive: bool = False
    width: int = 0
    height: int = 0
    bitrate_kbps: float = 0.0
    score: float = 0.0

@dataclass
class ChannelGroup:
    display_name: str
    group_name: str
    logo: str = ""
    streams: list = field(default_factory=list)

# --- 工具函數 ---
def clean_channel_title(title: str) -> str:
    """ 清理頻道名稱供顯示用（轉換繁體並移除畫質/雜訊標記） """
    title = cc.convert(title)
    title = re.sub(r'\[.*?\]|\(.*?\)', '', title)
    title = re.sub(r'(HD|SD|FHD|4K|1080p|720p|480p)', '', title, flags=re.IGNORECASE)
    title = title.replace("-", "").replace("_", "").strip()
    return title

def normalize_name(title: str) -> str:
    """ 極致正規化，用於內部字典歸類 (去除所有空白與符號轉小寫) """
    norm = re.sub(r'[^\w\u4e00-\u9fa5]', '', title).lower()
    return norm if norm else "unknown"

# --- 檢測核心 (異步) ---
async def run_ffprobe(url: str, semaphore: asyncio.Semaphore) -> dict:
    """ 異步執行 FFprobe 獲取解析度與碼率 """
    if not shutil.which("ffprobe"):
        return {}
    
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format",
        "-probesize", "500000", "-analyzeduration", "2000000",
        "-timeout", str(int(FFPROBE_TIMEOUT * 1000000)), url
    ]
    
    async with semaphore:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=FFPROBE_TIMEOUT + 2)
            data = json.loads(stdout)
            
            video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
            fmt = data.get("format", {})
            info = {}
            if video:
                info["width"] = int(video.get("width") or 0)
                info["height"] = int(video.get("height") or 0)
            if fmt.get("bit_rate"):
                info["bitrate_kbps"] = round(int(fmt["bit_rate"]) / 1000, 1)
            return info
        except (asyncio.TimeoutError, Exception):
            return {}

async def check_stream(session: aiohttp.ClientSession, stream: StreamInfo, 
                       http_sem: asyncio.Semaphore, ffprobe_sem: asyncio.Semaphore, retry=1):
    """ HTTP 檢測 + 聰明重試 + FFprobe 深度檢測 """
    url = stream.url
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
    
    for attempt in range(retry + 1):
        try:
            start_time = time.perf_counter()
            async with http_sem:
                async with session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True) as resp:
                    
                    # 1. 致命錯誤直接放棄 (不重試)
                    if resp.status in (403, 404, 500, 502):
                        return
                    
                    # 2. Content-Type 防偽過濾 (不重試)
                    ctype = resp.headers.get("Content-Type", "").lower()
                    if "text/html" in ctype or "application/json" in ctype:
                        return
                    
                    # 3. 讀取封包確認真有資料
                    await resp.content.read(1024)
                    
                    # --- HTTP 檢測成功 ---
                    latency = (time.perf_counter() - start_time) * 1000
                    stream.is_alive = True
                    stream.latency_ms = latency
                    
                    # 4. 執行深度檢測 (取得畫質)
                    if ENABLE_DEEP_CHECK:
                        probe = await run_ffprobe(url, ffprobe_sem)
                        stream.width = probe.get("width", 0)
                        stream.height = probe.get("height", 0)
                        stream.bitrate_kbps = probe.get("bitrate_kbps", 0.0)
                    
                    # 5. 計算綜合分數
                    calculate_score(stream)
                    print(f"[OK] 延遲:{int(latency)}ms 畫質:{stream.height}p | {url[:40]}...")
                    return

        except asyncio.TimeoutError:
            pass # 超時允許重試
        except Exception:
            pass # 其他連線錯誤允許重試
            
        if attempt < retry:
            await asyncio.sleep(0.5)

def calculate_score(stream: StreamInfo):
    """ 科學評分：延遲越低分越高，解析度與碼率越大分越高 """
    score = 0.0
    
    # 延遲得分 (基準2000ms，越小得分越高，最大40分)
    lat = stream.latency_ms
    score += max(0, (2000 - lat) / 50) 
    
    # 解析度得分 (4K=40, 1080p=30, 720p=20, 標清=10)
    h = stream.height
    if h >= 2160: score += 40
    elif h >= 1080: score += 30
    elif h >= 720: score += 20
    elif h > 0: score += 10
    
    # 碼率得分 (加權補償)
    br = stream.bitrate_kbps
    if br > 4000: score += 15
    elif br > 2000: score += 10
    elif br > 800: score += 5
    
    stream.score = score

# --- 主程式 ---
async def main():
    print("1. 下載並解析多組頻道清單...")
    channels_dict = {} # Key: 正規化名稱, Value: ChannelGroup
    logo_dict = {}     # Key: 顯示名稱, Value: Logo URL
    
    # 同步下載清單 (輕量操作維持 requests 風格，也可改 aiohttp)
    import requests
    for src in M3U_SOURCES:
        try:
            res = requests.get(src["url"], headers=HEADERS, timeout=15)
            res.encoding = 'utf-8'
            if res.status_code == 200:
                lines = [line.strip() for line in res.text.splitlines() if line.strip()]
                default_group = cc.convert(src["group"]) if src["group"] else "Default"
                
                curr_title, curr_logo, curr_group = "", "", default_group
                
                for line in lines:
                    if line.startswith("#EXTINF:"):
                        logo_m = re.search(r'tvg-logo="([^"]*)"', line)
                        curr_logo = logo_m.group(1) if logo_m else ""
                        
                        if not src["group"]:
                            group_m = re.search(r'group-title="([^"]*)"', line)
                            curr_group = cc.convert(group_m.group(1)) if group_m else "Default"
                        
                        parts = line.split(",", 1)
                        if len(parts) > 1:
                            curr_title = clean_channel_title(parts[1])
                            
                    elif line.startswith(("http://", "https://", "rtmp://")):
                        if not curr_title: continue
                        
                        norm_key = normalize_name(curr_title)
                        
                        # 收集 Logo
                        if curr_logo and curr_title not in logo_dict:
                            logo_dict[curr_title] = curr_logo
                        
                        # 頻道歸類
                        if norm_key not in channels_dict:
                            channels_dict[norm_key] = ChannelGroup(
                                display_name=curr_title,
                                group_name=curr_group
                            )
                        
                        # 避免同一頻道塞入完全相同的網址
                        exists = any(s.url == line for s in channels_dict[norm_key].streams)
                        if not exists:
                            channels_dict[norm_key].streams.append(StreamInfo(url=line))
                            
                        curr_title, curr_logo = "", ""
        except Exception as e:
            print(f"下載失敗 {src['url']}: {e}")

    # 補全 Logo
    total_streams = 0
    for ch in channels_dict.values():
        if not ch.logo and ch.display_name in logo_dict:
            ch.logo = logo_dict[ch.display_name]
        total_streams += len(ch.streams)

    print(f"\n2. 完成歸類！共 {len(channels_dict)} 個獨立頻道，合計 {total_streams} 條線路準備檢測...")

    # 開始非同步檢測
    http_sem = asyncio.Semaphore(MAX_CONCURRENT_HTTP)
    ffprobe_sem = asyncio.Semaphore(MAX_CONCURRENT_FFPROBE)
    
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT_HTTP, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for ch in channels_dict.values():
            for stream in ch.streams:
                tasks.append(check_stream(session, stream, http_sem, ffprobe_sem))
        
        await asyncio.gather(*tasks)

    # 排序與寫入
    print(f"\n3. 檢測完成！開始寫入 {OUTPUT_M3U}...")
    alive_count = 0
    
    # 將字典轉為列表，依照群組與名稱排序，M3U 呈現更整齊
    sorted_channels = sorted(channels_dict.values(), key=lambda x: (x.group_name, x.display_name))
    
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        
        for ch in sorted_channels:
            # 篩選存活線路並依照「分數」由高到低排序 (最棒的排前面)
            valid_streams = [s for s in ch.streams if s.is_alive]
            if not valid_streams:
                continue
                
            valid_streams.sort(key=lambda x: x.score, reverse=True)
            alive_count += 1
            
            logo_attr = f' tvg-logo="{ch.logo}"' if ch.logo else ""
            
            # 寫入 M3U (完全不加備用標籤，依序寫出)
            for stream in valid_streams:
                f.write(f'#EXTINF:-1 group-title="{ch.group_name}" tvg-name="{ch.display_name}"{logo_attr},{ch.display_name}\n')
                f.write(f'{stream.url}\n')

    print(f"完成！成功存活頻道數：{alive_count} / {len(channels_dict)}")

if __name__ == "__main__":
    # Windows 下避免 asyncio 報錯的標準寫法
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
