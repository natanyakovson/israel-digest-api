#!/usr/bin/env python3
"""
Scrape 7 public Telegram channels for posts since a given timestamp.
Includes built-in keyword-based deduplication (no LLM needed).
Usage: python3 scrape_telegram.py [--since ISO_TIMESTAMP]
Output: JSON to stdout with deduplicated posts.
"""
import json, sys, re, urllib.request, argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo

ISR_TZ = ZoneInfo("Asia/Jerusalem")

CHANNELS_RU = [
    'intellinews_russian',
    'markkot56',
    'INews_Israel',
    'EzraMorYoutubeShow',
    'voiceofisrael',
    'voinasordoy',
    'isr_live',
    'israel_truexa',
    'Godunov2020',
    'western_wall',
    'polet_israel',
    'cozy_shekel',
    'Trueisrael'
]

CHANNELS_HE = [
    'GLOBAL_Telegram_MOKED',
    'abualiexpress',
    'yosephhaddad',
]

ALL_CHANNELS = CHANNELS_RU + CHANNELS_HE

DISPLAY_NAMES = {
    'intellinews_russian': 'ИнтеллиНьюз',
    'markkot56': 'Марк Котляр',
    'INews_Israel': 'Новости Израиля«НИ»',
    'EzraMorYoutubeShow': 'Эзра Мор',
    'voiceofisrael': 'Голос Израиля',
    'voinasordoy': 'Война с Ордой',
    'isr_live': 'ИЗРАИЛЬ 🇮🇱 LIVE',
    'israel_truexa': 'Israel⚡️Труха',
    'Godunov2020': 'Борис и Лора',
    'western_wall': 'Стена Плача',
    'Trueisrael': 'Израильские трудовыебудни',
    'polet_israel': 'Полет Израиль',
    'cozy_shekel': 'Уютный шекель',
    'GLOBAL_Telegram_MOKED': 'GLOBAL_Telegram',
    'abualiexpress': 'Abu Ali',
    'yosephhaddad': 'Yoseph Haddad',
}

# ── helpers ──────────────────────────────────────────────────────────
_STRIP_RE = re.compile(r'[^\w\s]', re.UNICODE)
_STOP = frozenset('и в на по с к о а не из за для что как это но от до уже все был'.split())

def _keywords(text: str) -> set:
    """Extract meaningful keywords from text for dedup comparison."""
    words = _STRIP_RE.sub(' ', text.lower()).split()
    return {w for w in words if len(w) > 3 and w not in _STOP}

def _similarity(kw_a: set, kw_b: set) -> float:
    if not kw_a or not kw_b:
        return 0.0
    intersection = kw_a & kw_b
    return len(intersection) / min(len(kw_a), len(kw_b))

# ── scraper ──────────────────────────────────────────────────────────
def scrape_channel(channel_name: str, since_dt=None):
    url = f"https://t.me/s/{channel_name}"
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f"ERR {channel_name}: {e}", file=sys.stderr)
        return []

    msg_re = re.compile(
        r'data-post="([^"]+)".*?'
        r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>.*?'
        r'<time[^>]*datetime="([^"]+)"',
        re.DOTALL,
    )

    posts = []
    for m in msg_re.finditer(html):
        post_id = m.group(1)
        raw = m.group(2)
        ts_str = m.group(3)

        # clean html
        txt = re.sub(r'<br\s*/?>', '\n', raw)
        txt = re.sub(r'<[^>]+>', '', txt)
        for esc, ch in [('&amp;','&'),('&lt;','<'),('&gt;','>'),('&quot;','"'),('&#39;',"'"),('&#33;','!')]:
            txt = txt.replace(esc, ch)
        txt = re.sub(r'\n{3,}', '\n\n', txt).strip()
        if not txt:
            continue

        try:
            dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        except Exception as e:
            print(f"ERROR parsing time '{ts_str}': {e}", file=sys.stderr)
            continue

        if since_dt and dt <= since_dt:
            continue

        posts.append({
            'post_id': post_id,
            'link': f"https://t.me/{post_id}",
            'text': txt[:1000],
            'time_utc': dt.isoformat(),
            'time_israel': dt.astimezone(ISR_TZ).strftime('%H:%M'),
            'channel': channel_name,
            'display_name': DISPLAY_NAMES.get(channel_name, channel_name),
            'is_hebrew': channel_name in CHANNELS_HE,
        })
    return posts

# ── deduplication ────────────────────────────────────────────────────
def dedup_posts(posts: list, threshold: float = 0.55) -> list:
    """
    Group similar posts from DIFFERENT channels.

    Different posts from the same Telegram channel are never merged.
    The link of the selected best text is always placed first in all_sources.
    """
    if not posts:
        return []

    kw_cache = [(p, _keywords(p['text'])) for p in posts]
    used = [False] * len(kw_cache)
    groups = []

    for i, (pi, kwi) in enumerate(kw_cache):
        if used[i]:
            continue

        group = [i]
        used[i] = True
        channels_in_group = {pi['channel']}

        for j in range(i + 1, len(kw_cache)):
            if used[j]:
                continue

            pj, kwj = kw_cache[j]

            # Никогда не объединяем разные сообщения одного канала
            if pj['channel'] in channels_in_group:
                continue

            if _similarity(kwi, kwj) >= threshold:
                group.append(j)
                used[j] = True
                channels_in_group.add(pj['channel'])

        groups.append(group)

    deduped = []

    for grp in groups:
        items = [kw_cache[idx][0] for idx in grp]

        # Берём наиболее содержательный текст
        best = max(items, key=lambda p: len(p['text']))

        # Создаём копию, чтобы не менять исходный объект
        result = best.copy()

        # Ссылка именно выбранного текста всегда идёт первой
        sources = [{
            'link': best['link'],
            'display_name': best['display_name'],
            'channel': best['channel'],
            'is_hebrew': best['is_hebrew'],
        }]

        seen_links = {best['link']}
        seen_channels = {best['channel']}

        # Добавляем максимум одну ссылку от каждого другого канала
        for item in items:
            if item['link'] in seen_links:
                continue

            if item['channel'] in seen_channels:
                continue

            sources.append({
                'link': item['link'],
                'display_name': item['display_name'],
                'channel': item['channel'],
                'is_hebrew': item['is_hebrew'],
            })

            seen_links.add(item['link'])
            seen_channels.add(item['channel'])

        result['all_sources'] = sources
        deduped.append(result)

    return deduped
    
# ── main ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--since', default=None,
                        help='ISO timestamp – only collect posts AFTER this moment')
    parser.add_argument('--exclude-ids', default=None,
                        help='Path to JSON file with list of already-processed post_ids')
    args = parser.parse_args()

    since_dt = None
    if args.since:
        try:
            since_dt = datetime.fromisoformat(args.since)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        except Exception as e:
            print(f"ERROR parsing --since '{args.since}': {e}", file=sys.stderr)
    # fallback: last 24 hours if no --since given
    if since_dt is None:
        since_dt = datetime.now(timezone.utc) - timedelta(hours=24)

    exclude_ids = set()
    if args.exclude_ids:
        try:
            with open(args.exclude_ids) as f:
                exclude_ids = set(json.load(f))
        except Exception as e:
            print(f"ERROR loading exclude-ids '{args.exclude_ids}': {e}", file=sys.stderr)

    all_posts = []
    for ch in ALL_CHANNELS:
        posts = scrape_channel(ch, since_dt=since_dt)
        new = [p for p in posts if p['post_id'] not in exclude_ids]
        print(f"{ch}: {len(posts)} raw, {len(new)} new", file=sys.stderr)
        all_posts.extend(new)

    # sort by time
    all_posts.sort(key=lambda p: p.get('time_utc', ''))

    # deduplicate by keyword similarity
    deduped = dedup_posts(all_posts)
    print(f"Total: {len(all_posts)} new → {len(deduped)} after dedup", file=sys.stderr)

    json.dump(deduped, sys.stdout, ensure_ascii=False, indent=2)

if __name__ == '__main__':
    main()
