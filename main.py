from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
# anthropic remplacé par Groq
import time
import os
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ============================================================
# CLÉS API
# ============================================================
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')  # gardé pour compatibilité
BALLDONTLIE_KEY   = os.getenv('BALLDONTLIE_KEY')
OPENWEATHER_KEY   = os.getenv('OPENWEATHER_KEY')
APISPORTS_KEY     = os.getenv('APISPORTS_KEY')
GROQ_API_KEY      = os.getenv('GROQ_API_KEY')

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

HISTORIQUE_FILE = "historique.json"
BANKROLL_FILE   = "bankroll.json"
RESUME_FILE     = "resume.json"

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
    "amicaux":    {"id": 151, "nom": "Matchs Amicaux"},       # Amicaux internationaux
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

def fetch_football_today(league_id):
    today = datetime.now().strftime("%Y-%m-%d")
    return apisports_get("/fixtures", {"league": league_id, "date": today, "season": 2025}, "football")

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
    today = datetime.now().strftime("%Y-%m-%d")
    return bdl_get("/nba/v1/games", {"dates[]": today, "per_page": 15})

def fetch_nba_odds():
    today = datetime.now().strftime("%Y-%m-%d")
    return bdl_get("/nba/v2/odds", {"date": today, "per_page": 15})

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
        return []

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

    # Matchs du jour
    today_data = fetch_football_today(league["id"])
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
    try:
        with open(HISTORIQUE_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_historique(entry):
    historique = load_historique()
    historique.insert(0, entry)
    historique = historique[:100]
    with open(HISTORIQUE_FILE, 'w') as f:
        json.dump(historique, f)

def load_bankroll():
    try:
        with open(BANKROLL_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"total": 0, "disponible": 0, "mises": []}

def save_bankroll(data):
    with open(BANKROLL_FILE, 'w') as f:
        json.dump(data, f)

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
                "model": "llama3-70b-8192",
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
                    nouvelles_alertes.append({
                        "id": f"{match.get('id')}_{int(time.time())}",
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "match": f"{match.get('away_team')} @ {match.get('home_team')}",
                        "sport": match.get('sport_title'),
                        "confiance": analyse.get('confiance'),
                        "pari": analyse.get('pari_recommande'),
                        "cote": analyse.get('cote'),
                        "bookmaker": analyse.get('bookmaker'),
                        "raison": analyse.get('raison'),
                        "risque": analyse.get('risque'),
                        "impact_meteo": analyse.get('impact_meteo', 'aucun')
                    })
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
                "model": "llama3-70b-8192",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "max_tokens": 1000
            },
            timeout=15
        )
        res = r.json()
        resume = json.loads(res['choices'][0]['message']['content'])
        with open(RESUME_FILE, 'w') as f:
            json.dump(resume, f)
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
    with open(HISTORIQUE_FILE, 'w') as f:
        json.dump(historique, f)
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
    try:
        with open(RESUME_FILE, 'r') as f:
            return jsonify(json.load(f))
    except:
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

    if data.get('reset'):
        CHAT_HISTORY = []
        return jsonify({"status": "reset"})

    if not message:
        return jsonify({"error": "Message vide"}), 400

    system_prompt = f"""Tu es RADAR, l'IA du dashboard paris sportifs.
Sois bref, précis et professionnel. Réponds toujours en français.
Bankroll disponible : {load_bankroll().get('disponible', 0)}€
Sources : BallDontLie (NBA), API-Sports (Football européen, Basket Europe)
Météo : OpenWeatherMap"""

    CHAT_HISTORY.append({"role": "user", "content": message})

    try:
        messages = [{"role": "system", "content": system_prompt}] + CHAT_HISTORY[-6:]
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama3-70b-8192",
                "messages": messages,
                "max_tokens": 400
            },
            timeout=10
        )
        res = r.json()
        print(f"🔍 Groq chat réponse : {res}")

        if 'choices' in res and len(res['choices']) > 0:
            reply = res['choices'][0]['message']['content']
        else:
            reply = "Signal Groq reçu mais illisible."

        CHAT_HISTORY.append({"role": "assistant", "content": reply})
        return jsonify({"reply": reply})

    except Exception as e:
        print(f"🚨 Erreur chat Groq : {e}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        print(f"Erreur chat Groq : {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
