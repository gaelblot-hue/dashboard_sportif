from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import time
import os
import json
import base64
import io
from datetime import datetime
from PIL import Image
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max

# ============================================================
# CLÉS API
# ============================================================
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
OPENWEATHER_KEY   = os.getenv('OPENWEATHER_KEY')
APISPORTS_KEY     = os.getenv('APISPORTS_KEY')
GROQ_API_KEY      = os.getenv('GROQ_API_KEY')
CEREBRAS_API_KEY  = os.getenv('CEREBRAS_API_KEY')
ODDS_API_KEY      = os.getenv('ODDS_API_KEY', '')
BALLDONTLIE_KEY   = os.getenv('BALLDONTLIE_KEY', '')

# ============================================================
# NTFY — Notifications push gratuites
# ============================================================
NTFY_CHANNEL = "radar-v6-gael"
NTFY_URL = f"https://ntfy.sh/{NTFY_CHANNEL}"

def envoyer_notif_ntfy(titre, message, priorite="high", tags="rotating_light"):
    try:
        requests.post(
            NTFY_URL,
            data=message.encode('utf-8'),
            headers={
                "Title": titre,
                "Priority": priorite,
                "Tags": tags,
                "Content-Type": "text/plain; charset=utf-8"
            },
            timeout=5
        )
        print(f"Notif ntfy envoyée : {titre}")
    except Exception as e:
        print(f"Erreur ntfy : {e}")

# ============================================================
# UPSTASH REDIS — Mémoire persistante
# ============================================================
UPSTASH_URL   = os.getenv('UPSTASH_REDIS_REST_URL', '')
UPSTASH_TOKEN = os.getenv('UPSTASH_REDIS_REST_TOKEN', '')

def redis_get(key):
    try:
        r = requests.get(
            f"{UPSTASH_URL}/get/{key}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=5
        )
        result = r.json()
        if result.get('result'):
            return json.loads(result['result'])
    except Exception as e:
        print(f"Redis GET erreur ({key}): {e}")
    return None

def redis_set(key, value, ex=None):
    try:
        data = json.dumps(value, ensure_ascii=False)
        url = f"{UPSTASH_URL}/set/{key}"
        if ex:
            url += f"?ex={ex}"
        requests.post(
            url,
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}", "Content-Type": "application/json"},
            json=data,
            timeout=5
        )
    except Exception as e:
        print(f"Redis SET erreur ({key}): {e}")

# ============================================================
# CACHE INTELLIGENT
# ============================================================
CACHE = {}
CACHE_DURATIONS = {
    "live":       2  * 60,
    "scheduled":  2  * 3600,
    "odds":       4  * 3600,
    "weather":    30 * 60,
    "tennis_key": 24 * 3600,
}

def get_cache(key, cache_type="scheduled"):
    if key in CACHE:
        data, timestamp = CACHE[key]
        if time.time() - timestamp < CACHE_DURATIONS[cache_type]:
            return data
    return None

def set_cache(key, data):
    CACHE[key] = (data, time.time())

# ============================================================
# MÉMOIRE & FICHIERS
# ============================================================
ALERTES      = []
CHAT_HISTORY = []

BOOKMAKERS = ["Winamax", "Betify", "Mystake", "Betclic"]

def load_chat_history():
    data = redis_get('chat_history')
    return data if data else []

def save_chat_history(history):
    try:
        redis_set('chat_history', history[-200:])
    except Exception as e:
        print(f"Erreur sauvegarde chat Redis : {e}")

# ============================================================
# SOFASCORE — API interne JSON (zéro clé API)
# ============================================================
SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

# Mapping sport_id → tournament IDs SofaScore
SOFASCORE_TOURNAMENTS = {
    # Football clubs
    "epl":          17,
    "laliga":       8,
    "bundesliga":   35,
    "ligue1":       34,
    "seriea":       23,
    "ucl":          7,
    "mls":          242,
    # Football international
    "amicaux":      11,
    "nations":      1007,
    "worldcup":     16,
    # Basket
    "nba":          132,
    "euroleague":   551,
    "eurocup":      552,
    "proA":         182,   # Pro A Betclic Elite
    # Tennis — géré par catégorie, pas tournoi (voir fetch_sofascore_tennis)
    # "atp" et "wta" sont dans SOFASCORE_TENNIS_CATEGORIES
}

# Pour le tennis, on filtre par catégorie SofaScore (change chaque semaine)
SOFASCORE_TENNIS_CATEGORIES = {
    "atp": 3,
    "wta": 6,
}

# Mapping sport_id → sport_slug SofaScore
SOFASCORE_SPORT_SLUGS = {
    "epl": "football", "laliga": "football", "bundesliga": "football",
    "ligue1": "football", "seriea": "football", "ucl": "football",
    "mls": "football", "amicaux": "football", "nations": "football", "worldcup": "football",
    "nba": "basketball", "euroleague": "basketball", "eurocup": "basketball", "proA": "basketball",
    "atp": "tennis", "wta": "tennis",
}

def fetch_sofascore_odds(event_id):
    """
    Récupère les cotes d'un match SofaScore via son API interne.
    FIX : ajout des market names manquants (Winner, 2Way, Home/Away, Full Time Result).
    """
    if not event_id:
        return []
    try:
        url = f"https://api.sofascore.com/api/v1/event/{event_id}/odds/1/all"
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        data = r.json()
        markets = data.get("markets", [])
        cotes = []
        # FIX : liste étendue des noms de marchés selon le sport
        VALID_MARKETS = [
            "Full time", "Moneyline", "1X2", "Match Winner",
            "Full Time Result", "Home/Away", "Winner", "2Way",
            "Match Result", "To Win Match"
        ]
        for market in markets:
            if market.get("marketName") in VALID_MARKETS:
                choices = market.get("choices", [])
                home_cote = away_cote = draw_cote = None
                for choice in choices:
                    name = choice.get("name", "")
                    # FIX : récupérer decimalValue en priorité (fractionalValue = format UK)
                    val = choice.get("decimalValue") or choice.get("fractionalValue")
                    try:
                        val = float(val)
                    except (TypeError, ValueError):
                        val = None
                    if name in ["1", "Home", "W1", "1 (Home)"]:
                        home_cote = val
                    elif name in ["2", "Away", "W2", "2 (Away)"]:
                        away_cote = val
                    elif name in ["X", "Draw", "X (Draw)"]:
                        draw_cote = val
                if home_cote or away_cote:
                    entry = {
                        "bookmaker": "SofaScore",
                        "home_cote": home_cote,
                        "away_cote": away_cote,
                    }
                    if draw_cote:
                        entry["draw_cote"] = draw_cote
                    cotes.append(entry)
                    break
        return cotes
    except Exception as e:
        print(f"SofaScore odds erreur (event {event_id}): {e}")
        return []

def fetch_sofascore_events(sport_id):
    """
    Récupère les matchs du jour via l'API interne SofaScore.
    FIX : tennis géré séparément par catégorie.
    """
    # Le tennis est géré par fetch_sofascore_tennis()
    if sport_id in ["atp", "wta"]:
        return fetch_sofascore_tennis(sport_id)

    cache_key = f"sofascore_{sport_id}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached

    tournament_id = SOFASCORE_TOURNAMENTS.get(sport_id)
    if not tournament_id:
        return []

    sport_slug = SOFASCORE_SPORT_SLUGS.get(sport_id, "football")
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://api.sofascore.com/api/v1/sport/{sport_slug}/scheduled-events/{today}"

    try:
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=10)
        print(f"SofaScore {sport_id} status: {r.status_code}")
        if r.status_code != 200:
            return []

        data = r.json()
        events = data.get("events", [])
        matchs = []

        for event in events:
            t_id = event.get("tournament", {}).get("uniqueTournament", {}).get("id")
            if t_id != tournament_id:
                continue

            home = event.get("homeTeam", {}).get("name", "?")
            away = event.get("awayTeam", {}).get("name", "?")
            event_id = event.get("id")
            start_ts = event.get("startTimestamp", 0)
            commence_time = datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M') if start_ts else ""
            status_code = event.get("status", {}).get("type", "notstarted")
            score_home = event.get("homeScore", {}).get("current")
            score_away = event.get("awayScore", {}).get("current")

            cotes = fetch_sofascore_odds(event_id)

            matchs.append({
                "id": str(event_id),
                "home_team": home,
                "away_team": away,
                "commence_time": commence_time,
                "status": "in_progress" if status_code == "inprogress" else "scheduled",
                "score_home": score_home,
                "score_away": score_away,
                "cotes": cotes,
                "home_odds": cotes[0].get("home_cote") if cotes else None,
                "away_odds": cotes[0].get("away_cote") if cotes else None,
                "source": "sofascore"
            })

        if matchs:
            set_cache(cache_key, matchs)
            print(f"SofaScore {sport_id}: {len(matchs)} matchs récupérés")
        return matchs

    except Exception as e:
        print(f"SofaScore erreur ({sport_id}): {e}")
        return []

def fetch_sofascore_tennis(sport_id):
    """
    FIX TENNIS : filtre par category.id au lieu de tournament.id
    car le tournoi ATP/WTA change chaque semaine.
    ATP category_id = 3, WTA category_id = 6.
    """
    cache_key = f"sofascore_{sport_id}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached

    category_id = SOFASCORE_TENNIS_CATEGORIES.get(sport_id)
    if not category_id:
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://api.sofascore.com/api/v1/sport/tennis/scheduled-events/{today}"

    try:
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=10)
        print(f"SofaScore tennis {sport_id} status: {r.status_code}")
        if r.status_code != 200:
            return []

        data = r.json()
        events = data.get("events", [])
        matchs = []

        for event in events:
            # FIX : filtrer par category.id (ATP=3, WTA=6) pas par tournament
            cat_id = event.get("tournament", {}).get("category", {}).get("id")
            if cat_id != category_id:
                continue

            home = event.get("homeTeam", {}).get("name", "?")
            away = event.get("awayTeam", {}).get("name", "?")
            event_id = event.get("id")
            start_ts = event.get("startTimestamp", 0)
            commence_time = datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M') if start_ts else ""
            status_code = event.get("status", {}).get("type", "notstarted")

            # Score tennis
            score_home = event.get("homeScore", {}).get("current")
            score_away = event.get("awayScore", {}).get("current")

            # Tournoi actif
            tournament_name = event.get("tournament", {}).get("name", "")

            cotes = fetch_sofascore_odds(event_id)

            matchs.append({
                "id": str(event_id),
                "home_team": home,
                "away_team": away,
                "commence_time": commence_time,
                "status": "in_progress" if status_code == "inprogress" else "scheduled",
                "score_home": score_home,
                "score_away": score_away,
                "tournament": tournament_name,
                "cotes": cotes,
                "home_odds": cotes[0].get("home_cote") if cotes else None,
                "away_odds": cotes[0].get("away_cote") if cotes else None,
                "source": "sofascore"
            })

        if matchs:
            set_cache(cache_key, matchs)
            print(f"SofaScore tennis {sport_id}: {len(matchs)} matchs récupérés")
        return matchs

    except Exception as e:
        print(f"SofaScore tennis erreur ({sport_id}): {e}")
        return []

# ============================================================
# ESPN API GRATUITE — NBA, Euroleague, EuroCup (zéro blocage)
# ============================================================
ESPN_ENDPOINTS = {
    "nba":        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "euroleague": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",  # fallback
    "eurocup":    None,
    "proA":       None,
}

ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
    "Accept": "application/json",
}

def fetch_espn_nba():
    """
    FIX NBA : ESPN API gratuite, zéro blocage depuis Render.
    Remplace Bovada qui est mort depuis 2024.
    """
    cache_key = "espn_nba"
    cached = get_cache(cache_key, "live")
    if cached:
        return cached

    try:
        r = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
            headers=ESPN_HEADERS,
            timeout=10
        )
        print(f"ESPN NBA status: {r.status_code}")
        if r.status_code != 200:
            return []

        data = r.json()
        events = data.get("events", [])
        matchs = []

        for event in events:
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            comp = competitions[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue

            home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
            away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

            home_name = home.get("team", {}).get("displayName", "?")
            away_name = away.get("team", {}).get("displayName", "?")
            home_score = home.get("score")
            away_score = away.get("score")

            status_state = event.get("status", {}).get("type", {}).get("state", "pre")
            is_live = status_state == "in"
            is_finished = status_state == "post"

            if is_finished:
                continue

            start_str = event.get("date", "")
            try:
                dt = datetime.strptime(start_str, "%Y-%m-%dT%H:%MZ")
                commence_time = dt.strftime('%Y-%m-%d %H:%M')
            except:
                commence_time = start_str

            # Odds ESPN si dispo
            cotes = []
            odds_list = comp.get("odds", [])
            if odds_list:
                o = odds_list[0]
                home_odds = o.get("homeTeamOdds", {}).get("moneyLine")
                away_odds = o.get("awayTeamOdds", {}).get("moneyLine")
                if home_odds and away_odds:
                    # Convertir American odds → décimal
                    def american_to_decimal(ml):
                        try:
                            ml = float(ml)
                            if ml > 0:
                                return round(ml / 100 + 1, 2)
                            else:
                                return round(100 / abs(ml) + 1, 2)
                        except:
                            return None
                    hd = american_to_decimal(home_odds)
                    ad = american_to_decimal(away_odds)
                    if hd and ad:
                        cotes = [{"bookmaker": "ESPN/BetMGM", "home_cote": hd, "away_cote": ad}]

            matchs.append({
                "id": str(event.get("id", "")),
                "home_team": home_name,
                "away_team": away_name,
                "commence_time": commence_time,
                "status": "in_progress" if is_live else "scheduled",
                "score_home": home_score if is_live else None,
                "score_away": away_score if is_live else None,
                "cotes": cotes,
                "home_odds": cotes[0].get("home_cote") if cotes else None,
                "away_odds": cotes[0].get("away_cote") if cotes else None,
                "source": "espn"
            })

        if matchs:
            set_cache(cache_key, matchs)
            print(f"ESPN NBA: {len(matchs)} matchs récupérés")
        return matchs

    except Exception as e:
        print(f"ESPN NBA erreur: {e}")
        return []

def fetch_espn_euroleague():
    """
    ESPN Euroleague via leur endpoint basketball international.
    """
    cache_key = "espn_euroleague"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached

    try:
        r = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/basketball/euroleague/scoreboard",
            headers=ESPN_HEADERS,
            timeout=10
        )
        print(f"ESPN Euroleague status: {r.status_code}")
        if r.status_code != 200:
            return []

        data = r.json()
        events = data.get("events", [])
        matchs = []

        for event in events:
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            comp = competitions[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue

            home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
            away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

            home_name = home.get("team", {}).get("displayName", "?")
            away_name = away.get("team", {}).get("displayName", "?")
            home_score = home.get("score")
            away_score = away.get("score")

            status_state = event.get("status", {}).get("type", {}).get("state", "pre")
            is_live = status_state == "in"
            is_finished = status_state == "post"
            if is_finished:
                continue

            start_str = event.get("date", "")
            try:
                dt = datetime.strptime(start_str, "%Y-%m-%dT%H:%MZ")
                commence_time = dt.strftime('%Y-%m-%d %H:%M')
            except:
                commence_time = start_str

            matchs.append({
                "id": str(event.get("id", "")),
                "home_team": home_name,
                "away_team": away_name,
                "commence_time": commence_time,
                "status": "in_progress" if is_live else "scheduled",
                "score_home": home_score if is_live else None,
                "score_away": away_score if is_live else None,
                "cotes": [],
                "source": "espn"
            })

        if matchs:
            set_cache(cache_key, matchs)
            print(f"ESPN Euroleague: {len(matchs)} matchs")
        return matchs

    except Exception as e:
        print(f"ESPN Euroleague erreur: {e}")
        return []

# ============================================================
# FLASHSCORE — Scores live (format propriétaire)
# ============================================================
FLASHSCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": "https://www.flashscore.com/",
    "Origin": "https://www.flashscore.com",
    "X-Fsign": "SW9D1eZo",
}

FLASHSCORE_SPORTS = {
    "epl":        {"sport": "football", "country": "england",  "league": "premier-league",  "id": "p6ahwuwJ"},
    "laliga":     {"sport": "football", "country": "spain",    "league": "laliga",           "id": "W7fNEKFW"},
    "bundesliga": {"sport": "football", "country": "germany",  "league": "bundesliga",       "id": "dGmfBhFK"},
    "ligue1":     {"sport": "football", "country": "france",   "league": "ligue-1",          "id": "GFOjWhIc"},
    "seriea":     {"sport": "football", "country": "italy",    "league": "serie-a",          "id": "WXaknYP6"},
    "ucl":        {"sport": "football", "country": "europe",   "league": "champions-league", "id": "jQ8QEkQj"},
    "nba":        {"sport": "basketball", "country": "usa",    "league": "nba",              "id": "OmcgcWiH"},
    "euroleague": {"sport": "basketball", "country": "europe", "league": "euroleague",       "id": "ycGSNKeK"},
    "atp":        {"sport": "tennis",   "country": "",         "league": "atp-singles",      "id": ""},
    "wta":        {"sport": "tennis",   "country": "",         "league": "wta-singles",      "id": ""},
}

def parse_flashscore_feed(raw_text):
    matchs = []
    if not raw_text:
        return matchs
    blocs = raw_text.split('~')
    for bloc in blocs:
        if not bloc.strip():
            continue
        parts = bloc.split('¬')
        data = {}
        i = 0
        while i < len(parts) - 1:
            key = parts[i].strip()
            val = parts[i+1].strip() if i+1 < len(parts) else ''
            if key:
                data[key] = val
            i += 2

        match_id = data.get('AA') or data.get('~AA')
        home = data.get('CX') or data.get('AE') or data.get('CL')
        away = data.get('AF') or data.get('CY') or data.get('CM')
        score_home = data.get('AG')
        score_away = data.get('AH')
        status_code = data.get('AB', '')
        start_ts = data.get('AD', '0')

        if not match_id or not home or not away:
            continue

        try:
            start_ts = int(start_ts)
            commence_time = datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M') if start_ts > 0 else ''
        except:
            commence_time = ''

        is_live = status_code in ['2', '3', '4', '6', '7']
        is_finished = status_code in ['5', '100']

        if is_finished:
            continue

        matchs.append({
            "id": f"fs_{match_id}",
            "home_team": home,
            "away_team": away,
            "commence_time": commence_time,
            "status": "in_progress" if is_live else "scheduled",
            "score_home": score_home if is_live else None,
            "score_away": score_away if is_live else None,
            "cotes": [],
            "source": "flashscore"
        })

    return matchs

def fetch_flashscore_live():
    cache_key = "flashscore_live_all"
    cached = get_cache(cache_key, "live")
    if cached:
        return cached

    url = "https://d.flashscore.com/x/feed/f_1_0_1_en_1"
    try:
        r = requests.get(url, headers=FLASHSCORE_HEADERS, timeout=10)
        print(f"Flashscore live status: {r.status_code}")
        if r.status_code != 200:
            return []

        matchs = parse_flashscore_feed(r.text)
        live = [m for m in matchs if m['status'] == 'in_progress']
        print(f"Flashscore live: {len(live)} matchs en direct")

        if live:
            set_cache(cache_key, live)
        return live

    except Exception as e:
        print(f"Flashscore live erreur: {e}")
        return []

def fetch_flashscore_sport(sport_id):
    cache_key = f"flashscore_{sport_id}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached

    sport_info = FLASHSCORE_SPORTS.get(sport_id)
    if not sport_info or not sport_info.get("id"):
        return []

    league_id = sport_info["id"]
    url = f"https://d.flashscore.com/x/feed/f_2_{league_id}_en_1"

    try:
        r = requests.get(url, headers=FLASHSCORE_HEADERS, timeout=10)
        print(f"Flashscore {sport_id} status: {r.status_code}")
        if r.status_code != 200:
            return []

        matchs = parse_flashscore_feed(r.text)
        print(f"Flashscore {sport_id}: {len(matchs)} matchs")

        if matchs:
            set_cache(cache_key, matchs)
        return matchs

    except Exception as e:
        print(f"Flashscore {sport_id} erreur: {e}")
        return []

def merge_matchs(sofascore_matchs, flashscore_matchs):
    """Fusion complète — utilisée uniquement quand SofaScore est vide."""
    if not flashscore_matchs:
        return sofascore_matchs
    if not sofascore_matchs:
        return flashscore_matchs

    merged = list(sofascore_matchs)
    index = {}
    for i, m in enumerate(merged):
        key = f"{m['home_team'].lower()[:6]}_{m['away_team'].lower()[:6]}"
        index[key] = i

    for fs_match in flashscore_matchs:
        key = f"{fs_match['home_team'].lower()[:6]}_{fs_match['away_team'].lower()[:6]}"
        if key in index:
            i = index[key]
            if fs_match.get('status') == 'in_progress':
                merged[i]['status'] = 'in_progress'
                if fs_match.get('score_home') is not None:
                    merged[i]['score_home'] = fs_match['score_home']
                    merged[i]['score_away'] = fs_match['score_away']
            if not merged[i].get('cotes') and fs_match.get('cotes'):
                merged[i]['cotes'] = fs_match['cotes']
        else:
            merged.append(fs_match)

    return merged

def merge_matchs_live_only(sofascore_matchs, flashscore_matchs):
    """
    FIX LEAGUE ONE : Flashscore utilisé UNIQUEMENT pour mettre à jour
    les scores live des matchs déjà présents dans SofaScore.
    Ne rajoute JAMAIS de nouveaux matchs — élimine l'infiltration
    de League One, League Two, Championship, etc.
    """
    if not flashscore_matchs:
        return sofascore_matchs

    merged = list(sofascore_matchs)
    index = {}
    for i, m in enumerate(merged):
        key = f"{m['home_team'].lower()[:6]}_{m['away_team'].lower()[:6]}"
        index[key] = i

    for fs_match in flashscore_matchs:
        key = f"{fs_match['home_team'].lower()[:6]}_{fs_match['away_team'].lower()[:6]}"
        if key in index:  # Seulement si déjà dans SofaScore
            i = index[key]
            if fs_match.get('status') == 'in_progress':
                merged[i]['status'] = 'in_progress'
                if fs_match.get('score_home') is not None:
                    merged[i]['score_home'] = fs_match['score_home']
                    merged[i]['score_away'] = fs_match['score_away']
            if not merged[i].get('cotes') and fs_match.get('cotes'):
                merged[i]['cotes'] = fs_match['cotes']
        # PAS de else → on n'ajoute jamais un match inconnu depuis Flashscore

    return merged

# ============================================================
# THESPORTSDB — Fallback matchs (zéro cotes)
# ============================================================
SPORTSDB_KEY = "3"

SPORTSDB_LEAGUES = {
    "nba":          "4387",
    "nfl":          "4391",
    "epl":          "4328",
    "laliga":       "4335",
    "bundesliga":   "4331",
    "ligue1":       "4334",
    "seriea":       "4332",
    "ucl":          "4480",
    "atp":          "4424",
    "wta":          "4425",
    "euroleague":   "4966",
    "eurocup":      "4967",
    "proA":         "4422",
    "mls":          "4346",
}

APISPORTS_LEAGUES = {
    "epl":        {"nom": "Premier League"},
    "laliga":     {"nom": "La Liga"},
    "bundesliga": {"nom": "Bundesliga"},
    "ligue1":     {"nom": "Ligue 1"},
    "seriea":     {"nom": "Serie A"},
    "ucl":        {"nom": "Champions League"},
    "nba":        {"nom": "NBA"},
    "euroleague": {"nom": "EuroLeague"},
    "eurocup":    {"nom": "EuroCup"},
    "proA":       {"nom": "Pro A"},
    "atp":        {"nom": "ATP"},
    "wta":        {"nom": "WTA"},
    "amicaux":    {"nom": "Amicaux"},
    "nations":    {"nom": "Nations League"},
    "worldcup":   {"nom": "Qualif Coupe du Monde"},
}

def fetch_sportsdb_events(league_id):
    cache_key = f"sportsdb_{league_id}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached
    try:
        r = requests.get(
            f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_KEY}/eventsnextleague.php",
            params={"id": league_id},
            timeout=8
        )
        if r.status_code != 200:
            return []
        events = r.json().get('events') or []
        matchs = []
        for e in events[:10]:
            matchs.append({
                "id": e.get('idEvent'),
                "home_team": e.get('strHomeTeam', '?'),
                "away_team": e.get('strAwayTeam', '?'),
                "commence_time": e.get('dateEvent', '') + ' ' + (e.get('strTime', '') or ''),
                "status": "scheduled",
                "score_home": e.get('intHomeScore'),
                "score_away": e.get('intAwayScore'),
                "cotes": [],
                "source": "thesportsdb"
            })
        set_cache(cache_key, matchs)
        return matchs
    except Exception as e:
        print(f"TheSportsDB erreur: {e}")
        return []

def fetch_sportsdb_last_events(league_id):
    cache_key = f"sportsdb_last_{league_id}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached
    try:
        r = requests.get(
            f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_KEY}/eventspastleague.php",
            params={"id": league_id},
            timeout=8
        )
        if r.status_code != 200:
            return []
        events = r.json().get('events') or []
        results = []
        for e in events[-10:]:
            results.append({
                "home": e.get('strHomeTeam'),
                "away": e.get('strAwayTeam'),
                "score": f"{e.get('intHomeScore','?')}-{e.get('intAwayScore','?')}",
                "date": e.get('dateEvent')
            })
        set_cache(cache_key, results)
        return results
    except Exception as e:
        print(f"TheSportsDB last events erreur: {e}")
        return []

def fetch_sportsdb_team_info(team_name):
    cache_key = f"sportsdb_team_{team_name}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached
    try:
        r = requests.get(
            f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_KEY}/searchteams.php",
            params={"t": team_name},
            timeout=8
        )
        if r.status_code != 200:
            return None
        teams = r.json().get('teams') or []
        if not teams:
            return None
        team = teams[0]
        info = {
            "nom": team.get('strTeam'),
            "pays": team.get('strCountry'),
            "stade": team.get('strStadium'),
            "description": (team.get('strDescriptionFR') or team.get('strDescriptionEN', ''))[:300]
        }
        set_cache(cache_key, info)
        return info
    except Exception as e:
        print(f"TheSportsDB team erreur: {e}")
        return None

@app.route('/radar/sportsdb/<sport_id>')
def get_sportsdb(sport_id):
    league_id = SPORTSDB_LEAGUES.get(sport_id)
    if not league_id:
        return jsonify({"data": []})
    matchs = fetch_sportsdb_events(league_id)
    return jsonify({"data": matchs, "source": "thesportsdb"})

@app.route('/radar/sportsdb/form/<sport_id>')
def get_sportsdb_form(sport_id):
    league_id = SPORTSDB_LEAGUES.get(sport_id)
    if not league_id:
        return jsonify({"data": []})
    results = fetch_sportsdb_last_events(league_id)
    return jsonify({"data": results, "source": "thesportsdb"})

# ============================================================
# SOFASCORE — NOUVELLES FONCTIONS ENRICHIES
# ============================================================

def fetch_sofascore_team_last_events(team_id, pages=1):
    """
    Historique des N derniers matchs d'une équipe via SofaScore.
    Endpoint : /api/v1/team/{team_id}/events/last/{page}
    page=0 = les 10 derniers, page=1 = les 10 précédents, etc.
    """
    cache_key = f"sofa_team_last_{team_id}_{pages}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached

    all_events = []
    for page in range(pages):
        try:
            url = f"https://api.sofascore.com/api/v1/team/{team_id}/events/last/{page}"
            r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
            if r.status_code != 200:
                break
            data = r.json()
            events = data.get("events", [])
            if not events:
                break
            for event in events:
                home = event.get("homeTeam", {}).get("name", "?")
                away = event.get("awayTeam", {}).get("name", "?")
                score_h = event.get("homeScore", {}).get("current", "?")
                score_a = event.get("awayScore", {}).get("current", "?")
                start_ts = event.get("startTimestamp", 0)
                date_str = datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d') if start_ts else ""
                winner = event.get("winnerCode")  # 1=home, 2=away, 3=draw
                tournament = event.get("tournament", {}).get("name", "")
                all_events.append({
                    "home": home,
                    "away": away,
                    "score": f"{score_h}-{score_a}",
                    "date": date_str,
                    "winner": winner,
                    "tournament": tournament,
                    "event_id": event.get("id")
                })
        except Exception as e:
            print(f"SofaScore team last events erreur (team {team_id}, page {page}): {e}")
            break

    if all_events:
        set_cache(cache_key, all_events)
    return all_events


def fetch_sofascore_team_next_events(team_id):
    """
    Prochains matchs d'une équipe.
    Endpoint : /api/v1/team/{team_id}/events/next/0
    """
    cache_key = f"sofa_team_next_{team_id}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached

    try:
        url = f"https://api.sofascore.com/api/v1/team/{team_id}/events/next/0"
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        data = r.json()
        events = data.get("events", [])
        result = []
        for event in events[:5]:
            home = event.get("homeTeam", {}).get("name", "?")
            away = event.get("awayTeam", {}).get("name", "?")
            start_ts = event.get("startTimestamp", 0)
            date_str = datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M') if start_ts else ""
            tournament = event.get("tournament", {}).get("name", "")
            result.append({
                "home": home,
                "away": away,
                "date": date_str,
                "tournament": tournament,
                "event_id": event.get("id")
            })
        if result:
            set_cache(cache_key, result)
        return result
    except Exception as e:
        print(f"SofaScore team next events erreur (team {team_id}): {e}")
        return []


def fetch_sofascore_event_statistics(event_id):
    """
    Stats avancées d'un match : possession, tirs, xG, passes, fautes, etc.
    Endpoint : /api/v1/event/{event_id}/statistics
    """
    cache_key = f"sofa_stats_{event_id}"
    cached = get_cache(cache_key, "live")
    if cached:
        return cached

    try:
        url = f"https://api.sofascore.com/api/v1/event/{event_id}/statistics"
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if r.status_code != 200:
            return {}
        data = r.json()
        stats_raw = data.get("statistics", [])
        result = {"home": {}, "away": {}}

        for period in stats_raw:
            period_name = period.get("period", "ALL")
            if period_name != "ALL":
                continue
            for group in period.get("groups", []):
                for item in group.get("statisticsItems", []):
                    key = item.get("key", item.get("name", "unknown")).lower().replace(" ", "_")
                    home_val = item.get("home")
                    away_val = item.get("away")
                    result["home"][key] = home_val
                    result["away"][key] = away_val

        if result["home"] or result["away"]:
            set_cache(cache_key, result)
        return result
    except Exception as e:
        print(f"SofaScore stats erreur (event {event_id}): {e}")
        return {}


def fetch_sofascore_event_lineups(event_id):
    """
    Compos officielles (titulaires + remplaçants) d'un match.
    Endpoint : /api/v1/event/{event_id}/lineups
    """
    cache_key = f"sofa_lineups_{event_id}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached

    try:
        url = f"https://api.sofascore.com/api/v1/event/{event_id}/lineups"
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if r.status_code != 200:
            return {}
        data = r.json()

        def parse_lineup(side):
            lineup_data = data.get(side, {})
            players = []
            for p in lineup_data.get("players", []):
                pi = p.get("player", {})
                players.append({
                    "nom": pi.get("name", "?"),
                    "poste": p.get("position", "?"),
                    "numero": p.get("shirtNumber"),
                    "titulaire": p.get("substitute", True) is False
                })
            return {
                "formation": lineup_data.get("formation", "?"),
                "joueurs": players
            }

        result = {
            "home": parse_lineup("home"),
            "away": parse_lineup("away")
        }
        if result["home"]["joueurs"] or result["away"]["joueurs"]:
            set_cache(cache_key, result)
        return result
    except Exception as e:
        print(f"SofaScore lineups erreur (event {event_id}): {e}")
        return {}


def fetch_sofascore_event_h2h(event_id):
    """
    Historique Head-to-Head des deux équipes.
    Endpoint : /api/v1/event/{event_id}/h2h
    """
    cache_key = f"sofa_h2h_{event_id}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached

    try:
        url = f"https://api.sofascore.com/api/v1/event/{event_id}/h2h"
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if r.status_code != 200:
            return {}
        data = r.json()
        events = data.get("events", [])

        h2h_list = []
        for event in events[:10]:
            home = event.get("homeTeam", {}).get("name", "?")
            away = event.get("awayTeam", {}).get("name", "?")
            score_h = event.get("homeScore", {}).get("current", "?")
            score_a = event.get("awayScore", {}).get("current", "?")
            start_ts = event.get("startTimestamp", 0)
            date_str = datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d') if start_ts else ""
            winner = event.get("winnerCode")
            h2h_list.append({
                "home": home,
                "away": away,
                "score": f"{score_h}-{score_a}",
                "date": date_str,
                "winner": winner
            })

        result = {
            "homeTeam": data.get("team1", {}).get("name", "?"),
            "awayTeam": data.get("team2", {}).get("name", "?"),
            "homeWins": data.get("homeTeamWins", 0),
            "awayWins": data.get("awayTeamWins", 0),
            "draws": data.get("draws", 0),
            "matches": h2h_list
        }

        if h2h_list:
            set_cache(cache_key, result)
        return result
    except Exception as e:
        print(f"SofaScore H2H erreur (event {event_id}): {e}")
        return {}


def fetch_sofascore_player_stats(event_id):
    """
    Stats individuelles des joueurs sur un match (buts, passes, notes, tirs, etc.).
    Endpoint : /api/v1/event/{event_id}/player-statistics
    """
    cache_key = f"sofa_player_stats_{event_id}"
    cached = get_cache(cache_key, "live")
    if cached:
        return cached

    try:
        url = f"https://api.sofascore.com/api/v1/event/{event_id}/player-statistics"
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if r.status_code != 200:
            return {}
        data = r.json()

        def parse_players(side_data):
            players = []
            for p in side_data.get("players", []):
                pi = p.get("player", {})
                stats = p.get("statistics", {})
                players.append({
                    "nom": pi.get("name", "?"),
                    "note": stats.get("rating"),
                    "buts": stats.get("goals"),
                    "passes_decidees": stats.get("goalAssist"),
                    "tirs": stats.get("onTargetScoringAttempt"),
                    "duels_gagnes": stats.get("duelWon"),
                    "minutes": stats.get("minutesPlayed"),
                })
            return players

        home_data = data.get("home", {})
        away_data = data.get("away", {})

        result = {
            "home": parse_players(home_data),
            "away": parse_players(away_data)
        }

        if result["home"] or result["away"]:
            set_cache(cache_key, result)
        return result
    except Exception as e:
        print(f"SofaScore player stats erreur (event {event_id}): {e}")
        return {}


def fetch_sofascore_live_events():
    """
    Tous les matchs en direct sur SofaScore (football + basket + tennis).
    Endpoint : /api/v1/sport/{slug}/events/live
    """
    cache_key = "sofa_live_all"
    cached = get_cache(cache_key, "live")
    if cached:
        return cached

    sports = ["football", "basketball", "tennis"]
    all_live = []

    for sport_slug in sports:
        try:
            url = f"https://api.sofascore.com/api/v1/sport/{sport_slug}/events/live"
            r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
            if r.status_code != 200:
                continue
            data = r.json()
            for event in data.get("events", []):
                home = event.get("homeTeam", {}).get("name", "?")
                away = event.get("awayTeam", {}).get("name", "?")
                score_h = event.get("homeScore", {}).get("current")
                score_a = event.get("awayScore", {}).get("current")
                tournament = event.get("tournament", {}).get("name", "")
                event_id = event.get("id")
                status_desc = event.get("status", {}).get("description", "")
                all_live.append({
                    "id": str(event_id),
                    "sport": sport_slug,
                    "tournament": tournament,
                    "home_team": home,
                    "away_team": away,
                    "score_home": score_h,
                    "score_away": score_a,
                    "status_desc": status_desc,
                    "source": "sofascore_live"
                })
        except Exception as e:
            print(f"SofaScore live {sport_slug} erreur: {e}")

    if all_live:
        set_cache(cache_key, all_live)
    print(f"SofaScore live: {len(all_live)} matchs en direct")
    return all_live


def fetch_sofascore_tournament_standings(tournament_id, season_id):
    """
    Classement d'un tournoi/saison.
    Endpoint : /api/v1/tournament/{t_id}/season/{s_id}/standings/total
    """
    cache_key = f"sofa_standings_{tournament_id}_{season_id}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached

    try:
        url = f"https://api.sofascore.com/api/v1/tournament/{tournament_id}/season/{season_id}/standings/total"
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        data = r.json()
        standings = []
        for row in data.get("standings", [{}])[0].get("rows", []):
            team = row.get("team", {})
            standings.append({
                "position": row.get("position"),
                "equipe": team.get("name", "?"),
                "team_id": team.get("id"),
                "joues": row.get("matches"),
                "gagnes": row.get("wins"),
                "nuls": row.get("draws"),
                "perdus": row.get("losses"),
                "buts_pour": row.get("scoresFor"),
                "buts_contre": row.get("scoresAgainst"),
                "points": row.get("points"),
                "forme": row.get("promotion", {}).get("text", "")
            })
        if standings:
            set_cache(cache_key, standings)
        return standings
    except Exception as e:
        print(f"SofaScore standings erreur (t={tournament_id} s={season_id}): {e}")
        return []


def fetch_sofascore_search_team(team_name):
    """
    Recherche une équipe par nom et retourne son team_id SofaScore.
    Endpoint : /api/v1/search/teams?q={name}
    """
    cache_key = f"sofa_search_team_{team_name.lower()}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached

    try:
        url = f"https://api.sofascore.com/api/v1/search/teams"
        r = requests.get(url, headers=SOFASCORE_HEADERS, params={"q": team_name}, timeout=8)
        if r.status_code != 200:
            return []
        data = r.json()
        teams = []
        for team in data.get("teams", [])[:5]:
            teams.append({
                "id": team.get("id"),
                "nom": team.get("name"),
                "pays": team.get("country", {}).get("name", ""),
                "sport": team.get("sport", {}).get("name", "")
            })
        if teams:
            set_cache(cache_key, teams)
        return teams
    except Exception as e:
        print(f"SofaScore search team erreur ({team_name}): {e}")
        return []


def fetch_sofascore_event_incidents(event_id):
    """
    Incidents d'un match : buts, cartons, remplacements, penalties.
    Endpoint : /api/v1/event/{event_id}/incidents
    """
    cache_key = f"sofa_incidents_{event_id}"
    cached = get_cache(cache_key, "live")
    if cached:
        return cached

    try:
        url = f"https://api.sofascore.com/api/v1/event/{event_id}/incidents"
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        data = r.json()
        incidents = []
        for inc in data.get("incidents", []):
            inc_type = inc.get("incidentType", "")
            minute = inc.get("time")
            team = "home" if inc.get("isHome") else "away"
            player = inc.get("player", {}).get("name", "") if inc.get("player") else ""
            incidents.append({
                "type": inc_type,
                "minute": minute,
                "equipe": team,
                "joueur": player,
                "detail": inc.get("description", "")
            })
        if incidents:
            set_cache(cache_key, incidents)
        return incidents
    except Exception as e:
        print(f"SofaScore incidents erreur (event {event_id}): {e}")
        return []


def fetch_sofascore_season_id(tournament_id):
    """
    Récupère le season_id actuel d'un tournoi.
    Endpoint : /api/v1/tournament/{tournament_id}/seasons
    """
    cache_key = f"sofa_season_{tournament_id}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached

    try:
        url = f"https://api.sofascore.com/api/v1/tournament/{tournament_id}/seasons"
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
        seasons = data.get("seasons", [])
        if not seasons:
            return None
        # La première saison est la plus récente
        season_id = seasons[0].get("id")
        set_cache(cache_key, season_id)
        return season_id
    except Exception as e:
        print(f"SofaScore season_id erreur (t={tournament_id}): {e}")
        return None


# ============================================================
# SOFASCORE — NOUVEAUX ENDPOINTS FLASK
# ============================================================

@app.route('/radar/sofascore/live')
def get_sofascore_live():
    """Tous les matchs en direct sur SofaScore (foot + basket + tennis)."""
    matchs = fetch_sofascore_live_events()
    return jsonify({
        "data": matchs,
        "count": len(matchs),
        "source": "sofascore_live",
        "timestamp": datetime.now().strftime("%H:%M:%S")
    })


@app.route('/radar/sofascore/event/<int:event_id>/stats')
def get_event_stats(event_id):
    """Stats avancées d'un match (possession, xG, tirs...)."""
    stats = fetch_sofascore_event_statistics(event_id)
    return jsonify({"event_id": event_id, "statistics": stats})


@app.route('/radar/sofascore/event/<int:event_id>/h2h')
def get_event_h2h(event_id):
    """Head-to-Head des deux équipes d'un match."""
    h2h = fetch_sofascore_event_h2h(event_id)
    return jsonify({"event_id": event_id, "h2h": h2h})


@app.route('/radar/sofascore/event/<int:event_id>/lineups')
def get_event_lineups(event_id):
    """Compos officielles d'un match."""
    lineups = fetch_sofascore_event_lineups(event_id)
    return jsonify({"event_id": event_id, "lineups": lineups})


@app.route('/radar/sofascore/event/<int:event_id>/player-stats')
def get_event_player_stats(event_id):
    """Stats individuelles des joueurs d'un match."""
    stats = fetch_sofascore_player_stats(event_id)
    return jsonify({"event_id": event_id, "player_stats": stats})


@app.route('/radar/sofascore/event/<int:event_id>/incidents')
def get_event_incidents(event_id):
    """Incidents d'un match (buts, cartons, remplacements)."""
    incidents = fetch_sofascore_event_incidents(event_id)
    return jsonify({"event_id": event_id, "incidents": incidents})


@app.route('/radar/sofascore/event/<int:event_id>/full')
def get_event_full(event_id):
    """
    Données complètes d'un match : stats + H2H + compos + player stats + incidents.
    Endpoint agrégé pour le dashboard.
    """
    stats      = fetch_sofascore_event_statistics(event_id)
    h2h        = fetch_sofascore_event_h2h(event_id)
    lineups    = fetch_sofascore_event_lineups(event_id)
    player_stats = fetch_sofascore_player_stats(event_id)
    incidents  = fetch_sofascore_event_incidents(event_id)
    return jsonify({
        "event_id": event_id,
        "statistics": stats,
        "h2h": h2h,
        "lineups": lineups,
        "player_stats": player_stats,
        "incidents": incidents,
        "timestamp": datetime.now().strftime("%H:%M:%S")
    })


@app.route('/radar/sofascore/team/<int:team_id>/form')
def get_team_form(team_id):
    """
    Forme récente d'une équipe : 20 derniers matchs (2 pages).
    Inclut date, score, adversaire, compétition.
    """
    pages = int(request.args.get("pages", 2))
    events = fetch_sofascore_team_last_events(team_id, pages=pages)
    next_events = fetch_sofascore_team_next_events(team_id)

    # Calcul forme : W/D/L sur les 5 derniers
    forme_str = ""
    if events:
        for e in events[-5:]:
            w = e.get("winner")
            if w == 3:
                forme_str += "D"
            elif w is None:
                forme_str += "?"
            else:
                forme_str += "W" if w == 1 else "L"  # approximatif sans savoir si home/away

    return jsonify({
        "team_id": team_id,
        "derniers_matchs": events,
        "prochains_matchs": next_events,
        "forme_5": forme_str[-5:] if forme_str else "",
        "total_matchs": len(events)
    })


@app.route('/radar/sofascore/team/search')
def search_team():
    """Recherche une équipe par nom et retourne son team_id."""
    nom = request.args.get("q", "").strip()
    if not nom:
        return jsonify({"error": "Paramètre 'q' requis"}), 400
    teams = fetch_sofascore_search_team(nom)
    return jsonify({"query": nom, "teams": teams})


@app.route('/radar/sofascore/standings/<int:tournament_id>')
def get_standings(tournament_id):
    """
    Classement d'un tournoi. Récupère automatiquement la saison courante.
    Ex : /radar/sofascore/standings/17 pour la Premier League.
    """
    season_id = request.args.get("season_id")
    if not season_id:
        season_id = fetch_sofascore_season_id(tournament_id)
    if not season_id:
        return jsonify({"error": "Impossible de trouver la saison courante"}), 404

    standings = fetch_sofascore_tournament_standings(tournament_id, season_id)
    return jsonify({
        "tournament_id": tournament_id,
        "season_id": season_id,
        "standings": standings
    })


@app.route('/radar/sofascore/match-full')
def get_match_full_by_teams():
    """
    Données complètes d'un match via noms d'équipes.
    Cherche l'event_id dans le cache SofaScore existant, puis récupère tout.
    Usage : /radar/sofascore/match-full?home=Arsenal&away=Chelsea&sport=epl
    """
    home_name = request.args.get("home", "").strip().lower()
    away_name = request.args.get("away", "").strip().lower()
    sport_id  = request.args.get("sport", "epl")

    if not home_name or not away_name:
        return jsonify({"error": "Paramètres 'home' et 'away' requis"}), 400

    # Cherche dans les matchs déjà fetchés
    matchs = fetch_sofascore_events(sport_id)
    event_id = None
    for m in matchs:
        if home_name in m.get("home_team", "").lower() or away_name in m.get("away_team", "").lower():
            try:
                event_id = int(m["id"])
            except (ValueError, KeyError):
                pass
            break

    if not event_id:
        return jsonify({"error": f"Match '{home_name}' vs '{away_name}' non trouvé dans le cache {sport_id}"}), 404

    stats        = fetch_sofascore_event_statistics(event_id)
    h2h          = fetch_sofascore_event_h2h(event_id)
    lineups      = fetch_sofascore_event_lineups(event_id)
    player_stats = fetch_sofascore_player_stats(event_id)
    incidents    = fetch_sofascore_event_incidents(event_id)

    return jsonify({
        "event_id": event_id,
        "home": home_name,
        "away": away_name,
        "statistics": stats,
        "h2h": h2h,
        "lineups": lineups,
        "player_stats": player_stats,
        "incidents": incidents,
        "timestamp": datetime.now().strftime("%H:%M:%S")
    })


# ============================================================
# FETCH SPORTS — Logique de fallback complète
# ============================================================
def fetch_nba():
    """
    NBA : SofaScore → ESPN (remplace Bovada mort) → TheSportsDB.
    """
    cached = get_cache("sport_nba", "scheduled")
    if cached:
        return cached

    # 1. SofaScore
    sofa = fetch_sofascore_events("nba")
    flash = fetch_flashscore_sport("nba")
    matchs = merge_matchs(sofa, flash)

    # 2. FIX : ESPN à la place de Bovada (mort depuis 2024)
    if not matchs:
        matchs = fetch_espn_nba()

    # 3. Fallback TheSportsDB
    if not matchs:
        matchs = fetch_sportsdb_events(SPORTSDB_LEAGUES.get("nba", "4387"))

    # Si SofaScore a les matchs mais pas de cotes, on tente de fusionner avec ESPN
    if matchs and not any(m.get('cotes') for m in matchs):
        espn = fetch_espn_nba()
        if espn:
            matchs = merge_matchs(matchs, espn)

    set_cache("sport_nba", matchs)
    return matchs

def fetch_euroleague():
    """
    Euroleague : SofaScore → ESPN Euroleague → TheSportsDB.
    """
    cached = get_cache("sport_euroleague", "scheduled")
    if cached:
        return cached

    sofa = fetch_sofascore_events("euroleague")
    flash = fetch_flashscore_sport("euroleague")
    matchs = merge_matchs(sofa, flash)

    # FIX : ESPN Euroleague comme fallback fiable
    if not matchs:
        matchs = fetch_espn_euroleague()

    if not matchs:
        matchs = fetch_sportsdb_events(SPORTSDB_LEAGUES.get("euroleague", "4966"))

    set_cache("sport_euroleague", matchs)
    return matchs

def fetch_eurocup():
    """EuroCup : SofaScore → TheSportsDB."""
    cached = get_cache("sport_eurocup", "scheduled")
    if cached:
        return cached

    sofa = fetch_sofascore_events("eurocup")
    matchs = sofa

    if not matchs:
        matchs = fetch_sportsdb_events(SPORTSDB_LEAGUES.get("eurocup", "4967"))

    set_cache("sport_eurocup", matchs)
    return matchs

def fetch_proA():
    """Pro A : SofaScore → TheSportsDB (pas de cotes dispo sur ces sources)."""
    cached = get_cache("sport_proA", "scheduled")
    if cached:
        return cached

    sofa = fetch_sofascore_events("proA")
    matchs = sofa

    if not matchs:
        matchs = fetch_sportsdb_events(SPORTSDB_LEAGUES.get("proA", "4422"))

    set_cache("sport_proA", matchs)
    return matchs

def fetch_tennis(sport_id):
    """
    Tennis ATP/WTA : SofaScore avec filtre par category_id → TheSportsDB.
    FIX : n'utilise plus le tournament_id fixe (change chaque semaine).
    """
    cache_key = f"sport_{sport_id}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached

    # FIX : fetch_sofascore_tennis() via category_id
    matchs = fetch_sofascore_tennis(sport_id)

    if not matchs:
        league_id = SPORTSDB_LEAGUES.get(sport_id)
        if league_id:
            matchs = fetch_sportsdb_events(league_id)

    set_cache(cache_key, matchs)
    return matchs

def fetch_football_sport(sport_id):
    """
    Football : SofaScore (filtre tournoi exact) puis Flashscore live only.
    FIX LEAGUE ONE : si SofaScore est configuré pour ce sport_id mais renvoie
    vide, on affiche rien plutôt que de tomber sur TheSportsDB qui ramène
    League One, Championship et autres ligues parasites.
    TheSportsDB uniquement pour les sports sans ID SofaScore configuré.
    """
    cached = get_cache(f"sport_{sport_id}", "scheduled")
    if cached:
        return cached

    sofa = fetch_sofascore_events(sport_id)

    if sofa:
        # SofaScore a des données — enrichir avec Flashscore live seulement
        flash = fetch_flashscore_sport(sport_id)
        matchs = merge_matchs_live_only(sofa, flash)
    elif sport_id in SOFASCORE_TOURNAMENTS:
        # SofaScore configuré mais vide — on affiche rien (pas de fallback TheSportsDB)
        matchs = []
    else:
        # Pas de SofaScore configuré — TheSportsDB acceptable
        league_id = SPORTSDB_LEAGUES.get(sport_id)
        matchs = fetch_sportsdb_events(league_id) if league_id else []

    set_cache(f"sport_{sport_id}", matchs)
    return matchs

# ============================================================
# MÉTÉO
# ============================================================
TEAM_CITY_MAP = {
    "paris": "Paris", "psg": "Paris", "marseille": "Marseille", "lyon": "Lyon",
    "barcelona": "Barcelona", "real madrid": "Madrid", "atletico": "Madrid",
    "manchester": "Manchester", "arsenal": "London", "chelsea": "London", "tottenham": "London",
    "bayern": "Munich", "dortmund": "Dortmund",
    "juventus": "Turin", "milan": "Milan", "inter": "Milan", "roma": "Rome", "napoli": "Naples",
    "ajax": "Amsterdam", "porto": "Porto", "benfica": "Lisbon",
}

def get_weather(city="Paris"):
    cached = get_cache(f"weather_{city}", "weather")
    if cached:
        return cached
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_KEY}&units=metric&lang=fr"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            meteo = {
                "ville": city,
                "temp": data['main']['temp'],
                "conditions": data['weather'][0]['description'],
                "vent": data['wind']['speed'],
                "pluie": 'rain' in data['weather'][0]['main'].lower()
            }
            set_cache(f"weather_{city}", meteo)
            return meteo
    except Exception as e:
        print(f"Erreur météo : {e}")
    return None

def get_weather_for_match(match):
    sport = match.get('sport_key', '')
    # FIX : météo inutile pour basket et tennis
    if sport in ['nba', 'euroleague', 'eurocup', 'proA', 'atp', 'wta']:
        return None
    home = match.get('home_team', '').lower()
    for keyword, city in TEAM_CITY_MAP.items():
        if keyword in home:
            return get_weather(city)
    return get_weather()

# ============================================================
# MOTEUR D'EDGE DATA-DRIVEN — Calcul pur Python, zéro IA
# ============================================================

def implied_prob(cote):
    """Cote décimale -> probabilité implicite brute."""
    try:
        return 1.0 / float(cote)
    except (TypeError, ZeroDivisionError):
        return None

def remove_vig(home_cote, away_cote, draw_cote=None):
    """
    Enlève la marge bookmaker et retourne les probas réelles normalisées.
    Retourne (p_home, p_away, p_draw).
    """
    ph = implied_prob(home_cote)
    pa = implied_prob(away_cote)
    pd = implied_prob(draw_cote) if draw_cote else None

    if not ph or not pa:
        return None, None, None

    total = ph + pa + (pd or 0)
    if total <= 0:
        return None, None, None

    p_home = ph / total
    p_away = pa / total
    p_draw = (pd / total) if pd else None
    return p_home, p_away, p_draw

def compute_edge(proba_reelle, cote_bookmaker):
    """Edge = ((proba_reelle - proba_bookie) / proba_bookie) * 100"""
    try:
        proba_bookie = implied_prob(cote_bookmaker)
        if not proba_bookie or not proba_reelle:
            return 0.0
        return round(((proba_reelle - proba_bookie) / proba_bookie) * 100, 2)
    except Exception:
        return 0.0

def score_forme_equipe(derniers_matchs, team_name, n=5):
    """
    Score de forme (0.0->1.0) sur les N derniers matchs.
    W=1.0, D=0.5, L=0.0. Pondération exponentielle (récent pèse plus).
    """
    if not derniers_matchs or not team_name:
        return None

    resultats = []
    name_lower = team_name.lower()

    for m in derniers_matchs[-n:]:
        home = (m.get("home") or m.get("home_team") or "").lower()
        away = (m.get("away") or m.get("away_team") or "").lower()
        winner = m.get("winner")

        is_home = name_lower in home
        is_away = name_lower in away
        if not is_home and not is_away:
            continue

        if winner == 3:
            resultats.append(0.5)
        elif (winner == 1 and is_home) or (winner == 2 and is_away):
            resultats.append(1.0)
        else:
            resultats.append(0.0)

    if not resultats:
        return None

    weights = [2 ** i for i in range(len(resultats))]
    score = sum(r * w for r, w in zip(resultats, weights)) / sum(weights)
    return round(score, 3)

def score_h2h(h2h_data, home_team_name):
    """Ratio W/D/L du H2H pour l'équipe domicile."""
    if not h2h_data or not h2h_data.get("matches"):
        return None, None, 0

    home_lower = home_team_name.lower()
    wins_home = 0
    wins_away = 0
    draws = 0

    for m in h2h_data["matches"]:
        h = (m.get("home") or "").lower()
        winner = m.get("winner")
        is_home_playing_home = home_lower in h

        if winner == 3:
            draws += 1
        elif (winner == 1 and is_home_playing_home) or (winner == 2 and not is_home_playing_home):
            wins_home += 1
        else:
            wins_away += 1

    total = wins_home + wins_away + draws
    if total == 0:
        return None, None, 0

    return round(wins_home / total, 3), round(wins_away / total, 3), total

def compute_proba_reelle(
    p_home_fair, p_away_fair,
    forme_home=None, forme_away=None,
    h2h_home=None, h2h_away=None,
    position_home=None, position_away=None,
    nb_equipes=20
):
    """
    Proba réelle ajustée : combinaison pondérée de
    fair odds (50%) + forme (25%) + H2H (15%) + classement (10%).
    """
    if p_home_fair is None or p_away_fair is None:
        return None, None

    W = {"fair": 0.50, "forme": 0.25, "h2h": 0.15, "classement": 0.10}
    sh = p_home_fair * W["fair"]
    sa = p_away_fair * W["fair"]

    if forme_home is not None and forme_away is not None:
        tf = forme_home + forme_away
        if tf > 0:
            sh += (forme_home / tf) * W["forme"]
            sa += (forme_away / tf) * W["forme"]
        else:
            sh += p_home_fair * W["forme"]
            sa += p_away_fair * W["forme"]
    else:
        sh += p_home_fair * W["forme"]
        sa += p_away_fair * W["forme"]

    if h2h_home is not None and h2h_away is not None:
        th = h2h_home + h2h_away
        if th > 0:
            sh += (h2h_home / th) * W["h2h"]
            sa += (h2h_away / th) * W["h2h"]
        else:
            sh += p_home_fair * W["h2h"]
            sa += p_away_fair * W["h2h"]
    else:
        sh += p_home_fair * W["h2h"]
        sa += p_away_fair * W["h2h"]

    if position_home is not None and position_away is not None:
        fh = (nb_equipes + 1 - position_home) / nb_equipes
        fa = (nb_equipes + 1 - position_away) / nb_equipes
        tf2 = fh + fa
        if tf2 > 0:
            sh += (fh / tf2) * W["classement"]
            sa += (fa / tf2) * W["classement"]
        else:
            sh += p_home_fair * W["classement"]
            sa += p_away_fair * W["classement"]
    else:
        sh += p_home_fair * W["classement"]
        sa += p_away_fair * W["classement"]

    total = sh + sa
    if total <= 0:
        return p_home_fair, p_away_fair

    return round(sh / total, 4), round(sa / total, 4)

def get_position_from_standings(standings, team_name):
    """Cherche la position d'une équipe dans le classement."""
    if not standings or not team_name:
        return None
    name_lower = team_name.lower()
    for row in standings:
        if name_lower in (row.get("equipe") or "").lower():
            return row.get("position")
    return None

def calculer_edge_data_driven(match, sport_id=None):
    """
    Moteur principal data-driven.
    Retourne un dict complet avec probas, edge, verdict.
    """
    cotes = match.get("cotes", [])
    if not cotes:
        return {"erreur": "Pas de cotes disponibles", "edge_home": 0, "edge_away": 0}

    cote_obj   = cotes[0]
    home_cote  = cote_obj.get("home_cote")
    away_cote  = cote_obj.get("away_cote")
    draw_cote  = cote_obj.get("draw_cote")
    bookmaker  = cote_obj.get("bookmaker", "?")

    p_home_fair, p_away_fair, p_draw_fair = remove_vig(home_cote, away_cote, draw_cote)
    if p_home_fair is None:
        return {"erreur": "Cotes invalides", "edge_home": 0, "edge_away": 0}

    home_team = match.get("home_team", "")
    away_team = match.get("away_team", "")

    h2h_home = h2h_away = None
    forme_home = forme_away = None
    position_home = position_away = None
    nb_h2h = 0
    event_id_raw = match.get("id", "")

    try:
        numeric_id = int(str(event_id_raw).replace("fs_", ""))

        h2h_data = fetch_sofascore_event_h2h(numeric_id)
        if h2h_data and h2h_data.get("matches"):
            h2h_home, h2h_away, nb_h2h = score_h2h(h2h_data, home_team)

        if sport_id:
            results_home = fetch_sofascore_search_team(home_team)
            results_away = fetch_sofascore_search_team(away_team)
            team_id_home = results_home[0]["id"] if results_home else None
            team_id_away = results_away[0]["id"] if results_away else None

            if team_id_home:
                events_home = fetch_sofascore_team_last_events(team_id_home, pages=1)
                forme_home = score_forme_equipe(events_home, home_team)
            if team_id_away:
                events_away = fetch_sofascore_team_last_events(team_id_away, pages=1)
                forme_away = score_forme_equipe(events_away, away_team)

            tournament_id = SOFASCORE_TOURNAMENTS.get(sport_id)
            if tournament_id:
                season_id = fetch_sofascore_season_id(tournament_id)
                if season_id:
                    standings = fetch_sofascore_tournament_standings(tournament_id, season_id)
                    position_home = get_position_from_standings(standings, home_team)
                    position_away = get_position_from_standings(standings, away_team)

    except Exception as e:
        print(f"Edge data-driven enrichissement erreur: {e}")

    nb_equipes = 30 if sport_id == "nba" else 20
    p_home_real, p_away_real = compute_proba_reelle(
        p_home_fair, p_away_fair,
        forme_home=forme_home, forme_away=forme_away,
        h2h_home=h2h_home, h2h_away=h2h_away,
        position_home=position_home, position_away=position_away,
        nb_equipes=nb_equipes
    )

    edge_home = compute_edge(p_home_real, home_cote)
    edge_away = compute_edge(p_away_real, away_cote)
    edge_draw = compute_edge(p_draw_fair, draw_cote) if draw_cote and p_draw_fair else 0

    best_edge = max(edge_home, edge_away, edge_draw)
    if best_edge == edge_home:
        best_pari = f"Victoire {home_team}"
        best_cote = home_cote
        best_proba = p_home_real
    elif best_edge == edge_away:
        best_pari = f"Victoire {away_team}"
        best_cote = away_cote
        best_proba = p_away_real
    else:
        best_pari = "Match nul"
        best_cote = draw_cote
        best_proba = p_draw_fair

    is_value  = best_edge >= 15.0
    confiance = min(10, max(1, int(5 + (best_edge / 10))))

    return {
        "home_team":         home_team,
        "away_team":         away_team,
        "bookmaker":         bookmaker,
        "p_home_fair":       round(p_home_fair * 100, 1),
        "p_away_fair":       round(p_away_fair * 100, 1),
        "p_draw_fair":       round((p_draw_fair or 0) * 100, 1),
        "p_home_real":       round((p_home_real or p_home_fair) * 100, 1),
        "p_away_real":       round((p_away_real or p_away_fair) * 100, 1),
        "forme_home":        round(forme_home * 100, 1) if forme_home is not None else None,
        "forme_away":        round(forme_away * 100, 1) if forme_away is not None else None,
        "h2h_home_pct":      round((h2h_home or 0) * 100, 1),
        "h2h_away_pct":      round((h2h_away or 0) * 100, 1),
        "h2h_nb_matchs":     nb_h2h,
        "position_home":     position_home,
        "position_away":     position_away,
        "edge_home":         edge_home,
        "edge_away":         edge_away,
        "edge_draw":         edge_draw,
        "best_edge":         best_edge,
        "best_pari":         best_pari,
        "best_cote":         best_cote,
        "best_proba_reelle": round((best_proba or 0) * 100, 1),
        "value_bet":         is_value,
        "confiance":         confiance,
        "risque":            "FAIBLE" if best_edge >= 25 else ("MOYEN" if best_edge >= 15 else "ELEVE"),
    }


# ============================================================
# ENDPOINTS EDGE DATA-DRIVEN
# ============================================================

@app.route('/radar/edge/<sport_id>')
def get_edge_sport(sport_id):
    """
    Edge data-driven pour tous les matchs d'un sport, triés par edge décroissant.
    Ex : /radar/edge/epl  ou  /radar/edge/nba
    """
    if sport_id == "nba":
        matchs = fetch_nba()
    elif sport_id == "euroleague":
        matchs = fetch_euroleague()
    elif sport_id in ["atp", "wta"]:
        matchs = fetch_tennis(sport_id)
    else:
        matchs = fetch_football_sport(sport_id)

    resultats = []
    for match in matchs:
        if not match.get("cotes"):
            continue
        match["sport_key"] = sport_id
        edge_data = calculer_edge_data_driven(match, sport_id=sport_id)
        if "erreur" not in edge_data:
            edge_data["commence_time"] = match.get("commence_time")
            edge_data["match_id"]      = match.get("id")
            resultats.append(edge_data)

    resultats.sort(key=lambda x: x.get("best_edge", 0), reverse=True)
    value_bets = [r for r in resultats if r.get("value_bet")]

    return jsonify({
        "sport":                   sport_id,
        "total_matchs_analyses":   len(resultats),
        "value_bets_detectes":     len(value_bets),
        "resultats":               resultats,
        "timestamp":               datetime.now().strftime("%H:%M:%S")
    })


@app.route('/radar/edge/match/<int:event_id>')
def get_edge_match(event_id):
    """
    Edge data-driven pour un match précis via son event_id SofaScore.
    Ex : /radar/edge/match/12345678?sport=epl
    """
    sport_id = request.args.get("sport", "epl")

    if sport_id == "nba":
        matchs = fetch_nba()
    elif sport_id == "euroleague":
        matchs = fetch_euroleague()
    elif sport_id in ["atp", "wta"]:
        matchs = fetch_tennis(sport_id)
    else:
        matchs = fetch_football_sport(sport_id)

    match = next((m for m in matchs if str(m.get("id")) == str(event_id)), None)

    if not match:
        cotes = fetch_sofascore_odds(event_id)
        if not cotes:
            return jsonify({"erreur": f"Match {event_id} non trouvé"}), 404
        match = {
            "id": event_id,
            "home_team": request.args.get("home", "Home"),
            "away_team": request.args.get("away", "Away"),
            "cotes": cotes,
            "sport_key": sport_id
        }

    edge_data = calculer_edge_data_driven(match, sport_id=sport_id)
    return jsonify({"event_id": event_id, "edge": edge_data})


@app.route('/radar/edge/scan-all', methods=['POST'])
def scan_edge_all():
    """
    Scan complet data-driven sur tous les sports.
    Retourne uniquement les value bets (edge >= 15%), sauvegardés en Redis.
    """
    sports = [
        ("epl",        lambda: fetch_football_sport("epl")),
        ("laliga",     lambda: fetch_football_sport("laliga")),
        ("bundesliga", lambda: fetch_football_sport("bundesliga")),
        ("ligue1",     lambda: fetch_football_sport("ligue1")),
        ("seriea",     lambda: fetch_football_sport("seriea")),
        ("ucl",        lambda: fetch_football_sport("ucl")),
        ("nba",        fetch_nba),
        ("euroleague", fetch_euroleague),
        ("atp",        lambda: fetch_tennis("atp")),
        ("wta",        lambda: fetch_tennis("wta")),
    ]

    tous_value_bets = []
    resume_par_sport = {}

    for sport_id, fetch_fn in sports:
        try:
            matchs = fetch_fn()
            matchs_avec_cotes = [m for m in matchs if m.get("cotes")]
            analyses = []
            for match in matchs_avec_cotes:
                match["sport_key"] = sport_id
                edge = calculer_edge_data_driven(match, sport_id=sport_id)
                if "erreur" not in edge:
                    edge["sport"]         = sport_id
                    edge["commence_time"] = match.get("commence_time")
                    edge["match_id"]      = match.get("id")
                    analyses.append(edge)
                    if edge.get("value_bet"):
                        tous_value_bets.append(edge)
                time.sleep(0.3)

            resume_par_sport[sport_id] = {
                "matchs_analyses": len(analyses),
                "value_bets":      len([a for a in analyses if a.get("value_bet")]),
                "meilleur_edge":   max((a.get("best_edge", 0) for a in analyses), default=0)
            }
        except Exception as e:
            print(f"scan_edge_all erreur {sport_id}: {e}")
            resume_par_sport[sport_id] = {"erreur": str(e)}

    tous_value_bets.sort(key=lambda x: x.get("best_edge", 0), reverse=True)

    redis_set("edge_scan_last", {
        "timestamp":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "value_bets":  tous_value_bets[:20],
        "resume":      resume_par_sport
    }, ex=3600)

    if tous_value_bets:
        top = tous_value_bets[0]
        envoyer_notif_ntfy(
            titre=f"EDGE SCAN — {len(tous_value_bets)} value bet(s)",
            message=(
                f"TOP : {top.get('best_pari')} | Cote {top.get('best_cote')} | "
                f"Edge {top.get('best_edge')}% | {top.get('sport','?').upper()}"
            ),
            priorite="high",
            tags="dart,moneybag"
        )

    return jsonify({
        "status":            "Scan terminé",
        "total_value_bets":  len(tous_value_bets),
        "value_bets":        tous_value_bets,
        "resume_par_sport":  resume_par_sport,
        "timestamp":         datetime.now().strftime("%H:%M:%S")
    })


@app.route('/radar/edge/last-scan')
def get_last_edge_scan():
    """Résultat du dernier scan edge depuis Redis."""
    data = redis_get("edge_scan_last")
    if not data:
        return jsonify({"erreur": "Aucun scan, lance POST /radar/edge/scan-all"}), 404
    return jsonify(data)


# ============================================================
# ANALYSE IA
# ============================================================
def analyze_with_claude(match):
    if not GROQ_API_KEY:
        print("GROQ_API_KEY manquante !")
        return None

    meteo = get_weather_for_match(match)
    meteo_str = f"{meteo['temp']}°C, {meteo['conditions']}, vent {meteo['vent']}m/s" if meteo else "Salle / non applicable"

    cotes_summary = ""
    for c in match.get('cotes', [])[:3]:
        cotes_summary += f"\n- {c.get('bookmaker','?')}: Home {c.get('home_cote','-')} | Away {c.get('away_cote','-')}"
        if c.get('draw_cote'):
            cotes_summary += f" | Draw {c['draw_cote']}"
        if c.get('over_under'):
            cotes_summary += f" | O/U {c['over_under']}"

    score_str = ""
    if match.get('status') == 'in_progress':
        score_str = f"\nScore actuel: {match.get('score_home',0)}-{match.get('score_away',0)} (EN DIRECT)"

    # Enrichissement SofaScore : H2H + stats + forme si event_id dispo
    h2h_str = ""
    stats_str = ""
    forme_str = ""
    event_id = match.get("id", "")
    try:
        numeric_id = int(str(event_id).replace("fs_", ""))
        h2h_data = fetch_sofascore_event_h2h(numeric_id)
        if h2h_data and h2h_data.get("matches"):
            h2h_str = (
                f"\nH2H ({h2h_data.get('homeWins',0)}W-{h2h_data.get('draws',0)}D-{h2h_data.get('awayWins',0)}L) : "
                + " | ".join([f"{m['home']} {m['score']} {m['away']} ({m['date']})" for m in h2h_data["matches"][:5]])
            )
        stats_data = fetch_sofascore_event_statistics(numeric_id)
        if stats_data and stats_data.get("home"):
            h = stats_data["home"]
            a = stats_data["away"]
            stats_str = (
                f"\nStats (Dom/Ext) — Possession: {h.get('ball_possession','?')}%/{a.get('ball_possession','?')}% | "
                f"Tirs: {h.get('shots_on_target','?')}/{a.get('shots_on_target','?')} | "
                f"xG: {h.get('expected_goals','?')}/{a.get('expected_goals','?')}"
            )
    except Exception:
        pass

    prompt = f"""Tu es un expert en paris sportifs. Analyse ce match et réponds UNIQUEMENT en JSON valide sans aucun texte autour.

Match: {match.get('away_team')} @ {match.get('home_team')}
Sport: {match.get('sport_title')}
Heure: {match.get('commence_time')}
{f"Météo: {meteo_str}" if meteo_str != "Salle / non applicable" else "Météo: Irrelevante (sport en salle)"}{score_str}{h2h_str}{stats_str}
Cotes: {cotes_summary if cotes_summary else 'Non disponibles'}

REGLES STRICTES :
- value_bet = true UNIQUEMENT si edge >= 15% (formule : ((proba_reelle - proba_bookmaker) / proba_bookmaker) x 100)
- Si pas de cotes disponibles : value_bet = false obligatoirement
- confiance entre 1 et 10, jamais inventer une cote
- Ne jamais mettre value_bet = true si tu n as pas de vraies cotes

Réponds UNIQUEMENT avec ce JSON (value_bet est false par défaut) :
{{
  "value_bet": false,
  "edge_pct": 0,
  "confiance": 5,
  "pari_recommande": "conseil court ou AUCUN si pas de value",
  "cote": null,
  "bookmaker": null,
  "raison": "explication courte et honnete",
  "risque": "FAIBLE ou MOYEN ou ELEVE",
  "impact_meteo": "aucun",
  "mise_conseillee": "0% - pas de value bet"
}}"""

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "max_tokens": 800
            },
            timeout=12
        )
        res = r.json()
        return json.loads(res['choices'][0]['message']['content'])
    except Exception as e:
        print(f"Erreur Groq : {e}")
        return None

# ============================================================
# SCAN VALUE BETS
# ============================================================
def scan_value_bets():
    global ALERTES
    nouvelles_alertes = []
    ids_deja_alertes = {a.get('match_id') for a in ALERTES if a.get('match_id')}

    sports_a_scanner = [
        ("nba",        fetch_nba),
        ("euroleague", fetch_euroleague),
        ("epl",        lambda: fetch_football_sport("epl")),
        ("laliga",     lambda: fetch_football_sport("laliga")),
        ("ucl",        lambda: fetch_football_sport("ucl")),
        ("atp",        lambda: fetch_tennis("atp")),
        ("wta",        lambda: fetch_tennis("wta")),
    ]

    for sport_id, fetch_fn in sports_a_scanner:
        try:
            matchs = fetch_fn()
            for match in matchs[:2]:
                if not match.get('cotes'):
                    continue
                match_id = str(match.get('id', ''))
                if match_id and match_id in ids_deja_alertes:
                    print(f"Skip doublon: {match_id}")
                    continue
                analyse = analyze_with_claude(match)
                if not analyse:
                    continue
                if analyse.get('value_bet') and analyse.get('confiance', 0) >= 7 and analyse.get('edge_pct', 0) >= 15:
                    match_label = f"{match.get('away_team')} @ {match.get('home_team')}"
                    alerte = {
                        "id": f"{match.get('id')}_{int(time.time())}",
                        "match_id": str(match.get('id', '')),
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "match": match_label,
                        "sport": match.get('sport_title'),
                        "confiance": analyse.get('confiance'),
                        "pari": analyse.get('pari_recommande'),
                        "cote": analyse.get('cote'),
                        "bookmaker": analyse.get('bookmaker'),
                        "raison": analyse.get('raison'),
                        "risque": analyse.get('risque'),
                        "impact_meteo": analyse.get('impact_meteo', 'aucun')
                    }
                    nouvelles_alertes.append(alerte)
                    notif_msg = (
                        f"Match : {match_label}\n"
                        f"Pari : {analyse.get('pari_recommande', '?')}\n"
                        f"Cote : {analyse.get('cote', '?')} sur {analyse.get('bookmaker', '?')}\n"
                        f"Confiance : {analyse.get('confiance', '?')}/10\n"
                        f"Risque : {analyse.get('risque', '?')}"
                    )
                    envoyer_notif_ntfy(
                        titre=f"VALUE BET {sport_id.upper()}",
                        message=notif_msg,
                        priorite="urgent",
                        tags="rotating_light,moneybag"
                    )
                time.sleep(1)
        except Exception as e:
            print(f"Erreur scan {sport_id} : {e}")

    ALERTES = (nouvelles_alertes + ALERTES)[:20]
    print(f"Scan terminé : {len(nouvelles_alertes)} value bets détectés")

# ============================================================
# RÉSUMÉ QUOTIDIEN
# ============================================================
def generate_daily_resume():
    tous_matchs = []
    tous_matchs.extend(fetch_nba()[:2])
    tous_matchs.extend(fetch_euroleague()[:2])
    for sid in ["epl", "ucl", "laliga"]:
        tous_matchs.extend(fetch_football_sport(sid)[:2])

    if not tous_matchs:
        return None

    matchs_str = "\n".join([
        f"- {m.get('sport_title', '')} : {m.get('away_team')} @ {m.get('home_team')} | {m.get('commence_time')}"
        for m in tous_matchs
    ])

    prompt = f"""Tu es un expert en paris sportifs. Matchs disponibles aujourd'hui :
{matchs_str}

Réponds UNIQUEMENT en JSON :
{{
  "date": "{datetime.now().strftime('%d/%m/%Y')}",
  "resume_general": "2-3 phrases sur la journée sportive",
  "top_matchs": [
    {{
      "match": "Equipe1 vs Equipe2",
      "sport": "NBA",
      "raison": "pourquoi ce match est interessant",
      "pari_suggere": "pari recommande",
      "niveau_interet": 4
    }}
  ],
  "conseil_du_jour": "un conseil general",
  "sports_chauds": ["NBA", "EPL"]
}}"""

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "max_tokens": 1000
            },
            timeout=15
        )
        res = r.json()
        resume = json.loads(res['choices'][0]['message']['content'])
        redis_set('resume', resume)
        return resume
    except Exception as e:
        print(f"Erreur résumé Groq : {e}")
        return None


# ============================================================
# BACKTESTING — Moteur complet sur données historiques SofaScore
# ============================================================

def fetch_sofascore_tournament_events_by_page(tournament_id, season_id, page=0):
    """Matchs terminés d'un tournoi/saison, page par page (~10 matchs/page)."""
    cache_key = f"sofa_t_events_{tournament_id}_{season_id}_{page}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached
    try:
        url = f"https://api.sofascore.com/api/v1/tournament/{tournament_id}/season/{season_id}/events/last/{page}"
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        result = []
        for event in data.get("events", []):
            status = event.get("status", {}).get("type", "")
            if status not in ["finished", "canceled"]:
                continue
            start_ts = event.get("startTimestamp", 0)
            result.append({
                "event_id":   event.get("id"),
                "date":       datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d") if start_ts else "",
                "home_team":  event.get("homeTeam", {}).get("name", "?"),
                "away_team":  event.get("awayTeam", {}).get("name", "?"),
                "home_id":    event.get("homeTeam", {}).get("id"),
                "away_id":    event.get("awayTeam", {}).get("id"),
                "score_home": event.get("homeScore", {}).get("current"),
                "score_away": event.get("awayScore", {}).get("current"),
                "winner":     event.get("winnerCode"),
            })
        if result:
            set_cache(cache_key, result)
        return result
    except Exception as e:
        print(f"SofaScore tournament events erreur: {e}")
        return []


def fetch_historical_events(tournament_id, season_id, nb_pages=5):
    """Récupère nb_pages*10 matchs historiques d'un tournoi."""
    all_events = []
    for page in range(nb_pages):
        events = fetch_sofascore_tournament_events_by_page(tournament_id, season_id, page)
        if not events:
            break
        all_events.extend(events)
        time.sleep(0.2)
    return all_events


def backtest_match(event, sport_id):
    """Rejoue le moteur d'edge sur un match passé et compare au vrai résultat."""
    event_id  = event.get("event_id")
    home_team = event.get("home_team", "")
    away_team = event.get("away_team", "")
    winner    = event.get("winner")
    if not event_id or winner is None:
        return None

    cotes = fetch_sofascore_odds(event_id)
    if not cotes:
        return None
    cote_obj  = cotes[0]
    home_cote = cote_obj.get("home_cote")
    away_cote = cote_obj.get("away_cote")
    draw_cote = cote_obj.get("draw_cote")
    if not home_cote or not away_cote:
        return None

    p_home_fair, p_away_fair, p_draw_fair = remove_vig(home_cote, away_cote, draw_cote)
    if p_home_fair is None:
        return None

    h2h_home, h2h_away, nb_h2h = None, None, 0
    h2h_data = fetch_sofascore_event_h2h(event_id)
    if h2h_data and h2h_data.get("matches"):
        h2h_home, h2h_away, nb_h2h = score_h2h(h2h_data, home_team)

    p_home_real, p_away_real = compute_proba_reelle(
        p_home_fair, p_away_fair,
        h2h_home=h2h_home, h2h_away=h2h_away,
    )

    edge_home = compute_edge(p_home_real, home_cote)
    edge_away = compute_edge(p_away_real, away_cote)
    edge_draw = compute_edge(p_draw_fair, draw_cote) if draw_cote and p_draw_fair else 0

    best_edge = max(edge_home, edge_away, edge_draw)
    if best_edge == edge_home:
        prediction, best_cote = 1, home_cote
    elif best_edge == edge_away:
        prediction, best_cote = 2, away_cote
    else:
        prediction, best_cote = 3, (draw_cote or 0)

    is_value = best_edge >= 15.0
    correct  = (prediction == winner)
    gain_net = round(best_cote - 1, 3) if correct else -1.0

    return {
        "event_id":    event_id,
        "date":        event.get("date"),
        "match":       f"{home_team} vs {away_team}",
        "score":       f"{event.get('score_home')}-{event.get('score_away')}",
        "cote_home":   home_cote,
        "cote_away":   away_cote,
        "cote_draw":   draw_cote,
        "p_home_fair": round(p_home_fair * 100, 1),
        "p_away_fair": round(p_away_fair * 100, 1),
        "p_home_real": round((p_home_real or p_home_fair) * 100, 1),
        "p_away_real": round((p_away_real or p_away_fair) * 100, 1),
        "edge_home":   edge_home,
        "edge_away":   edge_away,
        "edge_draw":   edge_draw,
        "best_edge":   best_edge,
        "prediction":  prediction,
        "winner_reel": winner,
        "value_bet":   is_value,
        "correct":     correct,
        "gain_net":    gain_net if is_value else None,
        "h2h_matchs":  nb_h2h,
    }


def compute_backtest_stats(resultats):
    """Statistiques globales d'un backtest : ROI, précision, courbe bankroll."""
    if not resultats:
        return {}
    total         = len(resultats)
    value_bets    = [r for r in resultats if r.get("value_bet")]
    nb_vb         = len(value_bets)
    nb_correct_vb = len([r for r in value_bets if r.get("correct")])
    nb_correct_all= len([r for r in resultats if r.get("correct")])
    gains         = [r.get("gain_net", 0) for r in value_bets if r.get("gain_net") is not None]
    roi           = round(sum(gains) / nb_vb * 100, 2) if nb_vb > 0 else 0
    profit        = round(sum(gains), 3)

    bankroll_curve = []
    solde = 0
    for r in sorted(value_bets, key=lambda x: x.get("date", "")):
        solde += r.get("gain_net", 0)
        bankroll_curve.append({"date": r.get("date"), "match": r.get("match"), "gain": r.get("gain_net"), "solde": round(solde, 3)})

    edges = [r.get("best_edge", 0) for r in value_bets]
    edge_moyen = round(sum(edges) / len(edges), 2) if edges else 0

    tranches = {"15-20%": [], "20-25%": [], "25-30%": [], "30%+": []}
    for r in value_bets:
        e = r.get("best_edge", 0)
        if 15 <= e < 20:   tranches["15-20%"].append(r)
        elif 20 <= e < 25: tranches["20-25%"].append(r)
        elif 25 <= e < 30: tranches["25-30%"].append(r)
        elif e >= 30:      tranches["30%+"].append(r)

    precision_par_tranche = {}
    for label, items in tranches.items():
        if items:
            ok = len([i for i in items if i.get("correct")])
            precision_par_tranche[label] = {
                "nb": len(items), "corrects": ok,
                "precision": round(ok / len(items) * 100, 1),
                "roi": round(sum(i.get("gain_net", 0) for i in items) / len(items) * 100, 2)
            }

    return {
        "total_matchs":          total,
        "value_bets":            nb_vb,
        "taux_value_bets":       round(nb_vb / total * 100, 1) if total > 0 else 0,
        "precision_value_bets":  round(nb_correct_vb / nb_vb * 100, 1) if nb_vb > 0 else 0,
        "precision_globale":     round(nb_correct_all / total * 100, 1) if total > 0 else 0,
        "roi_pct":               roi,
        "profit_total":          profit,
        "edge_moyen":            edge_moyen,
        "precision_par_tranche": precision_par_tranche,
        "bankroll_curve":        bankroll_curve,
        "meilleur_edge":         round(max(edges), 2) if edges else 0,
        "verdict": "PROFITABLE" if roi > 0 else ("A L'EQUILIBRE" if roi > -5 else "DEFICITAIRE"),
    }


# ============================================================
# ENDPOINTS BACKTESTING
# ============================================================

@app.route("/radar/backtest/<sport_id>")
def run_backtest(sport_id):
    """Backtest complet sur la saison courante. Ex: /radar/backtest/epl?pages=10"""
    nb_pages = int(request.args.get("pages", 5))
    tournament_id = SOFASCORE_TOURNAMENTS.get(sport_id)
    if not tournament_id:
        return jsonify({"erreur": f"Sport '{sport_id}' non supporté"}), 400
    season_id = fetch_sofascore_season_id(tournament_id)
    if not season_id:
        return jsonify({"erreur": "Saison courante introuvable"}), 404

    events = fetch_historical_events(tournament_id, season_id, nb_pages=nb_pages)
    if not events:
        return jsonify({"erreur": "Aucun match historique"}), 404

    resultats = []
    for event in events:
        try:
            r = backtest_match(event, sport_id)
            if r:
                resultats.append(r)
            time.sleep(0.15)
        except Exception as e:
            print(f"Backtest erreur {event.get('event_id')}: {e}")

    if not resultats:
        return jsonify({"erreur": "Aucun match avec cotes"}), 404

    stats = compute_backtest_stats(resultats)
    redis_set(f"backtest_{sport_id}", {
        "sport": sport_id, "season_id": season_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stats": stats, "nb_matchs": len(resultats),
    }, ex=86400)

    return jsonify({"sport": sport_id, "season_id": season_id,
                    "nb_matchs": len(resultats), "stats": stats,
                    "detail": resultats, "timestamp": datetime.now().strftime("%H:%M:%S")})


@app.route("/radar/backtest/<sport_id>/last")
def get_last_backtest(sport_id):
    """Dernier backtest sauvegardé en Redis."""
    data = redis_get(f"backtest_{sport_id}")
    if not data:
        return jsonify({"erreur": f"Lance d'abord /radar/backtest/{sport_id}"}), 404
    return jsonify(data)


@app.route("/radar/backtest/all")
def run_backtest_all():
    """Résumé de tous les derniers backtests depuis Redis."""
    sports = ["epl", "laliga", "bundesliga", "ligue1", "seriea", "ucl", "nba", "euroleague"]
    resume = {}
    for sport_id in sports:
        data = redis_get(f"backtest_{sport_id}")
        if data:
            resume[sport_id] = {
                "timestamp": data.get("timestamp"), "nb_matchs": data.get("nb_matchs"),
                "roi_pct": data.get("stats", {}).get("roi_pct"),
                "value_bets": data.get("stats", {}).get("value_bets"),
                "precision": data.get("stats", {}).get("precision_value_bets"),
                "verdict": data.get("stats", {}).get("verdict"),
            }
        else:
            resume[sport_id] = {"erreur": "Pas de backtest"}
    return jsonify({"resume": resume, "timestamp": datetime.now().strftime("%H:%M:%S")})


@app.route("/radar/backtest/<sport_id>/calibrate")
def calibrate_weights(sport_id):
    """
    Teste 8 combinaisons de poids et retourne celle qui maximise le ROI.
    Ex: /radar/backtest/epl/calibrate
    """
    tournament_id = SOFASCORE_TOURNAMENTS.get(sport_id)
    season_id_data = redis_get(f"backtest_{sport_id}")
    season_id = season_id_data.get("season_id") if season_id_data else fetch_sofascore_season_id(tournament_id)
    if not tournament_id or not season_id:
        return jsonify({"erreur": "Lance d'abord /radar/backtest/" + sport_id}), 400

    events_raw = fetch_historical_events(tournament_id, season_id, nb_pages=5)
    configs = [
        {"label": "Défaut 50/25/15/10",  "fair": 0.50, "forme": 0.25, "h2h": 0.15, "classement": 0.10},
        {"label": "Cotes lourdes 60/20", "fair": 0.60, "forme": 0.20, "h2h": 0.15, "classement": 0.05},
        {"label": "Cotes max 70/15",     "fair": 0.70, "forme": 0.15, "h2h": 0.10, "classement": 0.05},
        {"label": "Forme forte 55/30",   "fair": 0.55, "forme": 0.30, "h2h": 0.10, "classement": 0.05},
        {"label": "H2H fort 50/30/20",   "fair": 0.50, "forme": 0.30, "h2h": 0.20, "classement": 0.00},
        {"label": "Équilibré 65/25/10",  "fair": 0.65, "forme": 0.25, "h2h": 0.10, "classement": 0.00},
        {"label": "Minimal 80/10/10",    "fair": 0.80, "forme": 0.10, "h2h": 0.10, "classement": 0.00},
        {"label": "Baseline pure cotes", "fair": 1.00, "forme": 0.00, "h2h": 0.00, "classement": 0.00},
    ]

    resultats_configs = []
    for cfg in configs:
        gains_vb, nb_correct, nb_vb = [], 0, 0
        for event in events_raw:
            event_id = event.get("event_id")
            winner   = event.get("winner")
            if not event_id or winner is None:
                continue
            cotes = fetch_sofascore_odds(event_id)
            if not cotes:
                continue
            c = cotes[0]
            hc, ac, dc = c.get("home_cote"), c.get("away_cote"), c.get("draw_cote")
            if not hc or not ac:
                continue
            ph, pa, pd = remove_vig(hc, ac, dc)
            if ph is None:
                continue
            h2h_d = fetch_sofascore_event_h2h(event_id)
            hh, ha = ph, pa
            if h2h_d and h2h_d.get("matches"):
                h2h_h, h2h_a, _ = score_h2h(h2h_d, event.get("home_team", ""))
                if h2h_h is not None and h2h_a is not None:
                    t = h2h_h + h2h_a
                    if t > 0:
                        hh, ha = h2h_h / t, h2h_a / t
            sh = ph * cfg["fair"] + ph * cfg["forme"] + hh * cfg["h2h"] + ph * cfg["classement"]
            sa = pa * cfg["fair"] + pa * cfg["forme"] + ha * cfg["h2h"] + pa * cfg["classement"]
            tt = sh + sa
            if tt <= 0:
                continue
            p_hr, p_ar = sh / tt, sa / tt
            eh = compute_edge(p_hr, hc)
            ea = compute_edge(p_ar, ac)
            ed = compute_edge(pd, dc) if dc and pd else 0
            best = max(eh, ea, ed)
            if best < 15.0:
                continue
            nb_vb += 1
            pred = 1 if best == eh else (2 if best == ea else 3)
            cu = hc if pred == 1 else (ac if pred == 2 else dc)
            correct = (pred == winner)
            if correct:
                nb_correct += 1
                gains_vb.append((cu or 1) - 1)
            else:
                gains_vb.append(-1.0)

        roi = round(sum(gains_vb) / nb_vb * 100, 2) if nb_vb > 0 else 0
        resultats_configs.append({
            "label": cfg["label"], "poids": cfg,
            "nb_value_bets": nb_vb,
            "precision": round(nb_correct / nb_vb * 100, 1) if nb_vb > 0 else 0,
            "roi_pct": roi,
            "profit": round(sum(gains_vb), 3) if gains_vb else 0,
        })

    resultats_configs.sort(key=lambda x: x.get("roi_pct", 0), reverse=True)
    return jsonify({
        "sport": sport_id,
        "meilleure_config": resultats_configs[0] if resultats_configs else None,
        "toutes_configs": resultats_configs,
        "timestamp": datetime.now().strftime("%H:%M:%S")
    })


# ============================================================
# ENDPOINTS SANTÉ
# ============================================================
@app.route('/')
def health():
    return "RADAR V6 : SYSTEM READY 📡🏀🏒⚾🏈🥊⚽🎾"

@app.route('/health')
def health_check():
    status = {
        "status": "ok",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "apis": {
            "groq":        "ok" if GROQ_API_KEY else "MANQUANTE",
            "cerebras":    "ok" if CEREBRAS_API_KEY else "non configuré",
            "openweather": "ok" if OPENWEATHER_KEY else "MANQUANTE",
            "redis":       "ok" if (UPSTASH_URL and UPSTASH_TOKEN) else "MANQUANTE",
            "sofascore":          "ok (no key needed)",
            "sofascore_live":     "ok - /radar/sofascore/live",
            "sofascore_stats":    "ok - /radar/sofascore/event/{id}/stats",
            "sofascore_h2h":      "ok - /radar/sofascore/event/{id}/h2h",
            "sofascore_lineups":  "ok - /radar/sofascore/event/{id}/lineups",
            "sofascore_form":     "ok - /radar/sofascore/team/{id}/form",
            "sofascore_standings":"ok - /radar/sofascore/standings/{tournament_id}",
            "sofascore_search":   "ok - /radar/sofascore/team/search?q=Arsenal",
            "flashscore":         "ok (no key needed)",
            "espn":               "ok (no key needed)",
            "thesportsdb":        "ok (public key)",
        },
        "cache_size": len(CACHE),
        "alertes_actives": len(ALERTES),
        "chat_history_msgs": len(CHAT_HISTORY),
    }
    apis_manquantes = [k for k, v in status["apis"].items() if v == "MANQUANTE"]
    if apis_manquantes:
        status["warnings"] = f"APIs manquantes: {', '.join(apis_manquantes)}"
    return jsonify(status)

@app.route('/test')
def test():
    today = datetime.now().strftime("%Y-%m-%d")
    results = {}

    # Test Flashscore live
    try:
        r = requests.get("https://d.flashscore.com/x/feed/f_1_0_1_en_1", headers=FLASHSCORE_HEADERS, timeout=8)
        results["flashscore_live"] = {"status": r.status_code, "bytes": len(r.text)}
    except Exception as e:
        results["flashscore_live"] = {"erreur": str(e)}

    # Test SofaScore football
    try:
        r = requests.get(
            f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{today}",
            headers=SOFASCORE_HEADERS, timeout=8
        )
        events = r.json().get('events', [])
        results["sofascore_football"] = {"status": r.status_code, "total_events": len(events)}
    except Exception as e:
        results["sofascore_football"] = {"erreur": str(e)}

    # Test SofaScore tennis
    try:
        r = requests.get(
            f"https://api.sofascore.com/api/v1/sport/tennis/scheduled-events/{today}",
            headers=SOFASCORE_HEADERS, timeout=8
        )
        events = r.json().get('events', [])
        atp = [e for e in events if e.get("tournament", {}).get("category", {}).get("id") == 3]
        wta = [e for e in events if e.get("tournament", {}).get("category", {}).get("id") == 6]
        results["sofascore_tennis"] = {"status": r.status_code, "atp": len(atp), "wta": len(wta)}
    except Exception as e:
        results["sofascore_tennis"] = {"erreur": str(e)}

    # Test ESPN NBA
    try:
        matchs = fetch_espn_nba()
        results["espn_nba"] = {"matchs": len(matchs), "avec_cotes": len([m for m in matchs if m.get('cotes')])}
    except Exception as e:
        results["espn_nba"] = {"erreur": str(e)}

    # Test ESPN Euroleague
    try:
        matchs = fetch_espn_euroleague()
        results["espn_euroleague"] = {"matchs": len(matchs)}
    except Exception as e:
        results["espn_euroleague"] = {"erreur": str(e)}

    # Test TheSportsDB
    try:
        r = requests.get(
            f"https://www.thesportsdb.com/api/v1/json/3/eventsnextleague.php",
            params={"id": "4328"}, timeout=8
        )
        events = r.json().get('events') or []
        results["thesportsdb_epl"] = {"status": r.status_code, "matchs": len(events)}
    except Exception as e:
        results["thesportsdb_epl"] = {"erreur": str(e)}

    return jsonify(results)

@app.route('/test/live')
def test_live():
    try:
        matchs = fetch_flashscore_live()
        return jsonify({"total_live": len(matchs), "matchs": matchs})
    except Exception as e:
        return jsonify({"erreur": str(e)})

# ============================================================
# ENDPOINT LIVE GLOBAL
# ============================================================
@app.route('/radar/live')
def get_live():
    matchs = fetch_flashscore_live()
    return jsonify({
        "data": matchs,
        "source": "flashscore",
        "count": len(matchs),
        "timestamp": datetime.now().strftime("%H:%M:%S")
    })

# ============================================================
# ENDPOINTS RADAR — route principale
# ============================================================
@app.route('/radar/<sport_id>')
def get_sport(sport_id):
    # NBA
    if sport_id == "nba":
        return jsonify({"data": fetch_nba(), "source": "sofascore+espn+thesportsdb"})

    # Basket européen
    elif sport_id == "euroleague":
        return jsonify({"data": fetch_euroleague(), "source": "sofascore+espn+thesportsdb"})
    elif sport_id == "eurocup":
        return jsonify({"data": fetch_eurocup(), "source": "sofascore+thesportsdb"})
    elif sport_id == "proA":
        return jsonify({"data": fetch_proA(), "source": "sofascore+thesportsdb"})

    # Tennis
    elif sport_id in ["atp", "wta"]:
        return jsonify({"data": fetch_tennis(sport_id), "source": "sofascore+thesportsdb"})

    # Football + autres
    elif sport_id in SOFASCORE_TOURNAMENTS or sport_id in SPORTSDB_LEAGUES:
        matchs = fetch_football_sport(sport_id)
        return jsonify({"data": matchs, "source": "sofascore+flashscore+thesportsdb"})

    else:
        return jsonify({"data": [], "error": f"Sport '{sport_id}' non supporté"})

@app.route('/radar/sofascore/<sport_id>')
def get_sofascore(sport_id):
    matchs = fetch_sofascore_events(sport_id)
    return jsonify({"data": matchs, "source": "sofascore", "count": len(matchs)})

@app.route('/radar/flashscore/<sport_id>')
def get_flashscore(sport_id):
    matchs = fetch_flashscore_sport(sport_id)
    return jsonify({"data": matchs, "source": "flashscore", "count": len(matchs)})

@app.route('/radar/espn/nba')
def get_espn_nba():
    matchs = fetch_espn_nba()
    return jsonify({"data": matchs, "source": "espn", "count": len(matchs)})

@app.route('/radar/espn/euroleague')
def get_espn_euroleague():
    matchs = fetch_espn_euroleague()
    return jsonify({"data": matchs, "source": "espn", "count": len(matchs)})

# ============================================================
# ENDPOINTS ANALYSE
# ============================================================
@app.route('/radar/analyze', methods=['POST'])
def analyze_match():
    data = request.get_json()
    match = data.get('match')
    if not match:
        return jsonify({"error": "No data"}), 400

    analyse = analyze_with_claude(match)
    if not analyse:
        return jsonify({"error": "Erreur IA"}), 500

    save_historique({
        "id": f"{match.get('id', '')}_{int(time.time())}",
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "match": f"{match.get('away_team', '')} @ {match.get('home_team', '')}",
        "sport": match.get('sport_title', match.get('sport_key', '')),
        "value_bet": analyse.get('value_bet'),
        "confiance": analyse.get('confiance'),
        "pari": analyse.get('pari_recommande'),
        "cote": analyse.get('cote'),
        "bookmaker": analyse.get('bookmaker'),
        "raison": analyse.get('raison'),
        "impact_meteo": analyse.get('impact_meteo', 'aucun'),
        "resultat": None
    })

    return jsonify({"analyse": analyse})

# ============================================================
# ENDPOINTS ALERTES
# ============================================================
@app.route('/radar/alertes')
def get_alertes():
    return jsonify({"alertes": ALERTES, "last_scan": datetime.now().strftime("%H:%M:%S")})

@app.route('/radar/scan', methods=['POST'])
def trigger_scan():
    scan_value_bets()
    return jsonify({"status": "Scan terminé", "alertes": len(ALERTES)})

@app.route('/radar/cache/clear', methods=['POST'])
def clear_cache():
    global CACHE
    cleared = len(CACHE)
    CACHE = {}
    try:
        cleared_redis = 0
        redis_keys = [
            "sportsdb_4966", "sportsdb_4967", "sportsdb_4387",
            "sportsdb_4328", "sportsdb_4335", "sportsdb_4331",
            "sportsdb_4334", "sportsdb_4332", "sportsdb_4480",
            "sportsdb_4424", "sportsdb_4425", "sportsdb_4422",
            "sport_nba", "sport_euroleague", "sport_eurocup", "sport_proA",
            "sport_epl", "sport_laliga", "sport_bundesliga",
            "sport_ligue1", "sport_seriea", "sport_ucl",
            "sport_atp", "sport_wta", "sport_amicaux", "sport_nations",
            "sofascore_nba", "sofascore_epl", "sofascore_laliga",
            "sofascore_bundesliga", "sofascore_ligue1", "sofascore_seriea",
            "sofascore_ucl", "sofascore_euroleague", "sofascore_eurocup",
            "sofascore_proA", "sofascore_atp", "sofascore_wta",
            "espn_nba", "espn_euroleague",
            "flashscore_live_all",
        ]
        for key in redis_keys:
            requests.delete(
                f"{UPSTASH_URL}/del/{key}",
                headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                timeout=3
            )
            cleared_redis += 1
    except Exception as e:
        print(f"Erreur clear Redis: {e}")
    return jsonify({"status": "Cache vidé ✅", "local": cleared, "redis": cleared_redis})

# ============================================================
# ENDPOINTS HISTORIQUE
# ============================================================
def load_historique():
    data = redis_get('historique')
    return data if data else []

def save_historique(entry):
    historique = load_historique()
    historique.insert(0, entry)
    historique = historique[:100]
    redis_set('historique', historique)

@app.route('/radar/historique')
def get_historique():
    historique = load_historique()
    total = len(historique)
    wins = [h for h in historique if h.get('resultat') == 'WIN']
    confiance_moy = round(sum(h.get('confiance', 0) for h in historique) / total, 1) if total > 0 else 0
    return jsonify({
        "historique": historique,
        "stats": {
            "total": total,
            "value_bets": len([h for h in historique if h.get('value_bet')]),
            "wins": len(wins),
            "losses": len([h for h in historique if h.get('resultat') == 'LOSS']),
            "confiance_moyenne": confiance_moy
        }
    })

@app.route('/radar/historique/resultat', methods=['POST'])
def update_resultat():
    data = request.get_json()
    historique = load_historique()
    for entry in historique:
        if entry.get('id') == data.get('id'):
            entry['resultat'] = data.get('resultat')
            break
    redis_set('historique', historique)
    return jsonify({"status": "ok"})

# ============================================================
# ENDPOINTS BANKROLL
# ============================================================
def load_bankroll():
    data = redis_get('bankroll')
    return data if data else {"total": 0, "disponible": 0, "mises": []}

def save_bankroll(data):
    redis_set('bankroll', data)

def calculer_mise(bankroll_disponible, confiance):
    pourcentages = {7: 0.02, 8: 0.04, 9: 0.06, 10: 0.08}
    return round(bankroll_disponible * pourcentages.get(confiance, 0.02), 2)

@app.route('/radar/bankroll')
def get_bankroll():
    return jsonify(load_bankroll())

@app.route('/radar/bankroll/init', methods=['POST'])
def init_bankroll():
    data = request.get_json()
    montant = float(data.get('montant', 0))
    bankroll = {"total": montant, "disponible": montant, "mises": []}
    save_bankroll(bankroll)
    return jsonify({"status": "ok", "bankroll": bankroll})

@app.route('/radar/bankroll/miser', methods=['POST'])
def ajouter_mise():
    data = request.get_json()
    bankroll = load_bankroll()
    mise = {
        "id": f"mise_{int(time.time())}",
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "match": data.get('match'),
        "pari": data.get('pari'),
        "cote": float(data.get('cote', 0)),
        "montant": float(data.get('montant', 0)),
        "confiance": int(data.get('confiance', 0)),
        "statut": "EN COURS",
        "gain": None
    }
    bankroll['disponible'] = round(bankroll['disponible'] - mise['montant'], 2)
    bankroll['mises'].insert(0, mise)
    save_bankroll(bankroll)
    return jsonify({"status": "ok", "bankroll": bankroll})

@app.route('/radar/bankroll/resultat', methods=['POST'])
def resultat_mise():
    data = request.get_json()
    bankroll = load_bankroll()
    for mise in bankroll['mises']:
        if mise['id'] == data.get('id'):
            mise['statut'] = data.get('statut')
            if data.get('statut') == 'WIN':
                gain = round(mise['montant'] * mise['cote'] - mise['montant'], 2)
                mise['gain'] = gain
                bankroll['disponible'] = round(bankroll['disponible'] + mise['montant'] + gain, 2)
            else:
                mise['gain'] = -mise['montant']
            break
    save_bankroll(bankroll)
    return jsonify({"status": "ok", "bankroll": bankroll})

@app.route('/radar/bankroll/historique_pnl')
def get_historique_pnl():
    bankroll = load_bankroll()
    mises = [m for m in bankroll.get('mises', []) if m.get('gain') is not None]
    mises.reverse()
    solde = bankroll.get('total', 0)
    points = [{"date": "Départ", "solde": solde, "gain": 0}]
    for mise in mises:
        solde = round(solde + mise['gain'], 2)
        points.append({
            "date": mise['timestamp'],
            "solde": solde,
            "gain": mise['gain'],
            "match": mise['match']
        })
    return jsonify({"points": points, "total_initial": bankroll.get('total', 0)})

# ============================================================
# ENDPOINTS BOOKMAKERS
# ============================================================
def load_bookmakers():
    data = redis_get('bookmakers_soldes')
    if data:
        return data
    return [
        {"nom": "Betclic",  "solde": 14.99},
        {"nom": "Winamax",  "solde": 25.28},
        {"nom": "Mystake",  "solde": 42.48},
        {"nom": "Betify",   "solde": 56.52},
    ]

def save_bookmakers(data):
    redis_set('bookmakers_soldes', data)

@app.route('/radar/bookmakers', methods=['GET'])
def get_bookmakers():
    bks = load_bookmakers()
    total = round(sum(b['solde'] for b in bks), 2)
    return jsonify({"bookmakers": bks, "total": total})

@app.route('/radar/bookmakers/update', methods=['POST'])
def update_bookmaker():
    data = request.get_json()
    nom = data.get('nom', '').strip()
    solde = float(data.get('solde', 0))
    if not nom:
        return jsonify({"error": "Nom manquant"}), 400
    bks = load_bookmakers()
    for b in bks:
        if b['nom'].lower() == nom.lower():
            b['solde'] = solde
            save_bookmakers(bks)
            return jsonify({"status": "updated", "bookmakers": bks, "total": round(sum(x['solde'] for x in bks), 2)})
    bks.append({"nom": nom, "solde": solde})
    save_bookmakers(bks)
    return jsonify({"status": "added", "bookmakers": bks, "total": round(sum(x['solde'] for x in bks), 2)})

@app.route('/radar/bookmakers/delete', methods=['POST'])
def delete_bookmaker():
    data = request.get_json()
    nom = data.get('nom', '').strip()
    bks = load_bookmakers()
    bks = [b for b in bks if b['nom'].lower() != nom.lower()]
    save_bookmakers(bks)
    total = round(sum(b['solde'] for b in bks), 2)
    return jsonify({"status": "deleted", "bookmakers": bks, "total": total})

# ============================================================
# ENDPOINTS RÉSUMÉ
# ============================================================
@app.route('/radar/resume')
def get_resume():
    data = redis_get('resume')
    if data:
        return jsonify(data)
    return jsonify({"error": "Aucun résumé disponible"})

@app.route('/radar/resume/generate', methods=['POST'])
def generate_resume():
    resume = generate_daily_resume()
    if resume:
        return jsonify({"status": "ok", "resume": resume})
    return jsonify({"error": "Erreur génération"}), 500

# ============================================================
# ENDPOINT CHAT
# ============================================================
@app.route('/radar/chat', methods=['POST'])
def chat():
    global CHAT_HISTORY
    data = request.get_json()
    message = data.get('message')
    image_b64 = data.get('image')
    images_b64 = data.get('images', [])
    if images_b64 and not image_b64:
        image_b64 = images_b64[0]
    extra_images = images_b64[1:3] if len(images_b64) > 1 else []

    if data.get('reset'):
        CHAT_HISTORY = []
        redis_set('chat_history', [])
        return jsonify({"status": "reset"})

    if not message and not image_b64 and not images_b64:
        return jsonify({"error": "Message vide"}), 400

    if not CHAT_HISTORY:
        CHAT_HISTORY = load_chat_history()

    bankroll = load_bankroll()
    bks = load_bookmakers()
    total_bookmakers = round(sum(b['solde'] for b in bks), 2)
    bks_detail = ', '.join([f"{b['nom']} {b['solde']}€" for b in bks])
    bankroll_info = f"Total réel réparti sur les bookmakers : {total_bookmakers}€ ({bks_detail})"
    bookmakers_str = ', '.join([b['nom'] for b in bks])

    cotes_reelles = ""
    TRIGGER_COTES = ['cote','jouer','value','pari','mise','analyse','match','vs','contre']
    if message and any(s in message.lower() for s in TRIGGER_COTES):
        sport_key_detected = None
        if any(s in message.lower() for s in ['nba','lakers','celtics','warriors','bulls']):
            sport_key_detected = 'nba'
        elif any(s in message.lower() for s in ['euroleague','eurocup']):
            sport_key_detected = 'euroleague'
        elif any(s in message.lower() for s in ['tennis','atp','wta','open','roland']):
            sport_key_detected = 'atp' if 'wta' not in message.lower() else 'wta'
        elif any(s in message.lower() for s in ['epl','premier','laliga','liga','bundesliga','ligue','serie','ucl','champions']):
            for sk in ['epl','laliga','bundesliga','ligue1','seriea','ucl']:
                if sk in message.lower() or APISPORTS_LEAGUES.get(sk, {}).get('nom', '').lower() in message.lower():
                    sport_key_detected = sk
                    break
            if not sport_key_detected:
                sport_key_detected = 'epl'

        if sport_key_detected:
            if sport_key_detected == 'nba':
                matchs_sport = fetch_nba()
            elif sport_key_detected == 'euroleague':
                matchs_sport = fetch_euroleague()
            elif sport_key_detected in ['atp', 'wta']:
                matchs_sport = fetch_tennis(sport_key_detected)
            else:
                matchs_sport = fetch_football_sport(sport_key_detected)

            matchs_avec_cotes = [m for m in matchs_sport if m.get('cotes')]
            if matchs_avec_cotes:
                cotes_reelles = f"COTES REELLES : " + " | ".join([
                    f"{m['home_team']} vs {m['away_team']} - Home:{m['cotes'][0].get('home_cote','?')} Away:{m['cotes'][0].get('away_cote','?')}"
                    for m in matchs_avec_cotes[:3]
                ])

    forme_context = ""
    if message:
        msg_lower = message.lower()
        sport_detected = None
        tennis_keywords = ['tennis', 'atp', 'wta', 'roland', 'wimbledon', 'open', 'tournoi']
        basket_keywords = ['nba', 'basket', 'euroleague', 'eurocup', 'lakers', 'celtics']
        foot_keywords = ['foot', 'epl', 'laliga', 'liga', 'bundesliga', 'ligue', 'serie', 'ucl', 'champions']

        if any(kw in msg_lower for kw in tennis_keywords):
            sport_detected = 'atp' if 'wta' not in msg_lower else 'wta'
        elif any(kw in msg_lower for kw in basket_keywords):
            sport_detected = 'nba' if 'nba' in msg_lower else 'euroleague'
        elif any(kw in msg_lower for kw in foot_keywords):
            for sid in ['epl','laliga','bundesliga','ligue1','seriea','ucl']:
                if sid in msg_lower or APISPORTS_LEAGUES.get(sid, {}).get('nom', '').lower() in msg_lower:
                    sport_detected = sid
                    break
            if not sport_detected:
                sport_detected = 'epl'

        if sport_detected and sport_detected in SPORTSDB_LEAGUES:
            league_id = SPORTSDB_LEAGUES[sport_detected]
            next_events = fetch_sportsdb_events(league_id)
            if next_events:
                forme_context += " PROCHAINS MATCHS : " + " | ".join([
                    f"{e['home_team']} vs {e['away_team']} ({e.get('commence_time','')[:10]})"
                    for e in next_events[:5]
                ])
            last_events = fetch_sportsdb_last_events(league_id)
            if last_events:
                forme_context += " RESULTATS RECENTS : " + " | ".join([
                    f"{e['home']} {e['score']} {e['away']} ({e['date']})"
                    for e in last_events[-5:]
                ])

    system_prompt = (
        "Tu es RADAR, le pote de Gael qui connait le sport mieux que personne. "
        "T es pas un assistant, t es pas un robot, t es le gars que Gael appelle quand il veut un vrai avis. "
        "Tu parles naturellement, avec du caractere. Tu peux dire franchement ce match je le sens pas, "
        "la cote est nulle la, attends je regarde ca, bah la t as pas le choix tu joues. "
        "Tu tutoies Gael, tu te souviens des conversations et tu peux y faire reference. "
        "Pas de listes a puces sauf si vraiment necessaire. Tu causes, tu expliques comme a un pote. "
        "REGLES QUE TU RESPECTES TOUJOURS (meme en etant cool) : "
        "- JAMAIS inventer des cotes ou des stats. Si t as pas les donnees, tu dis j ai pas les cotes sous la main. "
        "- JAMAIS faire semblant de chercher des donnees que t as pas. "
        "- Un value bet ca doit avoir un edge de 15% MINIMUM. En dessous c est pas un value bet, c est du bruit. "
        "- Edge negatif = pas de value bet, tu le dis cash et sans detour. "
        "- Si edge entre 0 et 15% : tu dis que c est trop juste et tu conseilles pas de miser. "
        f"Bankroll de Gael : {bankroll_info}. "
        "Mise selon confiance : 70% = 2%, 80% = 4%, 90% = 6%, 100% = 8% de la bankroll. "
        f"Bookmakers de Gael : {bookmakers_str}. "
        "DONNEES DISPO dans ce contexte (utilise-les si presentes, invente rien si absentes). "
        "FORMAT : court et percutant. Commence direct par le verdict. "
        "Bookmaker + cote + mise en euros seulement si t as les vraies donnees. "
        "Risque : FAIBLE / MOYEN / ELEVE. "
        "Si image : lis directement les cotes et stats visibles. "
        "METEO : Ne JAMAIS parler de meteo pour le basket (NBA, Euroleague, EuroCup, Pro A) "
        "ni pour le tennis — ca se joue en salle ou sur surface couverte, la meteo s en fout. "
        "La meteo est pertinente UNIQUEMENT pour le foot en exterieur. "
        "Toujours en francais. "
        f"{cotes_reelles}{forme_context}"
    )

    if image_b64:
        user_content = [
            {"type": "text", "text": message or "Analyse ces images"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
        ]
        for extra_img in extra_images:
            user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{extra_img}"}})
        model = "meta-llama/llama-4-scout-17b-16e-instruct"
        chat_api_url = "https://api.groq.com/openai/v1/chat/completions"
        chat_api_key = GROQ_API_KEY
    else:
        user_content = message
        if CEREBRAS_API_KEY:
            model = "qwen-3-32b"
            chat_api_url = "https://api.cerebras.ai/v1/chat/completions"
            chat_api_key = CEREBRAS_API_KEY
        else:
            model = "llama-3.3-70b-versatile"
            chat_api_url = "https://api.groq.com/openai/v1/chat/completions"
            chat_api_key = GROQ_API_KEY

    CHAT_HISTORY.append({"role": "user", "content": message or "image"})

    messages_to_send = [{"role": "system", "content": system_prompt}]
    for msg in CHAT_HISTORY[-20:]:
        content = msg["content"]
        if isinstance(content, list):
            content = "[image analysée]"
        messages_to_send.append({"role": msg["role"], "content": content})

    if image_b64:
        messages_to_send[-1]["content"] = user_content

    try:
        r = requests.post(
            chat_api_url,
            headers={
                "Authorization": f"Bearer {chat_api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": messages_to_send,
                "max_tokens": 1000
            },
            timeout=15
        )
        res = r.json()
        print(f"DEBUG chat API: {chat_api_url} model={model} status={r.status_code}")

        if 'choices' in res and len(res['choices']) > 0:
            reply = res['choices'][0]['message']['content']
        else:
            print(f"DEBUG chat erreur: {res}")
            if chat_api_url != "https://api.groq.com/openai/v1/chat/completions" and GROQ_API_KEY:
                r2 = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                    json={"model": "llama-3.3-70b-versatile", "messages": messages_to_send, "max_tokens": 1000},
                    timeout=15
                )
                res2 = r2.json()
                reply = res2.get('choices', [{}])[0].get('message', {}).get('content', "Erreur IA.")
            else:
                reply = "Erreur IA temporaire, réessaie !"

        CHAT_HISTORY.append({"role": "assistant", "content": reply})
        save_chat_history(CHAT_HISTORY)
        return jsonify({"reply": reply})

    except Exception as e:
        print(f"Erreur chat : {e}")
        return jsonify({"error": str(e)}), 500

# ============================================================
# ENDPOINT ANALYSE RAFALE — Multi-images Radar V6
# ============================================================
def optimiser_image_b64(b64_string, max_width=800, quality=85):
    try:
        img_bytes = base64.b64decode(b64_string)
        img = Image.open(io.BytesIO(img_bytes))
        if img.size[0] > max_width:
            ratio = max_width / float(img.size[0])
            new_h = int(img.size[1] * ratio)
            img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.convert('RGB').save(buffer, format="JPEG", quality=quality)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"Erreur optimisation image : {e}")
        return b64_string

@app.route('/radar/analyse-rafale', methods=['POST'])
def analyse_rafale():
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception as e:
        return jsonify({"error": f"Payload invalide: {e}"}), 400

    images_b64 = data.get('images', [])
    match_label = data.get('match', 'Match inconnu')

    print(f"DEBUG rafale: {len(images_b64)} images reçues pour {match_label}")

    if not images_b64:
        return jsonify({"error": "Aucune image reçue"}), 400
    if len(images_b64) > 3:
        images_b64 = images_b64[:3]

    images_b64 = [img for img in images_b64 if len(img) < 1_400_000]
    if not images_b64:
        return jsonify({"error": "Images trop lourdes, réduis leur taille"}), 400

    contenu = [
        {
            "type": "text",
            "text": (
                f"Match : {match_label}. "
                "Analyse ces captures (H2H, Series, Cotes, Stats) et reponds STRICTEMENT en JSON valide, "
                "sans aucun texte avant ou apres, sans balises markdown, sans backticks. "
                "JSON requis avec exactement ces cles : "
                "{\"match\": string, "
                "\"edge_pct\": number, "
                "\"value_bet\": boolean, "
                "\"verdict\": \"JOUER\" ou \"NE PAS JOUER\", "
                "\"pari_suggere\": string, "
                "\"cote_cible\": number, "
                "\"meilleur_bookmaker\": string, "
                "\"confiance_pct\": number, "
                "\"risque\": \"FAIBLE\" ou \"MOYEN\" ou \"ELEVE\", "
                "\"mise_recommandee\": string, "
                "\"resume\": string, "
                "\"signaux\": [liste de strings]}. "
                "REGLES : utilise UNIQUEMENT les cotes visibles dans les images. "
                "Edge minimum 15% pour value_bet=true. "
                "Ne jamais inventer de donnees absentes des images."
            )
        }
    ]

    for b64 in images_b64:
        b64_opt = optimiser_image_b64(b64)
        contenu.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64_opt}"}
        })

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "messages": [{"role": "user", "content": contenu}],
                "max_tokens": 1000,
                "temperature": 0.1
            },
            timeout=30
        )
        res = r.json()

        if 'choices' not in res or not res['choices']:
            return jsonify({"error": "Réponse Groq vide", "raw": res}), 500

        raw_reply = res['choices'][0]['message']['content']
        print(f"DEBUG rafale raw: {raw_reply[:300]}")

        clean = raw_reply.strip()
        if "```" in clean:
            for part in clean.split("```"):
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    clean = part
                    break

        start = clean.find("{")
        if start != -1:
            depth = 0
            end = start
            for i, c in enumerate(clean[start:], start):
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            clean = clean[start:end]

        print(f"DEBUG rafale clean: {clean[:300]}")

        try:
            analyse = json.loads(clean)
        except Exception as e:
            print(f"DEBUG parse error: {e}")
            analyse = {"resume": raw_reply, "parse_error": True}

        return jsonify({"status": "ok", "analyse": analyse})

    except Exception as e:
        print(f"Erreur analyse-rafale : {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
