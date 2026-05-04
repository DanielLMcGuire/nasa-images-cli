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

API_ROOT   = 'https://images-api.nasa.gov'
ASSET_BASE = 'https://images-assets.nasa.gov'
SIZE_ORDER = ['~orig', '~large', '~medium', '~small', '~thumb']

_ROMAN_MAP = [
    (1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'),
    (100,  'C'), (90,  'XC'), (50,  'L'), (40,  'XL'),
    (10,   'X'), (9,   'IX'), (5,   'V'), (4,   'IV'), (1, 'I'),
]

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
        if i + 1 < len(s) and roman_val[s[i]] < roman_val[s[i+1]]:
            res -= roman_val[s[i]]
        else:
            res += roman_val[s[i]]
    return res

def arabic_to_roman(text: str) -> str:
    def replace(m):
        pre, digits = m.group(1), m.group(2)
        n = int(digits)
        if 1 <= n <= 3999:
            sep = ' ' if pre else ''
            return pre + sep + to_roman(n)
        return m.group()
    return re.sub(r'([a-zA-Z]?)(\d+)', replace, text)

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
    q = _normalize_search_text(query)
    t = _normalize_search_text(title)

    if not q or not t:
        return 0.0

    q_tokens = set(q.split())
    t_tokens = set(t.split())

    token_score = len(q_tokens & t_tokens) / max(len(q_tokens), 1)
    seq_score = difflib.SequenceMatcher(None, q, t).ratio()
    prefix_bonus = 1.0 if t.startswith(q) else 0.0

    return (0.55 * token_score) + (0.35 * seq_score) + (0.10 * prefix_bonus)

def get_json(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise SystemExit(f'HTTP {e.code} — {url}')
    except urllib.error.URLError as e:
        raise SystemExit(f'Network error — {url} — {e.reason}')
    except json.JSONDecodeError:
        raise SystemExit(f'Invalid JSON response — {url}')

def _run_search(query: str, pages: int) -> dict:
    albums = {}
    for page in range(1, pages + 1):
        params = urllib.parse.urlencode({
            'q': query, 'media_type': 'image',
            'page_size': 100, 'page': page,
        })
        data = get_json(f'{API_ROOT}/search?{params}')
        if not data: break
        
        coll  = data['collection']
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
    def get_variants(q):
        variants = [q]
        if ' ' in q:
            variants.append(q.replace(' ', '_'))
        if '_' in q:
            variants.append(q.replace('_', ' '))
        return list(dict.fromkeys(variants))

    def get_numeral_variants(q):
        variants = []
        r = arabic_to_roman(q)
        if r != q:
            variants.append(r)
        a = roman_to_arabic(q)
        if a != q:
            variants.append(a)
        return variants

    merged_albums = {}
    attempted = set()

    def merge_results(queries):
        for q in queries:
            if q in attempted:
                continue
            attempted.add(q)

            res = _run_search(q, args.pages)
            for album_name, titles in res.items():
                merged_albums.setdefault(album_name, set()).update(titles)

    primary_queries = get_variants(args.query)
    merge_results(primary_queries)

    if not merged_albums:
        secondary_queries = []
        for q in primary_queries:
            secondary_queries.extend(get_numeral_variants(q))

        secondary_queries = list(dict.fromkeys(secondary_queries))
        if secondary_queries:
            print(f'No results for "{args.query}" — trying numeral variations...')
            merge_results(secondary_queries)

    if not merged_albums:
        print(f'No albums found for "{args.query}" or its variations.')
        return

    ranked_names = sorted(
        merged_albums.keys(),
        key=lambda n: _similarity(args.query, n.replace('_', ' ')),
        reverse=True
    )

    display_names = ranked_names[:args.limit]

    print(f'Found {len(merged_albums)} album(s). Showing top {len(display_names)} matches for "{args.query}":\n')
    for name in display_names:
        print(f'  {name}')
        best_titles = sorted(
            list(merged_albums[name]),
            key=lambda t: _similarity(args.query, t),
            reverse=True
        )[:2]
        for t in best_titles:
            print(f'      e.g. "{t}"')

    best_match = ranked_names[0]
    print(f'\nDownload with:\n  python {sys.argv[0]} download "{best_match}"')

def download_items(items, out_dir):
    dl = sk = fail = missing = 0

    for item in items:
        preview_links = [
            link for link in item.get('links', [])
            if link.get('rel') == 'preview' and '/image/' in link.get('href', '')
        ]

        if not preview_links:
            missing += 1
            continue

        href = preview_links[0]['href']
        fname = os.path.join(
            out_dir,
            os.path.basename(href).replace('~thumb', '~orig').replace(' ', '_')
        )

        if os.path.exists(fname):
            sk += 1
            continue

        downloaded = False
        for suffix in SIZE_ORDER:
            path = urllib.parse.urlparse(href.replace('~thumb', suffix)).path
            url = ASSET_BASE + urllib.parse.quote(path)

            try:
                urllib.request.urlretrieve(url, fname)
                print(f'  OK ({suffix})  {os.path.basename(fname)}')
                dl += 1
                downloaded = True
                break
            except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                continue

        if not downloaded:
            print(f'  FAIL  {os.path.basename(fname)}', file=sys.stderr)
            fail += 1

    return dl, sk, fail, missing

def cmd_download(args):
    out_dir = args.output or args.album.replace(' ', '_')
    os.makedirs(out_dir, exist_ok=True)
    encoded = urllib.parse.quote(args.album, safe='')
    base_url = f'{API_ROOT}/album/{encoded}'

    first = get_json(f'{base_url}?page_size=100&page=1')
    if not first:
        print(f'Album "{args.album}" not found.')
        sys.exit(1)

    coll = first['collection']
    total_hits = coll.get('metadata', {}).get('total_hits', 0)
    if not total_hits:
        print(f'Album "{args.album}" is empty.')
        sys.exit(1)

    total_pages = (total_hits + 99) // 100
    print(f'Album      : {args.album}')
    print(f'Output dir : {out_dir}')
    print(f'Total items: {total_hits}  ({total_pages} page(s))')
    print()

    tdl = tsk = tfail = tmissing = 0

    dl, sk, fail, missing = download_items(coll.get('items', []), out_dir)
    tdl += dl
    tsk += sk
    tfail += fail
    tmissing += missing

    current_coll = coll
    page = 2
    while any(l.get('rel') == 'next' for l in current_coll.get('links', [])):
        data = get_json(f'{base_url}?page_size=100&page={page}')
        if not data:
            break

        current_coll = data['collection']
        dl, sk, fail, missing = download_items(current_coll.get('items', []), out_dir)
        tdl += dl
        tsk += sk
        tfail += fail
        tmissing += missing
        page += 1

    print(f'\ndownloaded: {tdl} skipped: {tsk} failed: {tfail} no_preview: {tmissing}')

def main():
    parser = argparse.ArgumentParser(description='bulk-download images from NASA Image Library')
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('search')
    p.add_argument('query')
    p.add_argument('-l', '--limit', type=int, default=10, help='Max albums to show (default: 10)')
    p.add_argument('--pages', type=int, default=5, help='Search depth (default: 5)')
    p.set_defaults(func=cmd_search)

    p = sub.add_parser('download')
    p.add_argument('album')
    p.add_argument('-o', '--output', default=None)
    p.set_defaults(func=cmd_download)

    args = parser.parse_args()
    args.func(args)

if __name__ == '__main__':
    main()