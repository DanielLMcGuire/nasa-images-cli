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

_TO_ROMAN = [
    (1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'),
    (100,  'C'), (90,  'XC'), (50,  'L'), (40,  'XL'),
    (10,   'X'), (9,   'IX'), (5,   'V'), (4,   'IV'), (1, 'I'),
]

def to_roman(n: int) -> str:
    result = ''
    for value, numeral in _TO_ROMAN:
        while n >= value:
            result += numeral
            n -= value
    return result

def arabic_to_roman(text: str) -> str:
    found = False
    def replace(m):
        nonlocal found
        pre    = m.group(1)
        digits = m.group(2)
        n = int(digits)
        if 1 <= n <= 3999:
            found = True
            sep = ' ' if pre else ''
            return pre + sep + to_roman(n)
        return m.group()
    converted = re.sub(r'([a-zA-Z]?)(\d+)', replace, text)
    return converted if found else None

def _similarity(query: str, title: str) -> float:
    return difflib.SequenceMatcher(None, query.lower(), title.lower()).ratio()

def get_json(url):
    try:
        with urllib.request.urlopen(url) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f'HTTP {e.code} — {url}')

def _run_search(query: str, pages: int) -> dict:
    albums = {}
    for page in range(1, pages + 1):
        params = urllib.parse.urlencode({
            'q': query, 'media_type': 'image',
            'page_size': 100, 'page': page,
        })
        data  = get_json(f'{API_ROOT}/search?{params}')
        coll  = data['collection']
        items = coll.get('items', [])
        if not items:
            break

        for item in items:
            for block in item.get('data', []):
                for name in (block.get('album') or []):
                    albums.setdefault(name, [])
                    title = block.get('title', '')

                    if title and title not in albums[name] and len(albums[name]) < 10:
                        albums[name].append(title)

        if not any(l.get('rel') == 'next' for l in coll.get('links', [])):
            break

    return albums


def cmd_search(args):
    albums = _run_search(args.query, args.pages)
    roman_query = None
    if not albums:
        roman_query = arabic_to_roman(args.query)
        if roman_query and roman_query != args.query:
            print(f'No results for "{args.query}" — retrying as "{roman_query}" ...')
            albums = _run_search(roman_query, args.pages)

    if not albums:
        print('No albums found in search results. Try a different keyword.')
        return

    effective_query = roman_query if roman_query and albums else args.query

    print(f'Found {len(albums)} album name(s) for "{effective_query}":\n')
    for name in sorted(albums):
        print(f'  {name}')
        best = sorted(
            albums[name],
            key=lambda t: _similarity(effective_query, t),
            reverse=True,
        )[:2]
        for t in best:
            print(f'      e.g. "{t}"')

    print()
    print('Download with:')
    best_album = max(albums.keys(), key=lambda name: _similarity(effective_query, name.replace('_', ' ')))
    print(f'  python dl.py download "{best_album}"')


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

    first      = get_json(f'{base_url}?page_size=100&page=1')
    coll       = first['collection']
    total_hits = coll.get('metadata', {}).get('total_hits', 0)

    if not total_hits:
        print(f'Album "{args.album}" not found or empty.')
        print('Album names are case-sensitive. Use the search subcommand to find valid names.')
        sys.exit(1)

    total_pages = (total_hits + 99) // 100
    print(f'Album      : {args.album}')
    print(f'Output dir : {out_dir}')
    print(f'Total items: {total_hits}  ({total_pages} page(s))')
    print()

    tdl = tsk = tfail = 0

    dl, sk, fail = download_items(coll.get('items', []), out_dir)
    tdl += dl; tsk += sk; tfail += fail

    page = 2
    while True:
        data  = get_json(f'{base_url}?page_size=100&page={page}')
        coll  = data['collection']
        items = coll.get('items', [])
        if not items:
            break
        dl, sk, fail = download_items(items, out_dir)
        tdl += dl; tsk += sk; tfail += fail
        if not any(l.get('rel') == 'next' for l in coll.get('links', [])):
            break
        page += 1

    print(f'\nDone — downloaded: {tdl}  skipped: {tsk}  failed: {tfail}')

def main():
    parser = argparse.ArgumentParser(
        description='Download images from the NASA Image and Video Library.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('search', help='Find album names by keyword.')
    p.add_argument('query', help='Search keyword(s), e.g. "artemis" or "apollo 11"')
    p.add_argument('--pages', type=int, default=5,
                   help='Search result pages to scan (default 5 x 100 results)')
    p.set_defaults(func=cmd_search)

    p = sub.add_parser('download', help='Download all images in a named album.')
    p.add_argument('album', help='Album name (case-sensitive), e.g. Artemis_II')
    p.add_argument('-o', '--output', default=None, help='Output directory')
    p.set_defaults(func=cmd_download)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()