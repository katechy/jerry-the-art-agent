import feedparser
import requests
import json
import os
import re
import time
from datetime import datetime, timezone

# ============================================================
# CONFIGURAÇÕES
# ============================================================
# A URL do webhook NUNCA fica no código: vem do Secret WEBHOOK_URL do GitHub.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()

# (Opcional) Webhook de um canal só de 3D (Secret WEBHOOK_URL_3D).
# Se não existir, os posts de 3D vão para o canal normal, com a etiqueta 🧊 3D.
WEBHOOK_URL_3D = os.environ.get("WEBHOOK_URL_3D", "").strip()

# Posts de feeds gerais com estas palavras no título/texto são tratados como 3D.
KEYWORDS_3D = ["3d", "blender", "maya", "zbrush", "substance", "vrchat", "vrm",
               "unity avatar", "low poly", "lowpoly", "sculpt", "rigging", "rigger",
               "3d print", "stl", "hard surface", "retopo", "texturing"]

# (Opcional) IDs dos cargos a marcar. Configure como Variables no GitHub:
#   ROLE_ID     -> cargo marcado nos posts de 2D/gerais (ex: @2d-commission-taker)
#   ROLE_ID_3D  -> cargo marcado nos posts de 3D        (ex: @3d-commission-taker)
# Deixe vazio para não marcar ninguém.
ROLE_ID = os.environ.get("ROLE_ID", "").strip()
ROLE_ID_3D = os.environ.get("ROLE_ID_3D", "").strip()

# Quantos posts novos no máximo por execução (evita flood no canal).
MAX_POSTS_PER_RUN = 10

# Quantos IDs já vistos guardar por feed (para não repetir posts).
SEEN_LIMIT = 50

# Palavras que fazem o post ser ignorado (minúsculas). Ex: "nsfw", "unpaid".
BLOCKED_WORDS = ["nsfw", "unpaid", "exposure", "for free", "[for hire]"]

# Reddit bloqueia pedidos sem User-Agent identificável.
HEADERS = {"User-Agent": "linux:jerry-o-agente-de-arte:v2.0 (bot de commissions para Discord)"}

STATE_FILE = "last_posted.json"

# Pausa entre um feed e outro. O Reddit aceita ~10 pedidos por minuto sem login;
# abaixo de uns 6 segundos ele começa a responder "429 Too Many Requests".
PAUSA_ENTRE_FEEDS = 7

FEEDS = [
    {
        "url": "https://www.reddit.com/r/artcommissions/search.rss?q=flair%3APatron&sort=new&restrict_sr=on",
        "name": "🎨 Art Commissions - Patron"
    },
    {
        "url": "https://www.reddit.com/r/HungryArtists/search.rss?q=flair%3AHiring&sort=new&restrict_sr=on",
        "name": "🖌️ HungryArtists - Hiring" 
    },
    {
        "url": "https://www.reddit.com/r/commissions/search.rss?q=flair%3AHiring&sort=new&restrict_sr=on",
        "name": "💼 Commissions - Hiring"
    },
    {
        "url": "https://www.reddit.com/r/artcommission/search.rss?q=flair%3AHiring&sort=new&restrict_sr=on",
        "name": "🎨 ArtCommission - Hiring"
    },
    {
        "url": "https://www.reddit.com/r/ComicBookCollabs/search.rss?q=flair%3APaid&sort=new&restrict_sr=on", 
        "name": "📚 ComicBookCollabs - Paid"
    },
    {
        "url": "https://www.reddit.com/r/starvingartists/search.rss?q=flair%3AHiring&sort=new&restrict_sr=on",
        "name": "🍽️ StarvingArtists - Hiring"
    },
    {
        "url": "https://www.reddit.com/r/hireanartist/search.rss?q=flair%3A%22Hiring%202D%3A%20Other%22&sort=new&restrict_sr=on",
        "name": "👨‍🎨 HireAnArtist - 2D Other"
    },
    {
        "url": "https://www.reddit.com/r/hireanartist/search.rss?q=flair%3A%22Hiring%203D%22&sort=new&restrict_sr=on",
        "name": "🧊 HireAnArtist - 3D (todos os estilos)",
        "canal": "3d"
    },
    {
        "url": "https://www.reddit.com/r/Artistsforhire/search.rss?q=flair%3AHiring&sort=new&restrict_sr=on",
        "name": "🎯 ArtistsForHire - Hiring"
    },
    {
        "url": "https://www.reddit.com/r/gameDevClassifieds/search.rss?q=flair%3A%22PAID%20-%202D%20Art%20%2B%203D%20Modeler%20%7C%20Animation%22&sort=new&restrict_sr=on",
        "name": "🎮 GameDev - 2D/3D & Animation"
    },
    {
        "url": "https://www.reddit.com/r/gameDevClassifieds/search.rss?q=flair%3A%22PAID%20-%203D%20Modeler%20%7C%20Animation%22&sort=new&restrict_sr=on",
        "name": "🧊 GameDev - 3D Modeler & Animation",
        "canal": "3d"
    },
    {
        "url": "https://www.reddit.com/r/gameDevClassifieds/search.rss?q=flair%3A%22PAID%20-%202D%20Art%20%7C%20Animation%22&sort=new&restrict_sr=on",
        "name": "🎨 GameDev - 2D Art & Animation (Paid)"
    },
    {
        "url": "https://www.reddit.com/r/gameDevClassifieds/search.rss?q=flair%3A%22PAID%20-%20Multiple%20Roles%22&sort=new&restrict_sr=on",
        "name": "👥 GameDev - Multiple Roles (Paid)"
    },
    {
        "url": "https://www.reddit.com/r/FurryCommissions/search.rss?q=flair%3A%22SEEKING%20ARTIST%22&sort=new&restrict_sr=on",
        "name": "🦊 FurryCommissions - Seeking Artist"
    },
    # ---------------- 3D (novos) ----------------
    {
        # r/forhire é o maior quadro geral de freelas; aqui só os pedidos de 3D
        "url": "https://www.reddit.com/r/forhire/search.rss?q=flair%3AHiring%20%283D%20OR%20blender%20OR%20maya%20OR%20zbrush%20OR%20rigging%29&sort=new&restrict_sr=on",
        "name": "🧊 ForHire - 3D",
        "canal": "3d",
        "exigir_palavras": KEYWORDS_3D
    },
    {
        "url": "https://www.reddit.com/r/forhire/search.rss?q=flair%3AHiring%20%28vtuber%20OR%20vrchat%20OR%20avatar%29&sort=new&restrict_sr=on",
        # Sem "canal": avatar Live2D é 2D, VRChat/VRM é 3D; o bot decide pelas palavras.
        "name": "🎭 ForHire - Avatar / VTuber / VRChat",
        "exigir_palavras": ["vtuber", "vrchat", "avatar", "live2d", "vrm", "vtube"]
    },
    {
        "url": "https://www.reddit.com/r/gameDevClassifieds/search.rss?q=flair%3APAID%20%283D%20OR%20blender%20OR%20modeler%29&sort=new&restrict_sr=on",
        "name": "🧊 GameDev - Pagos com 3D (busca ampla)",
        "canal": "3d",
        "exigir_palavras": KEYWORDS_3D
    },
    {
        "url": "https://www.reddit.com/r/HungryArtists/search.rss?q=flair%3AHiring%20%283D%20OR%20blender%20OR%20vrchat%29&sort=new&restrict_sr=on",
        "name": "🧊 HungryArtists - 3D",
        "canal": "3d",
        "exigir_palavras": KEYWORDS_3D
    }
]


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    # Compatível com o formato antigo ({url: "um_id"})
    return {k: (v if isinstance(v, list) else [v]) for k, v in data.items()}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


LAST_ERROR = {"msg": ""}


def _wait_from_headers(r, default):
    """Usa o tempo de espera que o próprio Reddit informa, se vier."""
    for h in ("Retry-After", "x-ratelimit-reset"):
        try:
            return min(max(float(r.headers[h]), 1), 90)
        except (KeyError, ValueError):
            pass
    return default


def fetch_feed(url):
    """Baixa o feed com User-Agent próprio e respeita o limite de pedidos do Reddit."""
    LAST_ERROR["msg"] = ""
    for attempt in range(4):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 429:
                wait = _wait_from_headers(r, 20 * (attempt + 1))
                LAST_ERROR["msg"] = f"429 limite do Reddit (esperei {wait:.0f}s)"
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                LAST_ERROR["msg"] = f"HTTP {r.status_code}"
                if r.status_code in (403, 404):
                    return None  # bloqueado / sub não existe: não adianta repetir
                time.sleep(10)
                continue
            return feedparser.parse(r.content)
        except requests.RequestException as e:
            LAST_ERROR["msg"] = str(e)[:80]
            time.sleep(10)
    return None


def clean_html(text):
    """Remove HTML e o rodapé 'submitted by /u/... [link] [comments]' do Reddit."""
    if not text:
        return ""
    text = re.sub(r"(?s)submitted by.*$", "", text)
    text = re.sub(r"<[^<]+?>", " ", text)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:600] + ("…" if len(text) > 600 else "")


def has_required_words(entry, feed_info):
    """Feeds de busca às vezes trazem posts sem relação; confere as palavras aqui também."""
    words = feed_info.get("exigir_palavras")
    if not words:
        return True
    blob = " " + (entry.get("title", "") + " " + entry.get("summary", "")).lower() + " "
    return any(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", blob) for w in words)


def is_blocked(entry):
    blob = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
    return any(w in blob for w in BLOCKED_WORDS)


def is_3d(entry, feed_info):
    if feed_info.get("canal") == "3d":
        return True
    blob = " " + (entry.get("title", "") + " " + entry.get("summary", "")).lower() + " "
    return any(re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", blob) for k in KEYWORDS_3D)


def send_to_discord(embed, webhook, role_id=""):
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    if role_id:
        payload["content"] = f"<@&{role_id}>"
        payload["allowed_mentions"] = {"roles": [role_id]}
    for _ in range(3):
        r = requests.post(webhook, json=payload, timeout=20)
        if r.status_code == 429:  # rate limit do Discord
            time.sleep(float(r.json().get("retry_after", 2)))
            continue
        return r.status_code in (200, 204)
    return False


def build_embed(entry, feed_name, is3d=False):
    author = entry.get("author", "").replace("/u/", "")
    embed = {
        "title": (("🧊 3D · " if is3d else "") + entry.get("title", "Sem título"))[:256],
        "url": entry.get("link"),
        "description": clean_html(entry.get("summary", "")) or "Clique para ver os detalhes…",
        "color": 0x2ECC71 if is3d else 0x5865F2,
        "author": {"name": feed_name},
        "timestamp": entry.get("updated") or entry.get("published")
                     or datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "⚠️ Nunca pague para trabalhar · confira o perfil do cliente antes"},
    }
    if author:
        embed["fields"] = [{"name": "Postado por", "value": f"u/{author}", "inline": True}]
    return embed


def list_flairs(feed_url):
    """Mostra os flairs que existem de verdade no sub (para corrigir buscas vazias)."""
    m = re.search(r"reddit\.com/r/([^/]+)/", feed_url)
    if not m:
        return
    try:
        r = requests.get(f"https://www.reddit.com/r/{m.group(1)}/new.json?limit=100",
                         headers=HEADERS, timeout=20)
        posts = r.json()["data"]["children"]
    except Exception:
        print(f"{'':32}   (não consegui listar os flairs)")
        return
    flairs = sorted({p["data"].get("link_flair_text") or "(sem flair)" for p in posts})
    print(f"{'':32}   flairs que existem em r/{m.group(1)}: {flairs}")


def check_only():
    """Modo teste: mostra quantos posts cada feed retorna, sem enviar nada."""
    print("Testando feeds (nada será enviado)...\n")
    for fi in FEEDS:
        feed = fetch_feed(fi["url"])
        n = len(feed.entries) if feed else -1
        status = f"❌ erro ({LAST_ERROR['msg']})" if n < 0 else ("⚠️ 0 posts (flair/busca errada?)" if n == 0 else f"✅ {n} posts")
        print(f"{status:32} {fi['name']}")
        if feed and feed.entries:
            print(f"{'':32}   último: {feed.entries[0].get('title', '')[:70]}")
        elif feed is not None:
            time.sleep(PAUSA_ENTRE_FEEDS)
            list_flairs(fi["url"])
        time.sleep(PAUSA_ENTRE_FEEDS)


def check_feeds():
    if not WEBHOOK_URL:
        raise SystemExit("❌ WEBHOOK_URL não definido. Crie o Secret WEBHOOK_URL no GitHub.")

    state = load_state()
    to_send = []  # (entry, feed_info)
    # IDs já vistos em QUALQUER feed: o mesmo post em dois feeds só sai uma vez
    all_seen = {i for ids in state.values() for i in ids}

    for feed_info in FEEDS:
        url, name = feed_info["url"], feed_info["name"]
        print(f"Verificando {name}...")
        feed = fetch_feed(url)
        if feed is None:
            print(f"   ❌ {LAST_ERROR['msg']}")
            continue
        if not feed.entries:
            continue

        first_run = url not in state
        seen = state.setdefault(url, [])

        for entry in feed.entries[:10]:
            eid = entry.get("id") or entry.get("link")
            if eid in seen or eid in all_seen:
                if eid not in seen:
                    seen.insert(0, eid)
                continue
            seen.insert(0, eid)
            all_seen.add(eid)
            # Na primeira vez que vê um feed só marca como lido (não despeja posts antigos)
            if first_run or is_blocked(entry) or not has_required_words(entry, feed_info):
                continue
            to_send.append((entry, feed_info))

        state[url] = seen[:SEEN_LIMIT]
        time.sleep(PAUSA_ENTRE_FEEDS)  # educado com o Reddit

    # Mais antigos primeiro, para o canal ficar em ordem cronológica
    to_send.sort(key=lambda x: x[0].get("updated_parsed") or time.gmtime(0))
    sent = 0
    for entry, fi in to_send[-MAX_POSTS_PER_RUN:]:
        is3d = is_3d(entry, fi)
        webhook = WEBHOOK_URL_3D if (is3d and WEBHOOK_URL_3D) else WEBHOOK_URL
        role = ROLE_ID_3D if is3d else ROLE_ID
        if send_to_discord(build_embed(entry, fi["name"], is3d), webhook, role):
            sent += 1
            print(f"✅ Enviado: {entry.get('title', '')[:60]}")
        else:
            print(f"❌ Erro ao enviar: {entry.get('title', '')[:60]}")
        time.sleep(1.5)

    save_state(state)
    print(f"✅ Verificação concluída! {sent} post(s) enviado(s).")


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        check_only()
    else:
        check_feeds()
