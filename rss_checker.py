import feedparser
import requests
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

# ============================================================
# CONFIGURAÇÕES
# ============================================================
# As URLs dos webhooks NUNCA ficam no código: vêm dos Secrets do GitHub.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()
# (Opcional) canal só de 3D. Sem ele, o 3D vai pro canal normal com a etiqueta 🧊 3D.
WEBHOOK_URL_3D = os.environ.get("WEBHOOK_URL_3D", "").strip()

# (Opcional) cargos a marcar (Variables do GitHub). Vazio = não marca ninguém.
ROLE_ID = os.environ.get("ROLE_ID", "").strip()        # posts 2D / gerais
ROLE_ID_3D = os.environ.get("ROLE_ID_3D", "").strip()  # posts 3D

MAX_POSTS_PER_RUN = 10      # máximo de posts enviados por execução (evita flood)
SEEN_LIMIT = 200            # quantos IDs já vistos guardar por subreddit
PAUSA_ENTRE_SUBS = 7        # segundos entre pedidos (Reddit aceita ~10/min sem login)
TEMPO_MAXIMO = 8 * 60       # se passar disso, para e continua na próxima execução

# Posts com estas palavras são ignorados (minúsculas). Posts marcados +18 no Reddit também.
BLOCKED_WORDS = ["nsfw", "unpaid", "exposure", "for free", "[for hire]", "rev share", "revshare"]

# Palavras que fazem um post ser tratado como 3D.
KEYWORDS_3D = ["3d", "blender", "maya", "zbrush", "substance", "vrchat", "vrm",
               "unity avatar", "low poly", "lowpoly", "sculpt", "rigging", "rigger",
               "3d print", "stl", "hard surface", "retopo", "texturing"]

# Palavras de arte (usadas para filtrar subs gerais como r/forhire, que tem de tudo).
ART_WORDS = KEYWORDS_3D + ["artist", "art", "illustrat", "drawing", "draw", "character design",
                           "concept art", "animator", "animation", "vtuber", "live2d", "avatar",
                           "emote", "emotes", "logo", "comic", "manga", "anime", "portrait",
                           "pixel art", "sprite", "commission", "painting", "sketch", "twitch"]

# ------------------------------------------------------------
# SUBREDDITS
# Um pedido por sub. "flairs": trechos do flair que contam como vaga
# (sem diferenciar maiúsculas; ex. "hiring" pega "Hiring 2D: Other" e "Hiring 3D: Stylized").
# Se o post não tiver flair, o bot procura o mesmo trecho entre [colchetes] no título.
# "exigir_palavras": o post só passa se tiver pelo menos uma dessas palavras.
# "canal": "3d" manda tudo do sub para o canal de 3D.
# "busca": para subs que não põem [Hiring] no título, usa a busca do Reddit por flair
#          (todos os resultados já contam como vaga).
# ------------------------------------------------------------
SUBS = [
    {"sub": "artcommissions",     "name": "🎨 Art Commissions",    "flairs": ["patron", "hiring"]},
    {"sub": "HungryArtists",      "name": "🖌️ HungryArtists",      "flairs": ["hiring"]},
    {"sub": "commissions",        "name": "💼 Commissions",        "flairs": ["hiring", "patron"]},
    {"sub": "artcommission",      "name": "🎨 ArtCommission",      "flairs": ["hiring", "patron"]},
    {"sub": "ComicBookCollabs",   "name": "📚 ComicBookCollabs",   "flairs": ["paid"],
     "busca": "flair:Paid"},
    {"sub": "starvingartists",    "name": "🍽️ StarvingArtists",    "flairs": ["hiring"]},
    {"sub": "hireanartist",       "name": "👨‍🎨 HireAnArtist",       "flairs": ["hiring"],
     "busca": "flair:Hiring"},
    {"sub": "Artistsforhire",     "name": "🎯 ArtistsForHire",     "flairs": ["hiring"]},
    {"sub": "gameDevClassifieds", "name": "🎮 GameDev Classifieds",
     "flairs": ["paid - 2d", "paid - 3d", "paid - multiple"]},
    {"sub": "forhire",            "name": "💼 ForHire (arte)",     "flairs": ["hiring"],
     "exigir_palavras": ART_WORDS},
]

# O GitHub divide o mesmo IP com muita gente e o Reddit recusa o formato JSON (que traz
# o flair) vindo de lá. Deixe False no GitHub; True só se rodar no seu próprio PC.
USAR_JSON = os.environ.get("USAR_JSON", "").lower() == "true"

HEADERS = {"User-Agent": "linux:jerry-o-agente-de-arte:v2.1 (bot de commissions para Discord)"}
STATE_FILE = "last_posted.json"
LAST_ERROR = {"msg": ""}
START = time.time()


# ============================================================
# ESTADO
# ============================================================
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {k: (v if isinstance(v, list) else [v]) for k, v in data.items()}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ============================================================
# REDDIT
# ============================================================
def _get(url):
    """GET com respeito ao limite do Reddit. Desiste rápido para não travar a Action."""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
        except requests.RequestException as e:
            LAST_ERROR["msg"] = str(e)[:80]
            time.sleep(5)
            continue
        if r.status_code == 200:
            return r
        LAST_ERROR["msg"] = f"HTTP {r.status_code}"
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("Retry-After") or r.headers.get("x-ratelimit-reset") or 15)
            except ValueError:
                wait = 15
            wait = min(max(wait, 5), 30)
            LAST_ERROR["msg"] = f"429 limite do Reddit"
            time.sleep(wait)
            continue
        if r.status_code in (403, 404):
            return None
        time.sleep(5)
    return None


def fetch_posts(sub_info):
    """Busca os posts novos do sub (RSS; JSON opcional, que traz flair e +18)."""
    sub = sub_info["sub"]
    r = None
    if USAR_JSON and not sub_info.get("busca"):
        r = _get(f"https://www.reddit.com/r/{sub}/new.json?limit=50&raw_json=1")
    if r is not None:
        try:
            out = []
            for c in r.json()["data"]["children"]:
                d = c["data"]
                out.append({
                    "id": d.get("name") or d.get("id"),
                    "title": d.get("title", ""),
                    "link": "https://www.reddit.com" + d.get("permalink", ""),
                    "text": d.get("selftext", ""),
                    "author": d.get("author", ""),
                    "flair": d.get("link_flair_text") or "",
                    "nsfw": bool(d.get("over_18")),
                    "created": d.get("created_utc", 0),
                    "via_busca": False,
                })
            return out
        except (ValueError, KeyError, TypeError):
            pass
        time.sleep(PAUSA_ENTRE_SUBS)

    if sub_info.get("busca"):
        q = requests.utils.quote(sub_info["busca"])
        url = f"https://www.reddit.com/r/{sub}/search.rss?q={q}&sort=new&restrict_sr=on&limit=50"
    else:
        url = f"https://www.reddit.com/r/{sub}/new/.rss?limit=50"
    r = _get(url)
    if r is None:
        return None
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries:
        out.append({
            "id": e.get("id") or e.get("link"),
            "title": e.get("title", ""),
            "link": e.get("link"),
            "text": e.get("summary", ""),
            "author": e.get("author", "").replace("/u/", ""),
            "flair": "",
            "nsfw": False,
            "created": time.mktime(e.updated_parsed) if e.get("updated_parsed") else 0,
            "via_busca": bool(sub_info.get("busca")),
        })
    return out


# ============================================================
# FILTROS
# ============================================================
def _has_any(text, words):
    text = " " + text.lower() + " "
    return any(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text) for w in words)


def matches_flair(post, sub_info):
    if post.get("via_busca"):
        return True  # a própria busca do Reddit já filtrou pelo flair
    wanted = [f.lower() for f in sub_info["flairs"]]
    flair = post["flair"].lower()
    if flair:
        return any(w in flair for w in wanted)
    # Sem flair: procura [Hiring], [Paid] etc. no título
    tags = " ".join(re.findall(r"\[([^\]]+)\]", post["title"].lower()))
    return any(w in tags for w in wanted)


def passes_filters(post, sub_info):
    if post["nsfw"]:
        return False
    blob = f"{post['title']} {post['flair']} {post['text']}"
    if any(w in blob.lower() for w in BLOCKED_WORDS):
        return False
    if not matches_flair(post, sub_info):
        return False
    words = sub_info.get("exigir_palavras")
    if words and not _has_any(blob, words):
        return False
    return True


def is_3d(post, sub_info):
    if sub_info.get("canal") == "3d":
        return True
    return _has_any(f"{post['title']} {post['flair']} {post['text']}", KEYWORDS_3D)


# ============================================================
# DISCORD
# ============================================================
def clean_text(text):
    if not text:
        return ""
    text = re.sub(r"(?s)submitted by.*$", "", text)          # rodapé do RSS
    text = re.sub(r"<[^<]+?>", " ", text)                     # HTML
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", text)  # links markdown
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:600] + ("…" if len(text) > 600 else "")


def build_embed(post, sub_info, is3d):
    ts = (datetime.fromtimestamp(post["created"], timezone.utc) if post["created"]
          else datetime.now(timezone.utc)).isoformat()
    embed = {
        "title": (("🧊 3D · " if is3d else "") + (post["title"] or "Sem título"))[:256],
        "url": post["link"],
        "description": clean_text(post["text"]) or "Clique para ver os detalhes…",
        "color": 0x2ECC71 if is3d else 0x5865F2,
        "author": {"name": f"{sub_info['name']} · r/{sub_info['sub']}"},
        "timestamp": ts,
        "footer": {"text": "⚠️ Nunca pague para trabalhar · confira o perfil do cliente antes"},
        "fields": [],
    }
    if post["flair"]:
        embed["fields"].append({"name": "Flair", "value": post["flair"][:100], "inline": True})
    if post["author"]:
        embed["fields"].append({"name": "Postado por", "value": f"u/{post['author']}", "inline": True})
    return embed


def send_to_discord(embed, webhook, role_id=""):
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    if role_id:
        payload["content"] = f"<@&{role_id}>"
        payload["allowed_mentions"] = {"roles": [role_id]}
    for _ in range(3):
        r = requests.post(webhook, json=payload, timeout=20)
        if r.status_code == 429:  # limite do Discord
            try:
                time.sleep(float(r.json().get("retry_after", 2)))
            except ValueError:
                time.sleep(2)
            continue
        return r.status_code in (200, 204)
    return False


# ============================================================
# EXECUÇÃO
# ============================================================
def out_of_time():
    return time.time() - START > TEMPO_MAXIMO


def check_only():
    """Modo teste: mostra o que cada sub retorna, sem enviar nada."""
    print("Testando subreddits (nada será enviado)...\n")
    for i, si in enumerate(SUBS):
        if out_of_time():
            print("⏱️ Tempo máximo atingido, parando o teste aqui.")
            break
        if i:
            time.sleep(PAUSA_ENTRE_SUBS)
        posts = fetch_posts(si)
        if posts is None:
            print(f"❌ r/{si['sub']}: erro ({LAST_ERROR['msg']})")
            continue
        ok = [p for p in posts if passes_filters(p, si)]
        n3d = sum(1 for p in ok if is_3d(p, si))
        print(f"✅ r/{si['sub']}: {len(posts)} posts recentes, {len(ok)} são vagas ({n3d} de 3D)")
        flairs = sorted({p["flair"] for p in posts if p["flair"]})
        if flairs:
            print(f"     flairs vistos: {flairs}")
        if ok:
            print(f"     exemplo: {ok[0]['title'][:80]}")


def check_feeds():
    if not WEBHOOK_URL:
        raise SystemExit("❌ WEBHOOK_URL não definido. Crie o Secret WEBHOOK_URL no GitHub.")

    state = load_state()
    to_send = []
    # Ordem aleatória a cada execução: se o Reddit limitar no meio, não é sempre o mesmo sub que fica de fora
    random.shuffle(SUBS)

    for i, si in enumerate(SUBS):
        if out_of_time():
            print("⏱️ Tempo máximo atingido; o resto fica para a próxima execução.")
            break
        if i:
            time.sleep(PAUSA_ENTRE_SUBS)
        print(f"Verificando r/{si['sub']}...")
        posts = fetch_posts(si)
        if posts is None:
            print(f"   ❌ {LAST_ERROR['msg']}")
            continue

        key = "r/" + si["sub"]
        if not posts:
            continue
        first_run = key not in state
        seen = state.setdefault(key, [])
        seen_set = set(seen)
        for p in posts:
            if not p["id"] or p["id"] in seen_set:
                continue
            seen.insert(0, p["id"])
            seen_set.add(p["id"])
            # Na primeira vez só marca como lido (não despeja posts antigos no canal)
            if not first_run and passes_filters(p, si):
                to_send.append((p, si))
        state[key] = seen[:SEEN_LIMIT]

    # Mais antigos primeiro, para o canal ficar em ordem
    to_send.sort(key=lambda x: x[0]["created"])
    sent = 0
    for p, si in to_send[-MAX_POSTS_PER_RUN:]:
        three_d = is_3d(p, si)
        webhook = WEBHOOK_URL_3D if (three_d and WEBHOOK_URL_3D) else WEBHOOK_URL
        role = ROLE_ID_3D if three_d else ROLE_ID
        if send_to_discord(build_embed(p, si, three_d), webhook, role):
            sent += 1
            print(f"✅ Enviado: {p['title'][:60]}")
        else:
            print(f"❌ Erro ao enviar: {p['title'][:60]}")
        time.sleep(1.5)

    save_state(state)
    print(f"✅ Verificação concluída! {sent} post(s) enviado(s).")


if __name__ == "__main__":
    if "--check" in sys.argv:
        check_only()
    else:
        check_feeds()
