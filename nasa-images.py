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

def _similarity(query: str, title: str) -> float:
    return difflib.SequenceMatcher(None, query.lower(), title.lower()).ratio()

def get_json(url):
    try:
        with urllib.request.urlopen(url) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise SystemExit(f'HTTP {e.code} — {url}')

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
        vars = [q]
        if ' ' in q: vars.append(q.replace(' ', '_'))
        if '_' in q: vars.append(q.replace('_', ' '))
        return list(dict.fromkeys(vars))

    primary_queries = get_variants(args.query)
    merged_albums = {}

    for q in primary_queries:
        res = _run_search(q, args.pages)
        for album_name, titles in res.items():
            merged_albums.setdefault(album_name, set()).update(titles)

    if not merged_albums:
        secondary_queries = []
        for q in primary_queries:
            r = arabic_to_roman(q)
            if r != q: secondary_queries.append(r)
            a = roman_to_arabic(q)
            if a != q: secondary_queries.append(a)
        
        secondary_queries = list(dict.fromkeys(secondary_queries))
        if secondary_queries:
            print(f'No results for "{args.query}" — trying numeral variations...')
            for q in secondary_queries:
                res = _run_search(q, args.pages)
                for album_name, titles in res.items():
                    merged_albums.setdefault(album_name, set()).update(titles)

    if not merged_albums:
        print(f'No albums found for "{args.query}" or its variations.')
        return

    print(f'Found {len(merged_albums)} album(s) for "{args.query}":\n')
    for name in sorted(merged_albums):
        print(f'  {name}')
        best = sorted(
            list(merged_albums[name]),
            key=lambda t: _similarity(args.query, t),
            reverse=True,
        )[:2]
        for t in best:
            print(f'      e.g. "{t}"')

    best_album = max(merged_albums.keys(), key=lambda n: _similarity(args.query, n.replace('_', ' ')))
    print(f'\nDownload with:\n  python {sys.argv[0]} download "{best_album}"')


def download_items(items, out_dir):
    dl = sk = fail = 0
    for item in items:
        for link in item.get('links', []):
            if link.get('rel') != 'preview' or '/image/' not in link.get('href', ''):
                continue
            href  = link['href']
            fname = os.path.join(
                out_dir,
                os.path.basename(href).replace('~thumb', '~orig').replace(' ', '_'),
            )
            if os.path.exists(fname):
                sk += 1
                continue
            for suffix in SIZE_ORDER:
                path = urllib.parse.urlparse(href.replace('~thumb', suffix)).path
                url  = ASSET_BASE + urllib.parse.quote(path)
                try:
                    urllib.request.urlretrieve(url, fname)
                    print(f'  OK ({suffix})  {os.path.basename(fname)}')
                    dl += 1
                    break
                except Exception:
                    continue
            else:
                print(f'  FAIL  {os.path.basename(fname)}', file=sys.stderr)
                fail += 1
    return dl, sk, fail


def cmd_download(args):
    out_dir  = args.output or args.album.replace(' ', '_')
    os.makedirs(out_dir, exist_ok=True)

    encoded  = urllib.parse.quote(args.album, safe='')
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

    tdl = tsk = tfail = 0
    
    dl, sk, fail = download_items(coll.get('items', []), out_dir)
    tdl += dl; tsk += sk; tfail += fail

    current_coll = coll
    page = 2
    while any(l.get('rel') == 'next' for l in current_coll.get('links', [])):
        data = get_json(f'{base_url}?page_size=100&page={page}')
        if not data: break
        current_coll = data['collection']
        items = current_coll.get('items', [])
        if not items: break
        dl, sk, fail = download_items(items, out_dir)
        tdl += dl; tsk += sk; tfail += fail
        page += 1

    print(f'\nDone — downloaded: {tdl}  skipped: {tsk}  failed: {tfail}')

def main():
    parser = argparse.ArgumentParser(description='bulk-download images from NASA Image Library')
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('search')
    p.add_argument('query')
    p.add_argument('--pages', type=int, default=5)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser('download')
    p.add_argument('album')
    p.add_argument('-o', '--output', default=None)
    p.set_defaults(func=cmd_download)

    args = parser.parse_args()
    args.func(args)

if __name__ == '__main__':
    main()