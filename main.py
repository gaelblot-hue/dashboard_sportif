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
OPENWEATHER_KEY   = os.getenv('OPENWEATHER_KEY')
APISPORTS_KEY     = os.getenv('APISPORTS_KEY')
GROQ_API_KEY      = os.getenv('GROQ_API_KEY')
CEREBRAS_API_KEY  = os.getenv('CEREBRAS_API_KEY')

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
# SCRAPER BOVADA (NBA/BASKET SANS CLÉ API)
# ============================================================
def scrape_bovada_nba():
    """Remplace OddsAPI pour avoir les cotes NBA directes et gratuites."""
    url = "https://www.bovada.lv/services/sports/event/v1/events/list/data/basketball/nba"
    headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        matchs = []
        for section in data:
            for event in section.get('events', []):
                desc = event.get('description', '')
                home = desc.split(' @ ')[-1] if ' @ ' in desc else desc
                away = desc.split(' @ ')[0] if ' @ ' in desc else ''
                for group in event.get('displayGroups', []):
                    if group.get('description') == "Game Lines":
                        for m in group.get('markets', []):
                            if m.get('description') == "Moneyline":
                                out = m.get('outcomes', [])
                                if len(out) >= 2:
                                    matchs.append({
                                        "id": str(event.get('id')),
                                        "match": f"{away} vs {home}",
                                        "home_team": home, "away_team": away,
                                        "commence_time": datetime.fromtimestamp(event.get('startTime', 0)/1000).strftime('%Y-%m-%d %H:%M'),
                                        "status": "scheduled",
                                        "home_odds": out[1].get('price', {}).get('decimal'),
                                        "away_odds": out[0].get('price', {}).get('decimal'),
                                        "cotes": [{"bookmaker": "Bovada", "home_cote": out[1].get('price', {}).get('decimal'), "away_cote": out[0].get('price', {}).get('decimal')}]
                                    })
        return matchs
    except: return []

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
    "euroleague": "4966",  # EuroLeague Basketball correct
    "eurocup":    "4967",  # EuroCup Basketball correct
    "proA":       "4422",  # Pro A France Basketball
    "mls":        "4346",  # MLS Football
    "nbl":        "4459",  # NBL Australie
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
    """
    Utilise l'API interne d'OddsPortal (ce que le site JS appelle lui-même).
    Beaucoup plus fiable que scraper le HTML dynamique.
    """
    cache_key = f"oddsportal_{sport_key}_{match_name or 'all'}"
    cached = get_cache(cache_key, "odds")
    if cached:
        return cached

    sport_path = ODDSPORTAL_SPORTS.get(sport_key)
    if not sport_path:
        return None

    # Headers qui imitent une vraie requête navigateur OddsPortal
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Referer": f"https://www.oddsportal.com/{sport_path}/",
        "x-requested-with": "XMLHttpRequest",
    }

    # OddsPortal API interne — endpoint next events
    today = datetime.now().strftime("%Y%m%d")
    api_url = f"https://www.oddsportal.com/api/v2/leagues-events/?sport={sport_path.split('/')[0]}&path={sport_path}&date={today}&timezone=Europe/Paris"

    try:
        r = requests.get(api_url, headers=headers, timeout=12)
        print(f"OddsPortal API status: {r.status_code} pour {sport_key}")

        if r.status_code == 200:
            try:
                data = r.json()
                events = data.get("d", {}).get("rows", []) or data.get("rows", []) or []
                results = []
                for event in events[:10]:
                    home = event.get("home-name") or event.get("home") or ""
                    away = event.get("away-name") or event.get("away") or ""
                    if not home or not away:
                        continue
                    odds = event.get("odds", {})
                    cotes = {}
                    if odds:
                        cotes["home"] = odds.get("1") or odds.get("home")
                        cotes["draw"] = odds.get("X") or odds.get("draw")
                        cotes["away"] = odds.get("2") or odds.get("away")
                    results.append({
                        "match": f"{home} vs {away}",
                        "heure": event.get("date-start-ts", ""),
                        "cotes": {k: v for k, v in cotes.items() if v}
                    })
                if results:
                    set_cache(cache_key, results)
                    print(f"OddsPortal API: {len(results)} matchs pour {sport_key}")
                    return results
            except Exception as e:
                print(f"OddsPortal parse JSON erreur: {e}")

        # Fallback : essai avec l'URL next-events
        next_url = f"https://www.oddsportal.com/next/{sport_path}/"
        r2 = requests.get(next_url, headers={
            **headers,
            "Accept": "text/html,application/xhtml+xml",
        }, timeout=10)

        if r2.status_code == 200 and BeautifulSoup:
            soup = BeautifulSoup(r2.text, 'html.parser')
            # Chercher le JSON dans les scripts Next.js
            for script in soup.find_all('script'):
                txt = script.string or ""
                if '"home"' in txt and '"away"' in txt and '"odds"' in txt:
                    try:
                        # Extraire le JSON embarqué
                        start = txt.find('{"rows"')
                        if start == -1:
                            start = txt.find('{"events"')
                        if start != -1:
                            depth, end = 0, start
                            for i, c in enumerate(txt[start:], start):
                                if c == '{': depth += 1
                                elif c == '}':
                                    depth -= 1
                                    if depth == 0:
                                        end = i + 1
                                        break
                            chunk = json.loads(txt[start:end])
                            rows = chunk.get("rows") or chunk.get("events") or []
                            results = []
                            for ev in rows[:10]:
                                home = ev.get("home-name") or ev.get("home", "")
                                away = ev.get("away-name") or ev.get("away", "")
                                if home and away:
                                    results.append({"match": f"{home} vs {away}", "cotes": ev.get("odds", {})})
                            if results:
                                set_cache(cache_key, results)
                                return results
                    except Exception as e:
                        print(f"OddsPortal JSON embed erreur: {e}")

        print(f"OddsPortal: aucune donnée récupérée pour {sport_key}")
        return None

    except Exception as e:
        print(f"OddsPortal erreur globale: {e}")
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
    "live":       2  * 60,    # 2 min  — scores live
    "scheduled":  2  * 3600,  # 2h     — matchs à venir
    "odds":       4  * 3600,  # 4h     — cotes (economise le quota OddsAPI)
    "weather":    30 * 60,    # 30 min — météo
    "tennis_key": 24 * 3600,  # 24h    — tournoi tennis actif (change pas souvent)
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
# FETCH SPORTS — VERSION SANS CLÉ API (BOVADA + SPORTSDB)
# ============================================================

def fetch_nba():
    """NBA via Scraping Bovada pur."""
    cached = get_cache("sport_nba", "scheduled")
    if cached: return cached
    matchs = scrape_bovada_nba()
    if not matchs:
        matchs = fetch_sportsdb_events(SPORTSDB_LEAGUES.get("nba", "4387"))
    set_cache("sport_nba", matchs)
    return matchs

def fetch_football_sport(sport_id):
    """Foot via TheSportsDB (Clé publique gratuite)."""
    cached = get_cache(f"sport_{sport_id}", "scheduled")
    if cached: return cached
    league_id = SPORTSDB_LEAGUES.get(sport_id)
    matchs = fetch_sportsdb_events(league_id) if league_id else []
    set_cache(f"sport_{sport_id}", matchs)
    return matchs

def fetch_basket_sport(sport_id):
    """Basket Europe via TheSportsDB."""
    return fetch_football_sport(sport_id)

def fetch_tennis_sport(sport_id):
    """Tennis via TheSportsDB."""
    return fetch_football_sport(sport_id)

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
# Mapping équipes/villes pour météo pertinente
TEAM_CITY_MAP = {
    "paris": "Paris", "psg": "Paris", "marseille": "Marseille", "lyon": "Lyon",
    "barcelona": "Barcelona", "real madrid": "Madrid", "atletico": "Madrid",
    "manchester": "Manchester", "arsenal": "London", "chelsea": "London", "tottenham": "London",
    "bayern": "Munich", "dortmund": "Dortmund",
    "juventus": "Turin", "milan": "Milan", "inter": "Milan", "roma": "Rome", "napoli": "Naples",
    "ajax": "Amsterdam", "porto": "Porto", "benfica": "Lisbon",
}

def get_weather_for_match(match):
    """Récupère la météo de la ville du match, pas toujours Paris 😂"""
    sport = match.get('sport_key', '')
    # Basket en salle et tennis indoor : météo inutile
    if sport in ['nba', 'euroleague', 'eurocup', 'proA']:
        return None
    # Cherche la ville selon les équipes
    home = match.get('home_team', '').lower()
    for keyword, city in TEAM_CITY_MAP.items():
        if keyword in home:
            return get_weather(city)
    return get_weather()  # fallback Paris

def analyze_with_claude(match):
    if not GROQ_API_KEY:
        print("🚨 GROQ_API_KEY manquante sur Render !")
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

    prompt = f"""Tu es un expert en paris sportifs. Analyse ce match et réponds UNIQUEMENT en JSON valide sans aucun texte autour.

Match: {match.get('away_team')} @ {match.get('home_team')}
Sport: {match.get('sport_title')}
Heure: {match.get('commence_time')}
{f"Météo: {meteo_str}" if meteo_str != "Salle / non applicable" else "Météo: Irrelevante (sport en salle)"}{score_str}
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
        print(f"🚨 Erreur Groq : {e}")
        return None

# ============================================================
# SCAN VALUE BETS
# ============================================================
def scan_value_bets():
    global ALERTES
    nouvelles_alertes = []

    # IDs déjà alertés pour éviter les doublons
    ids_deja_alertes = {a.get('match_id') for a in ALERTES if a.get('match_id')}

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
                # Anti doublon — skip si déjà alerté
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

@app.route('/health')
def health_check():
    """Endpoint santé complet — vérifie toutes les clés et dépendances."""
    status = {
        "status": "ok",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "apis": {
            "groq":        "ok" if GROQ_API_KEY else "MANQUANTE",
            "cerebras":    "ok" if CEREBRAS_API_KEY else "non configuré",
            "odds_api":    "ok" if ODDS_API_KEY else "MANQUANTE",
            "apisports":   "ok" if APISPORTS_KEY else "MANQUANTE",
            "balldontlie": "ok" if BALLDONTLIE_KEY else "MANQUANTE",
            "openweather": "ok" if OPENWEATHER_KEY else "MANQUANTE",
            "redis":       "ok" if (UPSTASH_URL and UPSTASH_TOKEN) else "MANQUANTE",
        },
        "cache_size": len(CACHE),
        "alertes_actives": len(ALERTES),
        "chat_history_msgs": len(CHAT_HISTORY),
    }
    # Statut global
    apis_manquantes = [k for k, v in status["apis"].items() if v == "MANQUANTE"]
    if apis_manquantes:
        status["warnings"] = f"APIs manquantes: {', '.join(apis_manquantes)}"
    return jsonify(status)

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
    # 🏀 NBA : Scraping pur (Zéro clé API)
    if sport_id == "nba":
        return jsonify({"data": fetch_nba()})
    
    # ⚽ FOOT & BASKET EUROPE : Fallback sur TheSportsDB (Clé "3" gratuite)
    elif sport_id in SPORTSDB_LEAGUES:
        league_id = SPORTSDB_LEAGUES.get(sport_id)
        matchs = fetch_sportsdb_events(league_id)
        return jsonify({"data": matchs, "source": "thesportsdb"})
    
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

@app.route('/radar/cache/clear', methods=['POST'])
def clear_cache():
    """Vide le cache mémoire ET Redis (clés sportsdb uniquement)."""
    global CACHE
    # Vider le cache mémoire local
    cleared = len(CACHE)
    CACHE = {}
    # Vider les clés sportsdb dans Redis
    try:
        cleared_redis = 0
        for key in ["sportsdb_4966", "sportsdb_4967", "sportsdb_4387",
                    "sportsdb_4328", "sportsdb_4335", "sportsdb_4331",
                    "sportsdb_4334", "sportsdb_4332", "sportsdb_4480",
                    "sportsdb_4424", "sportsdb_4425", "sportsdb_4422",
                    "sport_nba", "sport_euroleague", "sport_eurocup",
                    "sport_epl", "sport_laliga", "sport_bundesliga",
                    "sport_ligue1", "sport_seriea", "sport_ucl"]:
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
# ENDPOINTS BOOKMAKERS — Soldes par bookmaker
# ============================================================
def load_bookmakers():
    data = redis_get('bookmakers_soldes')
    if data:
        return data
    # Valeurs par défaut de Gaël
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
    """Ajoute ou met a jour un bookmaker."""
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
    """Supprime un bookmaker."""
    data = request.get_json()
    nom = data.get('nom', '').strip()
    bks = load_bookmakers()
    bks = [b for b in bks if b['nom'].lower() != nom.lower()]
    save_bookmakers(bks)
    return jsonify({"status": "deleted", "bookmakers": bks, "total": round(sum(x['solde'] for x in bks), 2)})

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
    image_b64 = data.get('image')   # 1 image
    images_b64 = data.get('images', [])  # plusieurs images
    # Si plusieurs images envoyées, on les combine
    if images_b64 and not image_b64:
        image_b64 = images_b64[0]  # première pour compatibilité
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

    # Récupération cotes temps réel — SEULEMENT si message parle d un match précis
    # Guard anti-brulagequota : on appelle OddsAPI max 1 fois par message
    cotes_reelles = ""
    TRIGGER_COTES = ['cote','jouer','value','pari','mise','analyse','match','vs','contre']
    if message and any(s in message.lower() for s in TRIGGER_COTES):
        sport_key_detected = None
        if any(s in message.lower() for s in ['nba','basket','euroleague']):
            sport_key_detected = 'nba' if 'nba' in message.lower() else 'euroleague'
        elif any(s in message.lower() for s in ['tennis','atp','wta']):
            sport_key_detected = 'atp' if 'wta' not in message.lower() else 'wta'
        elif any(s in message.lower() for s in ['epl','premier','laliga','liga','bundesliga','ligue','serie','ucl','champions']):
            for sk in ['epl','laliga','bundesliga','ligue1','seriea','ucl']:
                if sk in message.lower() or APISPORTS_LEAGUES.get(sk,{}).get('nom','').lower() in message.lower():
                    sport_key_detected = sk
                    break
            if not sport_key_detected:
                sport_key_detected = 'epl'

        if sport_key_detected:
            odds_result = get_best_odds(sport_key_detected, message)
            if odds_result:
                source = odds_result.get("source", "inconnu")
                data_odds = odds_result.get("data")
                cotes_reelles = f"COTES REELLES ({source.upper()}) : {json.dumps(data_odds, ensure_ascii=False)[:500]}"

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
        "Tu es RADAR, le pote de Gael qui connait le sport mieux que personne. "
        "T es pas un assistant, t es pas un robot, t es le gars que Gael appelle quand il veut un vrai avis. "
        "Tu parles naturellement, avec du caractere. Tu peux dire franchement ce match je le sens pas, "
        "la cote est nulle la, attends je regarde ca, bah la t as pas le choix tu joues. "
        "Tu utilises des expressions naturelles, tu peux etre enthousiaste quand y a une vraie opportunite, "
        "decu quand les cotes sont mauvaises, cash quand y a rien a jouer. "
        "Tu tutoies Gael, tu te souviens des conversations et tu peux y faire reference. "
        "Pas de listes a puces sauf si vraiment necessaire. Tu causes, tu expliques comme a un pote. "
        "REGLES QUE TU RESPECTES TOUJOURS (meme en etant cool) : "
        "- JAMAIS inventer des cotes ou des stats. Si t as pas les donnees, tu dis j ai pas les cotes sous la main, "
        "jette un oeil sur Winamax et tu passes a autre chose. Pas de discours. "
        "- JAMAIS faire semblant de chercher des donnees que t as pas. "
        "- Un value bet ca doit avoir un edge de 15% MINIMUM. En dessous c est pas un value bet, c est du bruit. "
        "- Edge negatif = pas de value bet, tu le dis cash et sans detour. "
        "- Si edge entre 0 et 15% : tu dis que c est trop juste et tu conseilles pas de miser. "
        f"Bankroll de Gael : {bankroll_info}. "
        "Mise selon confiance : 70% = 2%, 80% = 4%, 90% = 6%, 100% = 8% de la bankroll. "
        f"Bookmakers de Gael : {bookmakers_str}. "
        "DONNEES DISPO dans ce contexte (utilise-les si presentes, invente rien si absentes) : "
        "TheSportsDB pour prochains matchs, OddsPortal/OddsAPI pour les cotes. "
        "FORMAT : court et percutant. Commence direct par le verdict. "
        "Bookmaker + cote + mise en euros seulement si t as les vraies donnees. "
        "Risque : FAIBLE / MOYEN / ELEVE. "
        "Si image : lis directement les cotes et stats visibles. "
        "METEO : Ne JAMAIS parler de meteo pour le basket (NBA, Euroleague, EuroCup, Pro A) "
        "ni pour le tennis — ca se joue en salle ou sur surface couverte, la meteo s en fout. "
        "La meteo est pertinente UNIQUEMENT pour le foot en exterieur. "
        "Toujours en francais. "
        f"{cotes_reelles}{forme_context}{news_context}"
    )

    # Construction du message utilisateur (texte + image si presente)
    if image_b64:
        user_content = [
            {"type": "text", "text": message or "Analyse ces images"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
        ]
        # Ajouter les images supplémentaires
        for extra_img in extra_images:
            user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{extra_img}"}})
        model = "meta-llama/llama-4-scout-17b-16e-instruct"
        chat_api_url = "https://api.groq.com/openai/v1/chat/completions"
        chat_api_key = GROQ_API_KEY
    else:
        user_content = message
        # Cerebras pour texte — ultra rapide
        if CEREBRAS_API_KEY:
            model = "qwen-3-32b"
            chat_api_url = "https://api.cerebras.ai/v1/chat/completions"
            chat_api_key = CEREBRAS_API_KEY
        else:
            model = "llama-3.3-70b-versatile"
            chat_api_url = "https://api.groq.com/openai/v1/chat/completions"
            chat_api_key = GROQ_API_KEY

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
            # Fallback Groq si Cerebras échoue
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
