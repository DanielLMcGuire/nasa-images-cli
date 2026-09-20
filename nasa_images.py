#!/usr/bin/env python3
import argparse
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import itertools
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import socket
import random
import shutil

API_ROOT = 'https://images-api.nasa.gov'
ASSET_BASE = 'https://images-assets.nasa.gov'
SIZE_ORDER = ['~orig', '~large', '~medium', '~small', '~thumb']
NASA_WORM_LOGO = r""" ___     _      __     ______      __
/###\   |#|    /##\   /######|    /##\
|#|\#\  |#|   /#/\#\ |##(____    /#/\#\
|#| \#\ |#|  /#/  \#\ \######\  /#/  \#\
|#|  \#\|#| /#/    \#\_____)##|/#/    \#\
|#|   \###//#/      \########//#/      \#\ Images Library
 ‾     ‾‾‾ ‾‾        ‾‾‾‾‾‾‾‾ ‾‾        ‾‾"""
MAX_RETRIES = 3
RETRY_BASE  = 1.5 # seconds

MEDIA_CHOICES = {
    'all':   {'image', 'video'},
    'image': {'image'},
    'video': {'video'},
}

MEDIA_PARAM = {'all': 'image,video', 'image': 'image', 'video': 'video'}

VIDEO_QUALITIES = ['orig', 'large', 'medium', 'small', 'mobile', 'preview']
VIDEO_EXTS = ['.mp4', '.mov', '.m4v', '.webm', '.avi', '.mpg', '.mpeg', '.wmv']
DEFAULT_VIDEO_QUALITY = 'large'

class WinProgress:
    HIDDEN        = 0
    NORMAL        = 1
    ERROR         = 2
    INDETERMINATE = 3
    WARNING       = 4

    _enabled = 'WT_SESSION' in os.environ or os.getenv("TERM_PROGRAM") == "ghostty"

    @staticmethod
    def _write(mode, value=0):
        if not WinProgress._enabled:
            return
        sys.stdout.write(f'\x1b]9;4;{mode};{value}\x07')
        sys.stdout.flush()

    @staticmethod
    def start():
        WinProgress._write(WinProgress.INDETERMINATE)

    @staticmethod
    def set(pct: int):
        WinProgress._write(WinProgress.NORMAL, max(0, min(100, pct)))

    @staticmethod
    def error():
        WinProgress._write(WinProgress.ERROR, 100)

    @staticmethod
    def done():
        WinProgress._write(WinProgress.HIDDEN)

class Color:
    PURPLE = '\033[95m'
    CYAN   = '\033[96m'
    BLUE   = '\033[94m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    RED    = '\033[91m'
    BOLD   = '\033[1m'
    END    = '\033[0m'

class Spinner:
    def __init__(self, message="Loading..."):
        self.spinner = itertools.cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'])
        self.busy = False
        self.delay = 0.1
        self.message = message
        self.thread = None
        self._lock = threading.Lock()

    def _spin(self):
        while self.busy:
            with self._lock:
                sys.stdout.write(f'\r{Color.CYAN}{next(self.spinner)}{Color.END} {self.message}')
                sys.stdout.flush()
            time.sleep(self.delay)

    def __enter__(self):
        self.busy = True
        WinProgress.start()
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.busy = False
        WinProgress.done()
        if self.thread:
            self.thread.join()
        sys.stdout.write('\r' + ' ' * (len(self.message) + 4) + '\r')
        sys.stdout.flush()

_ROMAN_MAP = [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]
def to_roman(n: int) -> str:
    result = ''
    for value, numeral in _ROMAN_MAP:
        while n >= value:
            result += numeral
            n -= value
    return result

def from_roman(s: str) -> int:
    roman_val = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    res = 0
    for i in range(len(s)):
        cur = roman_val.get(s[i].upper(), 0)
        nxt = roman_val.get(s[i+1].upper(), 0) if i + 1 < len(s) else 0
        res += -cur if cur < nxt else cur
    return res

def arabic_to_roman(text: str) -> str:
    def replace(m):
        pre, digits = m.group(1), m.group(2)
        n = int(digits)
        if 1 <= n <= 3999:
            sep = ' ' if pre else ''
            return pre + sep + to_roman(n)
        return m.group()
    return re.sub(r'(?<![.\w])(\b[a-zA-Z]?\b)?(\d+)(?!\.\d)(?!\w)', replace, text)

def roman_to_arabic(text: str) -> str:
    pattern = r'\b(?=[MDCLXVI]+\b)M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})\b'
    def replace(m):
        val = from_roman(m.group(0).upper())
        return str(val) if val > 0 else m.group(0)
    return re.sub(pattern, replace, text, flags=re.IGNORECASE)

def _normalize_search_text(text: str) -> str:
    text = text.replace('_', ' ')
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    text = re.sub(r'[^a-zA-Z0-9]+', ' ', text.lower())
    return re.sub(r'\s+', ' ', text).strip()

def _similarity(query: str, title: str) -> float:
    q, t = _normalize_search_text(query), _normalize_search_text(title)
    if not q or not t: return 0.0
    q_tokens, t_tokens = set(q.split()), set(t.split())
    token_score = len(q_tokens & t_tokens) / max(len(q_tokens), 1)
    seq_score = difflib.SequenceMatcher(None, q, t).ratio()
    prefix_bonus = 1.0 if t.startswith(q) else 0.0
    return (0.55 * token_score) + (0.35 * seq_score) + (0.10 * prefix_bonus)

def get_json(url, fatal=True):
    def bail(*lines):
        if fatal:
            for line in lines:
                print(line)
            sys.exit(1)
        return None

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'NASA-CLI-Archive-Tool/1.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode('utf-8'))

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        elif e.code == 429:
            return bail(f"\n{Color.RED}Rate limit exceeded.{Color.END}")
        elif 500 <= e.code < 600:
            return bail(f"\n{Color.RED}NASA Server Error ({e.code}).{Color.END}")
        else:
            return bail(f"\n{Color.RED}HTTP Error {e.code}:{Color.END} {e.reason}")

    except urllib.error.URLError as e:
        if isinstance(e.reason, socket.timeout):
            return bail(f"\n{Color.RED}Network Timeout.{Color.END}")
        return bail(f"\n{Color.RED}Connection Refused.{Color.END}")

    except json.JSONDecodeError as e:
        return bail(f"\n{Color.RED}Data Corruption.{Color.END} invalid JSON from API",
                    f"Details: {str(e)}")

    except Exception as e:
        return bail(f"\n{Color.RED}Unexpected Failure.{Color.END} Type: {type(e).__name__}",
                    f"Message: {str(e)}")

def _run_search(query: str, pages: int, media: str = 'image') -> dict:
    albums = {}
    for page in range(1, pages + 1):
        params = urllib.parse.urlencode({'q': query, 'media_type': media, 'page_size': 100, 'page': page})
        data = get_json(f'{API_ROOT}/search?{params}')
        if not data: break
        coll = data['collection']
        items = coll.get('items', [])
        if not items: break
        for item in items:
            for block in item.get('data', []):
                for name in (block.get('album') or []):
                    albums.setdefault(name, set())
                    title = block.get('title', '')
                    if title and len(albums[name]) < 10:
                        albums[name].add(title)
        if not any(l.get('rel') == 'next' for l in coll.get('links', [])):
            break
    return albums

def cmd_search(args):
    print(f"{Color.RED}{NASA_WORM_LOGO}{Color.END}")
    media = getattr(args, 'media', 'all')
    video_quality = getattr(args, 'video_quality', DEFAULT_VIDEO_QUALITY)
    media_param = MEDIA_PARAM[media]

    def get_variants(q):
        variants = [q]
        if ' ' in q: variants.append(q.replace(' ', '_'))
        if '_' in q: variants.append(q.replace('_', ' '))
        return list(dict.fromkeys(variants))

    merged_albums = {}
    attempted = set()

    def merge_results(queries):
        for q in queries:
            if q in attempted: continue
            attempted.add(q)
            res = _run_search(q, args.pages, media_param)
            for album_name, titles in res.items():
                merged_albums.setdefault(album_name, set()).update(titles)

    with Spinner(f"Searching {API_ROOT} for '{args.query}'..."):
        primary_queries = get_variants(args.query)
        merge_results(primary_queries)

        if not merged_albums:
            secondary = []
            for q in primary_queries:
                r, a = arabic_to_roman(q), roman_to_arabic(q)
                if r != q: secondary.append(r)
                if a != q: secondary.append(a)
            merge_results(list(dict.fromkeys(secondary)))

    if not merged_albums:
        print(f"{Color.RED}No albums found for '{args.query}'.{Color.END}")
        return

    ranked_names = sorted(merged_albums.keys(), key=lambda n: _similarity(args.query, n.replace('_', ' ')), reverse=True)
    display_names = ranked_names[:args.limit]

    print(f"{Color.BOLD}Found {len(merged_albums)} albums. Matches for '{args.query}':{Color.END}\n")
    for i, name in enumerate(display_names, 1):
        print(f" {Color.YELLOW}[{i}]{Color.END} {Color.BOLD}{name}{Color.END}")
        best_titles = sorted(list(merged_albums[name]), key=lambda t: _similarity(args.query, t), reverse=True)[:1]
        for t in best_titles:
            print(f"     {Color.CYAN}→{Color.END} e.g. \"{t}\"")

    print(f"\n{Color.PURPLE}Selection:{Color.END}")
    choice = input("Enter number to download (or Enter to quit): ").strip()
    
    if choice.isdigit() and 1 <= int(choice) <= len(display_names):
        selected_album = display_names[int(choice)-1]
        download_args = argparse.Namespace(album=selected_album, output=None,
                                           media=media, video_quality=video_quality)
        cmd_download(download_args)

def _download_url(url: str, dest: str) -> bool:
    tmp = dest + '.tmp'
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'NASA-CLI-Archive-Tool/1.0'})
            with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, 'wb') as f:
                while chunk := resp.read(65536):
                    f.write(chunk)
            os.replace(tmp, dest)
            return True
        except urllib.error.HTTPError:
            if os.path.exists(tmp): os.remove(tmp)
            return False
        except (urllib.error.URLError, socket.timeout, OSError):
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BASE * (2 ** attempt) + random.uniform(0, 0.5)
                time.sleep(wait)
    if os.path.exists(tmp): os.remove(tmp)
    return False

def _item_field(item, key, default=None):
    data = item.get('data') or [{}]
    return data[0].get(key, default)

def _asset_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    return ASSET_BASE + urllib.parse.quote(urllib.parse.unquote(path))

def _rank_video_urls(urls, quality: str = DEFAULT_VIDEO_QUALITY):
    idx = VIDEO_QUALITIES.index(quality)
    order = VIDEO_QUALITIES[idx:] + VIDEO_QUALITIES[:idx][::-1]

    ranked = []
    for url in urls:
        path = urllib.parse.urlparse(url).path
        stem, ext = os.path.splitext(path.lower())
        if ext not in VIDEO_EXTS:
            continue
        tag = stem.rsplit('~', 1)[-1] if '~' in stem else None
        q_rank = order.index(tag) if tag in order else len(order)
        ranked.append((q_rank, VIDEO_EXTS.index(ext), url))
    return [url for _, _, url in sorted(ranked)]

def _video_asset_urls(item):
    href = item.get('href')
    data = get_json(_asset_url(href), fatal=False) if href else None

    if data is None:
        nasa_id = _item_field(item, 'nasa_id')
        if nasa_id:
            data = get_json(f'{API_ROOT}/asset/{urllib.parse.quote(nasa_id, safe="")}', fatal=False)

    if isinstance(data, dict):
        data = [i.get('href', '') for i in data.get('collection', {}).get('items', [])]
    return [u for u in (data or []) if isinstance(u, str)]

def _process_image(item, out_dir):
    preview_links = [l for l in item.get('links', [])
                     if l.get('rel') == 'preview' and '/image/' in l.get('href', '')]

    if not preview_links:
        nasa_id = _item_field(item, 'nasa_id', 'unknown')
        return 'missing', None, nasa_id, 'image'

    href = preview_links[0]['href']
    base_name = os.path.basename(href).replace('~thumb', '~orig').replace(' ', '_')
    fname = os.path.join(out_dir, base_name)

    if os.path.exists(fname):
        parsed = urllib.parse.urlparse(href.replace('~thumb', '~orig'))
        return 'sk', ASSET_BASE + urllib.parse.quote(parsed.path), base_name, 'image'

    for suffix in SIZE_ORDER:
        parsed = urllib.parse.urlparse(href.replace('~thumb', suffix))
        url = ASSET_BASE + urllib.parse.quote(parsed.path)
        if _download_url(url, fname):
            return 'dl', url, base_name, 'image'

    return 'fail', None, base_name, 'image'

def _process_video(item, out_dir, quality):
    nasa_id = _item_field(item, 'nasa_id', 'unknown')
    candidates = _rank_video_urls(_video_asset_urls(item), quality)

    if not candidates:
        return 'missing', None, nasa_id, 'video'

    def local_name(u):
        return urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(u).path)).replace(' ', '_')

    best = candidates[0]
    if os.path.exists(os.path.join(out_dir, local_name(best))):
        return 'sk', _asset_url(best), local_name(best), 'video'

    for cand in candidates:
        url, name = _asset_url(cand), local_name(cand)
        if _download_url(url, os.path.join(out_dir, name)):
            return 'dl', url, name, 'video'

    return 'fail', None, local_name(best), 'video'

def _process_item(item, out_dir, video_quality=DEFAULT_VIDEO_QUALITY):
    if _item_field(item, 'media_type', 'image') == 'video':
        return _process_video(item, out_dir, video_quality)
    return _process_image(item, out_dir)

def download_items(items, out_dir, video_quality=DEFAULT_VIDEO_QUALITY,
                   workers=max(1, (os.cpu_count() or 4) // 2)):
    total = len(items)
    counters = {'dl': 0, 'sk': 0, 'fail': 0, 'missing': 0, 'done': 0}
    downloaded = {'image': 0, 'video': 0}
    collected_urls = {'image': [], 'video': []}
    lock = threading.Lock()

    WinProgress.start()

    def _on_complete(status, url, name, kind):
        counters[status] += 1
        counters['done'] += 1
        if status == 'dl':
            downloaded[kind] += 1
        if url:
            collected_urls[kind].append(url)

        if status == 'missing':
            print(f"\n  {Color.YELLOW}Skipped{Color.END} {name} (no {kind} file found)")

        pct = int((counters['done'] / total) * 100)
        WinProgress.set(pct)
        non_sk = total - counters['sk']
        processed = counters['dl'] + counters['fail'] + counters['missing']

        term_width = shutil.get_terminal_size((80, 20)).columns
        visible_prefix = f"[{pct}% {processed}/{non_sk}] Processing: "
        avail_space = term_width - len(visible_prefix) - 1
        
        if len(name) > avail_space:
            if avail_space > 3:
                display_name = name[:avail_space - 3] + "..."
            else:
                display_name = "." * max(0, avail_space)
        else:
            display_name = name

        progress_text = (
            f"{Color.YELLOW}[{pct}% {processed}/{non_sk}]{Color.END} "
            f"Processing: {display_name}\033[K"
        )
        sys.stdout.write("\r" + progress_text)
        sys.stdout.flush()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_item, item, out_dir, video_quality): item for item in items}
        for future in as_completed(futures):
            status, url, name, kind = future.result()
            with lock:
                _on_complete(status, url, name, kind)

    sys.stdout.write('\r' + ' ' * (shutil.get_terminal_size((80, 20)).columns - 1) + '\r')
    WinProgress.done()

    dl, sk, fail, missing = counters['dl'], counters['sk'], counters['fail'], counters['missing']
    print(f"  {Color.CYAN}Downloaded:{Color.END} {downloaded['image']} image(s), "
          f"{downloaded['video']} video(s) ({dl}/{total})")

    for kind, filename in (('image', 'images.txt'), ('video', 'videos.txt')):
        if collected_urls[kind]:
            urls_file = os.path.join(out_dir, filename)
            with open(urls_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(collected_urls[kind]) + '\n')
            print(f"  {Color.CYAN}{kind.capitalize()} URLs saved to:{Color.END} {urls_file}")

    return dl, sk, fail, missing

def cmd_download(args):
    media = getattr(args, 'media', 'all')
    video_quality = getattr(args, 'video_quality', DEFAULT_VIDEO_QUALITY)

    out_dir = args.output or args.album.replace(' ', '_')
    os.makedirs(out_dir, exist_ok=True)
    
    print(f"\n{Color.GREEN}↓ Initializing download for:{Color.END} {Color.BOLD}{args.album}{Color.END}")
    
    encoded = urllib.parse.quote(args.album, safe='')
    base_url = f'{API_ROOT}/album/{encoded}'

    with Spinner("Fetching album metadata..."):
        first = get_json(f'{base_url}?page_size=100&page=1')

    if not first or not first['collection'].get('metadata', {}).get('total_hits'):
        print(f"{Color.RED}Album empty or not found.{Color.END}")
        return

    all_items = []
    current_coll = first['collection']
    page = 1

    with Spinner("Fetching all pages..."):
        while True:
            all_items.extend(current_coll.get('items', []))
            if not any(l.get('rel') == 'next' for l in current_coll.get('links', [])):
                break
            page += 1
            current_coll = get_json(f'{base_url}?page_size=100&page={page}')['collection']

    wanted = MEDIA_CHOICES[media]
    items = [i for i in all_items if _item_field(i, 'media_type', 'image') in wanted]
    n_video = sum(1 for i in items if _item_field(i, 'media_type', 'image') == 'video')
    n_other = len(all_items) - len(items)

    print(f"  {Color.CYAN}Total items:{Color.END} {len(items)} "
          f"({len(items) - n_video} images, {n_video} videos)")
    if n_other:
        print(f"  {Color.YELLOW}Ignoring{Color.END} {n_other} item(s) not matching --media {media}")

    if not items:
        print(f"{Color.RED}Nothing to download.{Color.END}")
        return

    dl, sk, fail, missing = download_items(items, out_dir, video_quality)

    print(f"\n{Color.BOLD}Summary:{Color.END}")
    print(f"  {Color.GREEN}New:{Color.END} {dl} | {Color.CYAN}Existing:{Color.END} {sk} | {Color.RED}Failed:{Color.END} {fail} | {Color.YELLOW}Missing:{Color.END} {missing}\n")

def _add_media_args(p):
    p.add_argument('-m', '--media', choices=list(MEDIA_CHOICES), default='all',
                   help='What to fetch: images, videos, or all (default: all)')
    p.add_argument('-q', '--video-quality', choices=VIDEO_QUALITIES, default=DEFAULT_VIDEO_QUALITY,
                   help=f'Preferred video quality, will fall back to nearest available '
                        f'(default: {DEFAULT_VIDEO_QUALITY})')

def main():
    parser = argparse.ArgumentParser(description='bulk-download images and videos from NASA Image Library')
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('search')
    p.add_argument('query')
    p.add_argument('-l', '--limit', type=int, default=10, help='Max albums to show (default: 10)')
    p.add_argument('--pages', type=int, default=5, help='Search depth (default: 5)')
    _add_media_args(p)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser('download')
    p.add_argument('album')
    p.add_argument('-o', '--output', default=None)
    _add_media_args(p)
    p.set_defaults(func=cmd_download)

    args = parser.parse_args()
    args.func(args)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        WinProgress.done()
        print(f"\n{Color.RED}Exited by user.{Color.END}")
        sys.exit(0)