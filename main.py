from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
# anthropic remplacé par Groq
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
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')  # gardé pour compatibilité
BALLDONTLIE_KEY   = os.getenv('BALLDONTLIE_KEY')
OPENWEATHER_KEY   = os.getenv('OPENWEATHER_KEY')
APISPORTS_KEY     = os.getenv('APISPORTS_KEY')
GROQ_API_KEY      = os.getenv('GROQ_API_KEY')
ODDS_API_KEY      = os.getenv('ODDS_API_KEY')

# ============================================================
# NTFY — Notifications push gratuites
# ============================================================
NTFY_CHANNEL = "radar-v6-gael"
NTFY_URL = f"https://ntfy.sh/{NTFY_CHANNEL}"

def envoyer_notif_ntfy(titre, message, priorite="high", tags="rotating_light"):
    """Envoie une notification push via ntfy.sh."""
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
# ODDS API — Vraies cotes en temps réel
# ============================================================
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Mapping bookmakers OddsAPI → noms Gaël
BOOKMAKERS_MAP = {
    "winamax_fr": "Winamax",
    "betclic": "Betclic",
    "betway": "Betway",
    "unibet_fr": "Unibet",
    "pmu": "PMU",
}

# Sports OddsAPI
ODDS_SPORTS = {
    "nba": "basketball_nba",
    "epl": "soccer_epl",
    "laliga": "soccer_spain_la_liga",
    "bundesliga": "soccer_germany_bundesliga",
    "ligue1": "soccer_france_ligue_one",
    "seriea": "soccer_italy_serie_a",
    "ucl": "soccer_uefa_champs_league",
    "atp": "tennis_atp_french_open",
}

def fetch_odds_api(sport_key, match_teams=None):
    """Récupère les vraies cotes depuis OddsAPI pour le bot chat."""
    if not ODDS_API_KEY:
        return None
    
    odds_sport = ODDS_SPORTS.get(sport_key, sport_key)
    try:
        r = requests.get(
            f"{ODDS_API_BASE}/sports/{odds_sport}/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "eu",
                "markets": "h2h,totals",
                "oddsFormat": "decimal",
                "dateFormat": "iso"
            },
            timeout=10
        )
        if r.status_code != 200:
            print(f"OddsAPI erreur {r.status_code}: {r.text}")
            return None
        
        games = r.json()
        
        # Si on cherche un match précis
        if match_teams:
            teams_lower = match_teams.lower()
            for game in games:
                home = game.get('home_team', '').lower()
                away = game.get('away_team', '').lower()
                if any(t in teams_lower for t in [home, away]):
                    return format_odds_game(game)
        
        # Sinon retourne tous les matchs du jour
        results = []
        for game in games[:10]:
            results.append(format_odds_game(game))
        return results
        
    except Exception as e:
        print(f"OddsAPI erreur : {e}")
        return None

def format_odds_game(game):
    """Formate un match OddsAPI en dict propre."""
    cotes = {}
    for bookie in game.get('bookmakers', []):
        name = bookie.get('key', '')
        for market in bookie.get('markets', []):
            if market.get('key') == 'h2h':
                outcomes = {o['name']: o['price'] for o in market.get('outcomes', [])}
                cotes[bookie.get('title', name)] = outcomes
    return {
        "match": f"{game.get('home_team')} vs {game.get('away_team')}",
        "sport": game.get('sport_title'),
        "heure": game.get('commence_time', '')[:16].replace('T', ' '),
        "cotes": cotes
    }

# ============================================================
# THESPORTSDB — API gratuite multi-sports
# ============================================================
SPORTSDB_KEY = "3"  # Clé publique gratuite TheSportsDB

SPORTSDB_LEAGUES = {
    "nba":        "4387",
    "nfl":        "4391",
    "epl":        "4328",
    "laliga":     "4335",
    "bundesliga": "4331",
    "ligue1":     "4334",
    "seriea":     "4332",
    "ucl":        "4480",
    "atp":        "4424",
    "wta":        "4425",
    "euroleague": "4455",
}

def fetch_sportsdb_events(league_id):
    """Récupère les prochains matchs TheSportsDB - gratuit."""
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

def fetch_sportsdb_team_info(team_name):
    """Récupère les infos d'une équipe - forme, joueurs clés."""
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

def fetch_sportsdb_last_events(league_id):
    """Récupère les derniers résultats pour analyse de forme."""
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

@app.route('/radar/sportsdb/<sport_id>')
def get_sportsdb(sport_id):
    """Endpoint TheSportsDB prochains matchs."""
    league_id = SPORTSDB_LEAGUES.get(sport_id)
    if not league_id:
        return jsonify({"data": []})
    matchs = fetch_sportsdb_events(league_id)
    return jsonify({"data": matchs, "source": "thesportsdb"})

@app.route('/radar/sportsdb/form/<sport_id>')
def get_sportsdb_form(sport_id):
    """Endpoint TheSportsDB derniers résultats - analyse de forme."""
    league_id = SPORTSDB_LEAGUES.get(sport_id)
    if not league_id:
        return jsonify({"data": []})
    results = fetch_sportsdb_last_events(league_id)
    return jsonify({"data": results, "source": "thesportsdb"})

# ============================================================
# ODDSPORTAL — Scraping cotes multi-bookmakers
# ============================================================
ODDSPORTAL_BOOKMAKERS = ["Winamax", "Betclic", "Mystake", "Betify", "Unibet", "bet365"]

ODDSPORTAL_SPORTS = {
    "nba":        "basketball/usa/nba",
    "epl":        "soccer/england/premier-league",
    "laliga":     "soccer/spain/laliga",
    "bundesliga": "soccer/germany/bundesliga",
    "ligue1":     "soccer/france/ligue-1",
    "seriea":     "soccer/italy/serie-a",
    "ucl":        "soccer/europe/champions-league",
    "euroleague": "basketball/europe/euroleague",
    "atp":        "tennis/atp-singles",
    "wta":        "tennis/wta-singles",
}

ODDSPORTAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": "https://www.oddsportal.com/",
}

def scrape_oddsportal(sport_key, match_name=None):
    """Scrape OddsPortal pour récupérer les cotes en temps réel."""
    if not BeautifulSoup:
        print("BeautifulSoup non disponible")
        return None

    cache_key = f"oddsportal_{sport_key}_{match_name or 'all'}"
    cached = get_cache(cache_key, "odds")
    if cached:
        return cached

    sport_path = ODDSPORTAL_SPORTS.get(sport_key)
    if not sport_path:
        return None

    url = f"https://www.oddsportal.com/{sport_path}/"
    try:
        r = requests.get(url, headers=ODDSPORTAL_HEADERS, timeout=10)
        if r.status_code != 200:
            print(f"OddsPortal erreur {r.status_code}")
            return None

        soup = BeautifulSoup(r.text, 'html.parser')

        # Chercher les matchs dans la page
        results = []
        # OddsPortal charge les données en JS - on cherche dans les balises disponibles
        rows = soup.select('div[class*="eventRow"]') or soup.select('tr[class*="deactivate"]') or []

        if not rows:
            # Chercher dans le JSON embarqué
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and 'odds' in str(script.string).lower() and 'home' in str(script.string).lower():
                    print(f"OddsPortal script trouvé: {str(script.string)[:200]}")
                    break

        for row in rows[:10]:
            try:
                teams = row.select('a[class*="participant"]') or row.select('a[href*="results"]')
                odds_cells = row.select('td[class*="odds"]') or row.select('div[class*="odds"]')

                if len(teams) >= 2:
                    match_str = f"{teams[0].text.strip()} vs {teams[1].text.strip()}"
                    cotes = {}
                    for i, cell in enumerate(odds_cells[:3]):
                        val = cell.text.strip()
                        try:
                            cotes[f"cote_{i+1}"] = float(val)
                        except:
                            pass
                    results.append({"match": match_str, "cotes": cotes})
            except:
                continue

        if results:
            set_cache(cache_key, results)
            print(f"OddsPortal: {len(results)} matchs trouvés pour {sport_key}")
            return results

        print(f"OddsPortal: aucun match parsé pour {sport_key} (JS dynamique probable)")
        return None

    except Exception as e:
        print(f"OddsPortal scraping erreur: {e}")
        return None

def get_best_odds(sport_key, match_name=None):
    """Essaie OddsPortal d'abord, fallback OddsAPI si bloqué."""
    # 1. Essai OddsPortal
    data = scrape_oddsportal(sport_key, match_name)
    if data:
        return {"source": "oddsportal", "data": data}

    # 2. Fallback OddsAPI
    data = fetch_odds_api(sport_key, match_name)
    if data:
        return {"source": "oddsapi", "data": data}

    return None

# ============================================================
# CACHE INTELLIGENT
# ============================================================
CACHE = {}
CACHE_DURATIONS = {
    "live":      2  * 60,   # 2 min  — scores live
    "scheduled": 2  * 3600, # 2h     — matchs à venir
    "odds":      1  * 3600, # 1h     — cotes
    "weather":   30 * 60,   # 30 min — météo
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

HISTORIQUE_FILE   = "historique.json"
BANKROLL_FILE     = "bankroll.json"
RESUME_FILE       = "resume.json"
CHAT_HISTORY_FILE = "chat_history.json"

# Bookmakers de Gaël
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
# API-SPORTS — Fonction générique
# ============================================================
APISPORTS_BASE = "https://v3.football.api-sports.io"

# IDs des ligues API-Sports
APISPORTS_LEAGUES = {
    "epl":        {"id": 39,  "nom": "Premier League"},
    "laliga":     {"id": 140, "nom": "La Liga"},
    "bundesliga": {"id": 78,  "nom": "Bundesliga"},
    "ligue1":     {"id": 61,  "nom": "Ligue 1"},
    "seriea":     {"id": 135, "nom": "Serie A"},
    "ucl":        {"id": 2,   "nom": "Champions League"},
    "mls":        {"id": 253, "nom": "MLS"},
    "amicaux":    {"id": 10,  "nom": "Matchs Amicaux"},       # Amicaux internationaux
    "nations":    {"id": 5,   "nom": "UEFA Nations League"},  # Nations League
    "worldcup":   {"id": 1,   "nom": "Qualif. Coupe du Monde"},
}

# Basket via api-basketball.api-sports.io
APIBASKET_LEAGUES = {
    "euroleague":  {"id": 120, "nom": "Euroleague"},
    "eurocup":     {"id": 121, "nom": "EuroCup"},
    "proA":        {"id": 116, "nom": "Pro A France"},
    "nbl":         {"id": 8,   "nom": "NBL Australia"},
}

# Tennis via api-tennis.api-sports.io
APITENNIS_LEAGUES = {
    "atp": {"id": 1, "nom": "ATP Tour"},
    "wta": {"id": 2, "nom": "WTA Tour"},
}

def apisports_get(endpoint, params={}, sport="football"):
    cache_key = f"apisports_{sport}_{endpoint}_{str(params)}"
    cache_type = "live" if params.get("live") else "scheduled"
    cached = get_cache(cache_key, cache_type)
    if cached:
        return cached

    base = f"https://v3.{sport}.api-sports.io"
    try:
        r = requests.get(
            f"{base}{endpoint}",
            headers={"x-apisports-key": APISPORTS_KEY},
            params=params,
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            set_cache(cache_key, data)
            return data
        else:
            print(f"❌ API-Sports {sport} {endpoint} : {r.status_code}")
            return None
    except Exception as e:
        print(f"🚨 API-Sports erreur : {e}")
        return None

def fetch_football_today(league_id, with_season=True):
    today = datetime.now().strftime("%Y-%m-%d")
    params = {"league": league_id, "date": today}
    if with_season:
        params["season"] = 2025
    return apisports_get("/fixtures", params, "football")

def fetch_football_live(league_id):
    return apisports_get("/fixtures", {"league": league_id, "live": "all"}, "football")

def fetch_football_odds(fixture_id):
    cached = get_cache(f"odds_{fixture_id}", "odds")
    if cached:
        return cached
    data = apisports_get("/odds", {"fixture": fixture_id, "bookmaker": 6}, "football")
    if data:
        set_cache(f"odds_{fixture_id}", data)
    return data

def fetch_basket_today(league_id):
    today = datetime.now().strftime("%Y-%m-%d")
    data = apisports_get("/games", {"league": league_id, "date": today, "season": "2025-2026"}, "basketball")
    if not data or not data.get("response"):
        print(f"🔍 Aucun match programmé trouvé pour {league_id}, check du LIVE en cours...")
        data = apisports_get("/games", {"league": league_id, "live": "all"}, "basketball")
    return data

def fetch_tennis_today():
    today = datetime.now().strftime("%Y-%m-%d")
    return apisports_get("/games", {"date": today}, "tennis")

# ============================================================
# BALLDONTLIE — NBA uniquement
# ============================================================
def bdl_get(endpoint, params={}):
    cache_key = f"bdl_{endpoint}_{str(params)}"
    cached = get_cache(cache_key, "scheduled")
    if cached:
        return cached
    try:
        r = requests.get(
            f"https://api.balldontlie.io{endpoint}",
            headers={"Authorization": BALLDONTLIE_KEY},
            params=params,
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            set_cache(cache_key, data)
            return data
        else:
            print(f"❌ BallDontLie {endpoint} : {r.status_code}")
            return None
    except Exception as e:
        print(f"🚨 BallDontLie erreur : {e}")
        return None

def fetch_nba_today():
    if not BALLDONTLIE_KEY:
        return None
    from datetime import timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    # Cherche aujourd hui ET hier pour couvrir les matchs NBA de la nuit
    data = bdl_get("/nba/v1/games", {"dates[]": today, "per_page": 15})
    if not data or not data.get("data"):
        data = bdl_get("/nba/v1/games", {"dates[]": yesterday, "per_page": 15})
    return data

def fetch_nba_odds():
    # /nba/v2/odds nécessite un plan payant BallDontLie
    return None

# ============================================================
# FETCH PAR SPORT — Retourne format unifié
# ============================================================
def fetch_nba():
    cached = get_cache("sport_nba", "scheduled")
    if cached:
        return cached

    matchs = []
    data = fetch_nba_today()
    if not data or not data.get('data'):
        # Fallback TheSportsDB si BallDontLie vide
        print("BallDontLie vide → fallback TheSportsDB NBA")
        fallback = fetch_sportsdb_events(SPORTSDB_LEAGUES.get("nba", "4387"))
        for m in fallback:
            m["sport_key"] = "nba"
            m["sport_title"] = "NBA"
        set_cache("sport_nba", fallback)
        return fallback

    # Cotes NBA
    odds_data = fetch_nba_odds()
    odds_map = {}
    if odds_data and odds_data.get('data'):
        for odd in odds_data['data']:
            gid = odd.get('game_id')
            if gid:
                odds_map.setdefault(gid, []).append(odd)

    for game in data['data']:
        gid = game.get('id')
        home = game.get('home_team', {})
        away = game.get('visitor_team', {})
        cotes = []
        for odd in odds_map.get(gid, [])[:3]:
            cotes.append({
                "bookmaker": odd.get('book', 'Unknown'),
                "home_cote": odd.get('home_moneyline'),
                "away_cote": odd.get('away_moneyline'),
                "over_under": odd.get('over_under'),
                "spread": odd.get('home_spread')
            })
        matchs.append({
            "id": str(gid),
            "sport_key": "nba",
            "sport_title": "NBA",
            "commence_time": game.get('date'),
            "home_team": home.get('full_name', '?'),
            "away_team": away.get('full_name', '?'),
            "status": game.get('status', 'scheduled'),
            "score_home": game.get('home_team_score'),
            "score_away": game.get('visitor_team_score'),
            "cotes": cotes,
            "source": "balldontlie"
        })

    set_cache("sport_nba", matchs)
    return matchs

def fetch_football_sport(sport_id):
    league = APISPORTS_LEAGUES.get(sport_id)
    if not league:
        return []

    cached = get_cache(f"sport_{sport_id}", "scheduled")
    if cached:
        return cached

    matchs = []

    # Matchs live
    live_data = fetch_football_live(league["id"])
    live_ids = set()
    if live_data and live_data.get('response'):
        for fix in live_data['response']:
            teams = fix.get('teams', {})
            goals = fix.get('goals', {})
            fixture_id = fix.get('fixture', {}).get('id')
            live_ids.add(fixture_id)
            matchs.append({
                "id": str(fixture_id),
                "sport_key": sport_id,
                "sport_title": league["nom"],
                "commence_time": fix.get('fixture', {}).get('date'),
                "home_team": teams.get('home', {}).get('name', '?'),
                "away_team": teams.get('away', {}).get('name', '?'),
                "status": "in_progress",
                "score_home": goals.get('home'),
                "score_away": goals.get('away'),
                "cotes": [],
                "source": "apisports"
            })

    # Pas de filtre saison pour les matchs internationaux
    no_season = sport_id in ["amicaux", "nations", "worldcup"]
    today_data = fetch_football_today(league["id"], with_season=not no_season)
    if today_data and today_data.get('response'):
        for fix in today_data['response']:
            fixture_id = fix.get('fixture', {}).get('id')
            if fixture_id in live_ids:
                continue
            teams  = fix.get('teams', {})
            goals  = fix.get('goals', {})
            status = fix.get('fixture', {}).get('status', {}).get('short', 'NS')

            if status in ['FT', 'AET', 'PEN']:
                continue  # On n'affiche pas les matchs terminés

            # Cotes (1 requête par match — on limite aux 5 premiers)
            cotes = []
            if len(matchs) < 5:
                odds_data = fetch_football_odds(fixture_id)
                if odds_data and odds_data.get('response'):
                    for bookmaker in odds_data['response'][:1]:
                        for book in bookmaker.get('bookmakers', [])[:1]:
                            for bet in book.get('bets', []):
                                if bet.get('name') == 'Match Winner':
                                    vals = {v['value']: v['odd'] for v in bet.get('values', [])}
                                    cotes.append({
                                        "bookmaker": book.get('name', '?'),
                                        "home_cote": vals.get('Home'),
                                        "away_cote": vals.get('Away'),
                                        "draw_cote": vals.get('Draw'),
                                        "over_under": None,
                                        "spread": None
                                    })

            matchs.append({
                "id": str(fixture_id),
                "sport_key": sport_id,
                "sport_title": league["nom"],
                "commence_time": fix.get('fixture', {}).get('date'),
                "home_team": teams.get('home', {}).get('name', '?'),
                "away_team": teams.get('away', {}).get('name', '?'),
                "status": "scheduled" if status == 'NS' else "in_progress",
                "score_home": goals.get('home'),
                "score_away": goals.get('away'),
                "cotes": cotes,
                "source": "apisports"
            })

    # Fallback TheSportsDB si API-Sports vide
    if not matchs:
        sdb_id = SPORTSDB_LEAGUES.get(sport_id)
        if sdb_id:
            print(f"API-Sports vide pour {sport_id} → fallback TheSportsDB")
            matchs = fetch_sportsdb_events(sdb_id)
            for m in matchs:
                m["sport_key"] = sport_id
                m["sport_title"] = APISPORTS_LEAGUES.get(sport_id, {}).get("nom", sport_id)

    set_cache(f"sport_{sport_id}", matchs)
    return matchs

def fetch_basket_sport(sport_id):
    league = APIBASKET_LEAGUES.get(sport_id)
    if not league:
        return []

    cached = get_cache(f"sport_{sport_id}", "live")
    if cached:
        return cached

    matchs = []
    data = fetch_basket_today(league["id"])

    if data and data.get("response"):
        for game in data["response"]:
            teams  = game.get("teams", {})
            scores = game.get("scores", {})
            status = game.get("status", {}).get("short", "NS")
            if status == "FT":
                continue
            matchs.append({
                "id": str(game.get("id")),
                "sport_key": sport_id,
                "sport_title": league["nom"],
                "commence_time": game.get("date"),
                "home_team": teams.get("home", {}).get("name", "?"),
                "away_team": teams.get("away", {}).get("name", "?"),
                "status": "in_progress" if status != "NS" else "scheduled",
                "score_home": scores.get("home", {}).get("total"),
                "score_away": scores.get("away", {}).get("total"),
                "cotes": [],
                "source": "apisports"
            })

    # Fallback TheSportsDB si API-Sports vide
    if not matchs:
        sdb_id = SPORTSDB_LEAGUES.get(sport_id)
        if sdb_id:
            print(f"API-Sports basket vide pour {sport_id} → fallback TheSportsDB")
            matchs = fetch_sportsdb_events(sdb_id)
            for m in matchs:
                m["sport_key"] = sport_id
                m["sport_title"] = APIBASKET_LEAGUES.get(sport_id, {}).get("nom", sport_id)

    set_cache(f"sport_{sport_id}", matchs)
    return matchs

def fetch_tennis_sport(sport_id):
    league = APITENNIS_LEAGUES.get(sport_id)
    if not league:
        return []

    cached = get_cache(f"sport_{sport_id}", "scheduled")
    if cached:
        return cached

    matchs = []
    data = fetch_tennis_today()
    if data and data.get('response'):
        for game in data['response']:
            # Filtre ATP ou WTA selon le sport_id
            tournament = game.get('tournament', {})
            league_name = tournament.get('name', '').lower()
            if sport_id == 'atp' and 'wta' in league_name:
                continue
            if sport_id == 'wta' and 'wta' not in league_name:
                continue

            players = game.get('players', [])
            home = players[0].get('player', {}) if len(players) > 0 else {}
            away = players[1].get('player', {}) if len(players) > 1 else {}
            status = game.get('status', {}).get('short', 'NS')

            if status == 'FIN':
                continue

            matchs.append({
                "id": str(game.get('id')),
                "sport_key": sport_id,
                "sport_title": league["nom"],
                "commence_time": game.get('date'),
                "home_team": home.get('name', '?'),
                "away_team": away.get('name', '?'),
                "status": "in_progress" if status == 'LIVE' else "scheduled",
                "score_home": None,
                "score_away": None,
                "cotes": [],
                "source": "apisports"
            })

    set_cache(f"sport_{sport_id}", matchs)
    return matchs

# ============================================================
# MÉTÉO
# ============================================================
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

# ============================================================
# UTILITAIRES
# ============================================================
def load_historique():
    data = redis_get('historique')
    return data if data else []

def save_historique(entry):
    historique = load_historique()
    historique.insert(0, entry)
    historique = historique[:100]
    redis_set('historique', historique)

def load_bankroll():
    data = redis_get('bankroll')
    return data if data else {"total": 0, "disponible": 0, "mises": []}

def save_bankroll(data):
    redis_set('bankroll', data)

def calculer_mise(bankroll_disponible, confiance):
    pourcentages = {7: 0.02, 8: 0.04, 9: 0.06, 10: 0.08}
    return round(bankroll_disponible * pourcentages.get(confiance, 0.02), 2)

# ============================================================
# ANALYSE IA
# ============================================================
def analyze_with_claude(match):
    if not GROQ_API_KEY:
        print("🚨 GROQ_API_KEY manquante sur Render !")
        return None

    meteo = get_weather()
    meteo_str = f"{meteo['temp']}°C, {meteo['conditions']}, vent {meteo['vent']}m/s" if meteo else "Stable"

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

    prompt = f"""Tu es un expert en paris sportifs. Analyse ce match et réponds UNIQUEMENT en JSON valide sans aucun texte autour.

Match: {match.get('away_team')} @ {match.get('home_team')}
Sport: {match.get('sport_title')}
Heure: {match.get('commence_time')}
Météo: {meteo_str}{score_str}
Cotes: {cotes_summary if cotes_summary else 'Non disponibles - analyse sur contexte uniquement'}

Réponds UNIQUEMENT avec ce JSON :
{{
  "value_bet": true,
  "confiance": 7,
  "pari_recommande": "conseil court",
  "cote": 2.10,
  "bookmaker": "Bet365",
  "raison": "explication courte",
  "risque": "risque principal",
  "impact_meteo": "aucun",
  "mise_conseillee": "2% bankroll"
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
                "max_tokens": 500
            },
            timeout=10
        )
        res = r.json()
        return json.loads(res['choices'][0]['message']['content'])
    except Exception as e:
        print(f"🚨 Erreur Groq : {e}")
        return None

# ============================================================
# SCAN VALUE BETS
# ============================================================
def scan_value_bets():
    global ALERTES
    nouvelles_alertes = []

    # On scanne NBA + les 3 premiers championnats foot
    sports_a_scanner = [
        ("nba", fetch_nba),
        ("epl", lambda: fetch_football_sport("epl")),
        ("laliga", lambda: fetch_football_sport("laliga")),
        ("ucl", lambda: fetch_football_sport("ucl")),
    ]

    for sport_id, fetch_fn in sports_a_scanner:
        try:
            matchs = fetch_fn()
            for match in matchs[:2]:  # Max 2 matchs par sport
                if not match.get('cotes'):
                    continue
                analyse = analyze_with_claude(match)
                if not analyse:
                    continue
                if analyse.get('value_bet') and analyse.get('confiance', 0) >= 7:
                    match_label = f"{match.get('away_team')} @ {match.get('home_team')}"
                    alerte = {
                        "id": f"{match.get('id')}_{int(time.time())}",
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
                    # Notification push ntfy
                    notif_msg = (
                        f"Match : {match_label}\n"
                        f"Pari : {analyse.get('pari_recommande', '?')}\n"
                        f"Cote : {analyse.get('cote', '?')} sur {analyse.get('bookmaker', '?')}\n"
                        f"Confiance : {analyse.get('confiance', '?')}/10\n"
                        f"Risque : {analyse.get('risque', '?')}"
                    )
                    envoyer_notif_ntfy(
                        titre=f"🚨 VALUE BET {match.get('sport_title','').upper()}",
                        message=notif_msg,
                        priorite="urgent",
                        tags="rotating_light,moneybag"
                    )
                time.sleep(1)
        except Exception as e:
            print(f"Erreur scan {sport_id} : {e}")

    ALERTES = (nouvelles_alertes + ALERTES)[:20]
    print(f"✅ Scan terminé : {len(nouvelles_alertes)} value bets détectés")

# ============================================================
# RÉSUMÉ QUOTIDIEN
# ============================================================
def generate_daily_resume():
    tous_matchs = []
    tous_matchs.extend(fetch_nba()[:2])
    for sid in ["epl", "ucl", "laliga"]:
        tous_matchs.extend(fetch_football_sport(sid)[:2])

    if not tous_matchs:
        return None

    matchs_str = "\n".join([
        f"- {m.get('sport_title')} : {m.get('away_team')} @ {m.get('home_team')} | {m.get('commence_time')}"
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
# ENDPOINTS SANTÉ
# ============================================================
@app.route('/')
def health():
    return "RADAR V6 : SYSTEM READY 📡🏀🏒⚾🏈🥊⚽🎾"

@app.route('/test')
def test():
    today = datetime.now().strftime("%Y-%m-%d")
    results = {}

    # Test BallDontLie NBA
    try:
        r = requests.get("https://api.balldontlie.io/nba/v1/games",
            headers={"Authorization": BALLDONTLIE_KEY},
            params={"dates[]": today}, timeout=5)
        results["nba_balldontlie"] = {"status": r.status_code, "matchs": len(r.json().get('data', []))}
    except Exception as e:
        results["nba_balldontlie"] = {"erreur": str(e)}

    # Test API-Sports Football
    try:
        r = requests.get("https://v3.football.api-sports.io/fixtures",
            headers={"x-apisports-key": APISPORTS_KEY},
            params={"league": 39, "date": today, "season": 2025}, timeout=5)
        results["epl_apisports"] = {"status": r.status_code, "matchs": len(r.json().get('response', []))}
    except Exception as e:
        results["epl_apisports"] = {"erreur": str(e)}

    return jsonify(results)

@app.route('/test/live')
def test_live():
    """Teste tous les matchs de foot EN DIRECT maintenant — idéal pour France amical"""
    try:
        r = requests.get(
            "https://v3.football.api-sports.io/fixtures",
            headers={"x-apisports-key": APISPORTS_KEY},
            params={"live": "all"},
            timeout=10
        )
        data = r.json()
        matchs = data.get('response', [])
        print(f"🔴 {len(matchs)} matchs live détectés")

        result = []
        for fix in matchs:
            teams = fix.get('teams', {})
            goals = fix.get('goals', {})
            fixture = fix.get('fixture', {})
            league = fix.get('league', {})
            result.append({
                "id": fixture.get('id'),
                "league": league.get('name'),
                "league_id": league.get('id'),
                "home": teams.get('home', {}).get('name'),
                "away": teams.get('away', {}).get('name'),
                "score": f"{goals.get('home', 0)}-{goals.get('away', 0)}",
                "statut": fixture.get('status', {}).get('long'),
                "minute": fixture.get('status', {}).get('elapsed')
            })

        return jsonify({
            "total_live": len(matchs),
            "matchs": result,
            "quota_restant": data.get('errors', {}),
            "status_api": r.status_code
        })
    except Exception as e:
        return jsonify({"erreur": str(e)})

# ============================================================
# ENDPOINTS RADAR
# ============================================================
@app.route('/radar/<sport_id>')
def get_sport(sport_id):
    if sport_id == "nba":
        return jsonify({"data": fetch_nba()})
    elif sport_id in APISPORTS_LEAGUES:
        return jsonify({"data": fetch_football_sport(sport_id)})
    elif sport_id in APIBASKET_LEAGUES:
        return jsonify({"data": fetch_basket_sport(sport_id)})
    elif sport_id in APITENNIS_LEAGUES:
        return jsonify({"data": fetch_tennis_sport(sport_id)})
    else:
        return jsonify({"data": []})

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

# ============================================================
# ENDPOINTS HISTORIQUE
# ============================================================
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
    image_b64 = data.get('image')  # image en base64 optionnelle

    if data.get('reset'):
        CHAT_HISTORY = []
        redis_set('chat_history', [])
        return jsonify({"status": "reset"})

    if not message and not image_b64:
        return jsonify({"error": "Message vide"}), 400

    if not CHAT_HISTORY:
        CHAT_HISTORY = load_chat_history()

    bankroll = load_bankroll()
    dispo = bankroll.get('disponible', 0)
    bankroll_info = f"{dispo}€ sur le dashboard" if dispo > 0 else "0€ sur le dashboard (Gael a de la bankroll sur ses bookmakers)"
    bookmakers_str = ', '.join(BOOKMAKERS)

    # Récupération cotes temps réel — OddsPortal en priorité, OddsAPI en fallback
    cotes_reelles = ""
    if message and any(s in message.lower() for s in ['cote','cotes','pari','jouer','match','nba','foot','tennis','basket','euro','ligue','liga']):
        for sport_key in ODDS_SPORTS.keys():
            if sport_key in message.lower() or any(s in message.lower() for s in ['nba','foot','tennis','basket']):
                odds_result = get_best_odds(sport_key, message)
                if odds_result:
                    source = odds_result.get("source", "inconnu")
                    data = odds_result.get("data")
                    cotes_reelles = f"COTES REELLES ({source.upper()}) : {json.dumps(data, ensure_ascii=False)[:500]}"
                break

    # Récupération données TheSportsDB — prochains matchs + forme récente
    forme_context = ""
    if message:
        msg_lower = message.lower()
        # Détection sport/tournoi dans le message
        sport_detected = None
        tennis_keywords = ['tennis', 'atp', 'wta', 'marrakech', 'roland', 'wimbledon', 'open', 'tournoi']
        basket_keywords = ['nba', 'basket', 'euroleague', 'eurocup']
        foot_keywords = ['foot', 'epl', 'laliga', 'liga', 'bundesliga', 'ligue', 'serie', 'ucl', 'champions']

        if any(kw in msg_lower for kw in tennis_keywords):
            sport_detected = 'atp' if 'wta' not in msg_lower else 'wta'
        elif any(kw in msg_lower for kw in basket_keywords):
            sport_detected = 'nba' if 'nba' in msg_lower else 'euroleague'
        elif any(kw in msg_lower for kw in foot_keywords):
            for sid in ['epl','laliga','bundesliga','ligue1','seriea','ucl']:
                if sid in msg_lower or APISPORTS_LEAGUES.get(sid,{}).get('nom','').lower() in msg_lower:
                    sport_detected = sid
                    break
            if not sport_detected:
                sport_detected = 'epl'

        if sport_detected and sport_detected in SPORTSDB_LEAGUES:
            league_id = SPORTSDB_LEAGUES[sport_detected]
            # Prochains matchs
            next_events = fetch_sportsdb_events(league_id)
            if next_events:
                forme_context += " PROCHAINS MATCHS (TheSportsDB) : " + " | ".join([
                    f"{e['home_team']} vs {e['away_team']} ({e.get('commence_time','')[:10]})"
                    for e in next_events[:5]
                ])
            # Forme récente
            last_events = fetch_sportsdb_last_events(league_id)
            if last_events:
                forme_context += " RESULTATS RECENTS : " + " | ".join([
                    f"{e['home']} {e['score']} {e['away']} ({e['date']})"
                    for e in last_events[-5:]
                ])

    # Récupération news GNews + ESPN si pertinent
    news_context = ""
    if message:
        keywords = ["nba","basket","foot","ligue","chelsea","paris","real","celtics","lakers","blessure","suspension","blesse","forfait"]
        if any(kw in message.lower() for kw in keywords):
            gnews = fetch_news(message[:60], max_results=2)
            if gnews:
                news_context += " ACTUALITES : " + " | ".join([f"{n['titre']} ({n['date']})" for n in gnews])
            for sport_id in ESPN_SPORTS.keys():
                if sport_id in message.lower():
                    espn_news = fetch_espn_news(sport_id)
                    if espn_news:
                        news_context += " ESPN : " + " | ".join([n['titre'] for n in espn_news[:2]])
                    break

    system_prompt = (
        "Tu es RADAR, le pote expert en paris sportifs de Gael. Tu parles comme un ami qui s en connait, "
        "pas comme un robot. Tu tutoies Gael, tu es direct, parfois cash, jamais chiant. "
        "Tu connais Gael et tu te souviens de vos conversations. "
        "REGLES DE BASE : "
        "- Ne jamais inventer des cotes ou des stats - utilise uniquement ce que t as dans le contexte ou les images. "
        "- Si t as pas les donnees, dis-le en une phrase et propose une alternative, pas un discours de 50 lignes. "
        "- Verdict toujours clair : JOUER ou PAS. "
        "ANALYSE VALUE BET : "
        "Edge minimum 15%. Formule : ((Proba reelle - Proba bookmaker) / Proba bookmaker) x 100. "
        f"Bankroll de Gael : {bankroll_info}. "
        "Mise selon confiance : 70% = 2%, 80% = 4%, 90% = 6%, 100% = 8% de la bankroll. "
        f"Bookmakers de Gael : {bookmakers_str}. Toujours comparer et donner le meilleur. "
        "DONNEES DISPO dans ce contexte - utilise-les directement sans dire que t as pas acces : "
        "TheSportsDB pour prochains matchs et resultats recents, OddsPortal/OddsAPI pour les cotes. "
        "STYLE DE REPONSE : "
        "- Court et percutant. Pas de blabla. "
        "- Verdict JOUER / PAS en gras au debut. "
        "- Bookmaker recommande + cote + mise en euros. "
        "- Risque : FAIBLE / MOYEN / ELEVE. "
        "- Sections avec ### pour les titres, ** pour les mots cles. "
        "- Si image : extrait direct cotes stats scores visibles. "
        "- Tiens compte forme recente 5 matchs, H2H, domicile/exterieur. "
        "Toujours en francais. Sois le pote qui connait le sport pas le robot corporate. "
        f"{cotes_reelles}{forme_context}{news_context}"
    )

    # Construction du message utilisateur (texte + image si presente)
    if image_b64:
        user_content = [
            {"type": "text", "text": message or "Analyse cette image"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
        ]
        model = "meta-llama/llama-4-scout-17b-16e-instruct"
    else:
        user_content = message
        model = "llama-3.3-70b-versatile"

    CHAT_HISTORY.append({"role": "user", "content": user_content if not image_b64 else message or "image"})

    try:
        messages_to_send = [{"role": "system", "content": system_prompt}]
        # Pour les messages historiques (texte seulement)
        for msg in CHAT_HISTORY[-20:]:
            content = msg["content"]
            if isinstance(content, list):
                content = "[image analysée]"
            messages_to_send.append({"role": msg["role"], "content": content})

        # Si image, on remplace le dernier message user par le contenu avec image
        if image_b64:
            messages_to_send[-1]["content"] = user_content

        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": messages_to_send,
                "max_tokens": 600
            },
            timeout=15
        )
        res = r.json()

        if 'choices' in res and len(res['choices']) > 0:
            reply = res['choices'][0]['message']['content']
        else:
            reply = "Signal Groq recu mais illisible."

        CHAT_HISTORY.append({"role": "assistant", "content": reply})
        save_chat_history(CHAT_HISTORY)
        return jsonify({"reply": reply})

    except Exception as e:
        print(f"Erreur chat Groq : {e}")
        return jsonify({"error": str(e)}), 500

# ============================================================
# ENDPOINT ANALYSE RAFALE — Multi-images Radar V6
# ============================================================
def optimiser_image_b64(b64_string, max_width=800, quality=85):
    """Redimensionne et compresse une image base64."""
    try:
        img_bytes = base64.b64decode(b64_string)
        img = Image.open(io.BytesIO(img_bytes))
        # Redimensionnement si trop large
        if img.size[0] > max_width:
            ratio = max_width / float(img.size[0])
            new_h = int(img.size[1] * ratio)
            img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)
        # Compression JPEG
        buffer = io.BytesIO()
        img.convert('RGB').save(buffer, format="JPEG", quality=quality)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"Erreur optimisation image : {e}")
        return b64_string  # retourne l'original si erreur

@app.route('/radar/analyse-rafale', methods=['POST'])
def analyse_rafale():
    """
    Reçoit jusqu'à 6 images base64, les optimise,
    les envoie à Groq Vision et retourne le JSON d'analyse Radar V6.
    """
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
    
    # Limite taille : max 1MB par image en base64
    images_b64 = [img for img in images_b64 if len(img) < 1_400_000]
    if not images_b64:
        return jsonify({"error": "Images trop lourdes, réduis leur taille"}), 400

    # Construction du contenu multimodal
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

        # Extraction du PREMIER JSON valide uniquement
        clean = raw_reply.strip()
        
        # Retirer les balises markdown
        if "```" in clean:
            for part in clean.split("```"):
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    clean = part
                    break

        # Extraire uniquement le premier objet JSON complet
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
