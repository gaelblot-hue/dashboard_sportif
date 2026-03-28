from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import anthropic
import time
import os
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ============================================================
# CLÉS API
# ============================================================
ODDS_API_KEY = os.getenv('ODDS_API_KEY')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
SPORTSDATA_API_KEY = os.getenv('SPORTSDATA_API_KEY')
BALLDONTLIE_KEY = os.getenv('BALLDONTLIE_KEY')
API_FOOTBALL_KEY = os.getenv('API_FOOTBALL_KEY')
SPORTS_API_PRO = os.getenv('SPORTS_API_PRO')
OPENWEATHER_KEY = os.getenv('OPENWEATHER_KEY')
GNEWS_API_KEY = os.getenv('GNEWS_API_KEY')
HIGHLIGHTLY_KEY = os.getenv('HIGHLIGHTLY_KEY')

# ============================================================
# MÉMOIRE & FICHIERS
# ============================================================
ALERTES = []
CHAT_HISTORY = []
CACHE = {}
CACHE_DURATION = 900  # 15 minutes

HISTORIQUE_FILE = "historique.json"
BANKROLL_FILE = "bankroll.json"
RESUME_FILE = "resume.json"

# ============================================================
# LIGUES ODDS API
# ============================================================
LIGUES = {
    "football": ["soccer_epl", "soccer_france_ligue_1", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_usa_mls"],
    "tennis": ["tennis_atp_miami", "tennis_wta_miami", "tennis_atp_challenger", "tennis_itf_men", "tennis_itf_women"],
    "hockey_eu": ["icehockey_sweden_allsvenskan", "icehockey_finland_liiga"],
    "rugby": ["rugbyunion_top_14", "rugbyunion_premiership", "rugbyunion_six_nations", "rugbyunion_championship", "rugbyleague_nrl"]
}

# ============================================================
# CACHE
# ============================================================
def get_cache(key):
    if key in CACHE:
        data, timestamp = CACHE[key]
        if time.time() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit : {key}")
            return data
    return None

def set_cache(key, data):
    CACHE[key] = (data, time.time())

# ============================================================
# CONVERSION MONEYLINE → DÉCIMAL
# ============================================================
def american_to_decimal(american):
    try:
        american = float(american)
        if american > 0:
            return round((american / 100) + 1, 2)
        else:
            return round((100 / abs(american)) + 1, 2)
    except:
        return None

def format_odds_sportsdata(game):
    pregame = game.get('PregameOdds', [])
    bookmakers = {}
    for odd in pregame[:10]:
        sportsbook = odd.get('Sportsbook', 'Unknown')
        if sportsbook == 'Scrambled':
            continue
        if sportsbook not in bookmakers:
            away_ml = odd.get('AwayMoneyLine')
            home_ml = odd.get('HomeMoneyLine')
            bookmakers[sportsbook] = {
                "title": sportsbook,
                "url": odd.get('SportsbookUrl', ''),
                "away_moneyline_us": away_ml,
                "home_moneyline_us": home_ml,
                "away_cote_eu": american_to_decimal(away_ml),
                "home_cote_eu": american_to_decimal(home_ml),
                "spread": odd.get('AwayPointSpread'),
                "over_under": odd.get('OverUnder'),
                "over_payout_eu": american_to_decimal(odd.get('OverPayout')),
                "under_payout_eu": american_to_decimal(odd.get('UnderPayout'))
            }
    return list(bookmakers.values())

# ============================================================
# MÉTÉO
# ============================================================
def get_weather(city="Paris"):
    cached = get_cache(f"weather_{city}")
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
                "ressenti": data['main']['feels_like'],
                "conditions": data['weather'][0]['description'],
                "vent": data['wind']['speed'],
                "humidite": data['main']['humidity'],
                "pluie": 'rain' in data['weather'][0]['main'].lower()
            }
            set_cache(f"weather_{city}", meteo)
            return meteo
    except Exception as e:
        print(f"Erreur météo : {e}")
    return None

# ============================================================
# SPORTSDATA IO — Sports US
# ============================================================
def fetch_sportsdata(sport, endpoint):
    cache_key = f"sportsdata_{sport}_{endpoint}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    today = datetime.now().strftime("%Y-%b-%d").upper()
    try:
        url = f"https://api.sportsdata.io/v3/{sport}/odds/json/{endpoint}/{today}?key={SPORTSDATA_API_KEY}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and isinstance(r.json(), list):
            set_cache(cache_key, r.json())
            return r.json()
        return []
    except Exception as e:
        print(f"Erreur SportsDataIO {sport}: {e}")
        return []

def build_match_sportsdata(game, sport_key, sport_title):
    bookmakers = format_odds_sportsdata(game)
    if not bookmakers:
        return None
    return {
        "id": str(game.get('GameId', game.get('FightId', ''))),
        "sport_key": sport_key,
        "sport_title": sport_title,
        "commence_time": game.get('DateTime'),
        "home_team": game.get('HomeTeamName', game.get('Fighter1', '')),
        "away_team": game.get('AwayTeamName', game.get('Fighter2', '')),
        "status": game.get('Status'),
        "bookmakers_sportsdata": bookmakers,
        "source": "sportsdata"
    }

def fetch_nba_sportsdata():
    return [m for m in [build_match_sportsdata(g, "basketball_nba", "NBA") for g in fetch_sportsdata("nba", "GameOddsByDate")] if m]

def fetch_nhl_sportsdata():
    return [m for m in [build_match_sportsdata(g, "icehockey_nhl", "NHL") for g in fetch_sportsdata("nhl", "GameOddsByDate")] if m]

def fetch_mlb_sportsdata():
    return [m for m in [build_match_sportsdata(g, "baseball_mlb", "MLB") for g in fetch_sportsdata("mlb", "GameOddsByDate")] if m]

def fetch_nfl_sportsdata():
    return [m for m in [build_match_sportsdata(g, "americanfootball_nfl", "NFL") for g in fetch_sportsdata("nfl", "GameOddsByDate")] if m]

def fetch_mma_sportsdata():
    return [m for m in [build_match_sportsdata(g, "mma", "MMA") for g in fetch_sportsdata("mma", "GameOddsByDate")] if m]

# ============================================================
# ODDS API — Sports EU + Tennis (avec cache)
# ============================================================
def fetch_global_data(sport_key):
    cached = get_cache(f"odds_{sport_key}")
    if cached:
        return cached

    all_results = []
    for league in LIGUES.get(sport_key, []):
        try:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                all_results.extend(r.json())
            time.sleep(0.1)
        except:
            continue

    all_results.sort(key=lambda x: x.get('commence_time', ''))
    set_cache(f"odds_{sport_key}", all_results)
    return all_results

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
    meteo = get_weather()
    source = match.get('source', 'odds_api')

    odds_summary = ""
    if source == 'sportsdata':
        for b in match.get('bookmakers_sportsdata', [])[:5]:
            odds_summary += f"\n- {b['title']}: Away {b['away_cote_eu']} | Home {b['home_cote_eu']}"
            if b.get('spread'):
                odds_summary += f" | Spread: {b['spread']}"
            if b.get('over_under'):
                odds_summary += f" | O/U: {b['over_under']}"
    else:
        for b in match.get('bookmakers', [])[:3]:
            odds_summary += f"\n- {b['title']}: "
            for m in b.get('markets', []):
                for o in m.get('outcomes', []):
                    odds_summary += f"{o['name']}@{o['price']} "

    meteo_str = f"{meteo['temp']}°C, {meteo['conditions']}, vent {meteo['vent']}m/s" if meteo else "Stable"

    prompt = f"""Expert Radar V5 - Analyse Paris Sportifs.
Match: {match.get('away_team')} @ {match.get('home_team')}
Sport: {match.get('sport_title', match.get('sport_key'))}
Heure: {match.get('commence_time')}
Météo: {meteo_str}
Cotes (décimal européen): {odds_summary}

Réponds UNIQUEMENT en JSON valide :
{{
  "value_bet": true ou false,
  "confiance": nombre entre 0 et 10,
  "pari_recommande": "conseil court",
  "cote": nombre décimal ex: 2.45,
  "bookmaker": "nom",
  "raison": "explication courte",
  "risque": "risque principal",
  "impact_meteo": "aucun ou description",
  "mise_conseillee": "% bankroll"
}}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        res_text = response.content[0].text
        raw = res_text[res_text.find("{"):res_text.rfind("}")+1]
        return json.loads(raw)
    except Exception as e:
        print(f"Erreur Claude : {e}")
        return None

# ============================================================
# SCAN VALUE BETS
# ============================================================
def scan_value_bets():
    global ALERTES
    nouvelles_alertes = []

    tous_matchs = []
    tous_matchs.extend(fetch_nba_sportsdata()[:3])
    tous_matchs.extend(fetch_nhl_sportsdata()[:3])
    tous_matchs.extend(fetch_mlb_sportsdata()[:3])
    tous_matchs.extend(fetch_nfl_sportsdata()[:2])
    tous_matchs.extend(fetch_mma_sportsdata()[:2])
    for sport_key in LIGUES.keys():
        tous_matchs.extend(fetch_global_data(sport_key)[:2])

    for match in tous_matchs:
        if not match.get('bookmakers') and not match.get('bookmakers_sportsdata'):
            continue
        analyse = analyze_with_claude(match)
        if not analyse:
            continue
        if analyse.get('value_bet') and analyse.get('confiance', 0) >= 7:
            nouvelles_alertes.append({
                "id": f"{match.get('id', '')}_{int(time.time())}",
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "match": f"{match.get('away_team')} @ {match.get('home_team')}",
                "sport": match.get('sport_title', match.get('sport_key')),
                "confiance": analyse.get('confiance'),
                "pari": analyse.get('pari_recommande'),
                "cote": analyse.get('cote'),
                "bookmaker": analyse.get('bookmaker'),
                "raison": analyse.get('raison'),
                "risque": analyse.get('risque'),
                "impact_meteo": analyse.get('impact_meteo', 'aucun')
            })
        time.sleep(1)

    ALERTES = (nouvelles_alertes + ALERTES)[:20]
    print(f"✅ Scan terminé : {len(nouvelles_alertes)} value bets détectés")

# ============================================================
# RÉSUMÉ QUOTIDIEN
# ============================================================
def generate_daily_resume():
    tous_matchs = []
    tous_matchs.extend(fetch_nba_sportsdata()[:3])
    tous_matchs.extend(fetch_nhl_sportsdata()[:2])
    tous_matchs.extend(fetch_mlb_sportsdata()[:2])
    tous_matchs.extend(fetch_global_data("football")[:2])
    tous_matchs.extend(fetch_global_data("tennis")[:2])
    tous_matchs.extend(fetch_global_data("rugby")[:2])

    if not tous_matchs:
        return None

    matchs_str = "\n".join([
        f"- {m.get('sport_title', m.get('sport_key'))} : {m.get('away_team')} @ {m.get('home_team')} | {m.get('commence_time')}"
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
  "sports_chauds": ["NBA", "Football"]
}}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        res_text = response.content[0].text
        raw = res_text[res_text.find("{"):res_text.rfind("}")+1]
        resume = json.loads(raw)
        with open(RESUME_FILE, 'w') as f:
            json.dump(resume, f)
        return resume
    except Exception as e:
        print(f"Erreur résumé : {e}")
        return None

# ============================================================
# ENDPOINTS TEST
# ============================================================
@app.route('/')
def health():
    return "RADAR V5.4 : SYSTEM READY 📡🏀🏒⚾🏈🥊⚽🎾🏉"

@app.route('/test_all_apis')
def test_all_apis():
    results = {}
    today = datetime.now().strftime("%Y-%b-%d").upper()

    # SportsDataIO NBA
    try:
        r = requests.get(f"https://api.sportsdata.io/v3/nba/odds/json/GameOddsByDate/{today}?key={SPORTSDATA_API_KEY}", timeout=5)
        results["SportsDataIO_NBA"] = {"status": r.status_code, "matchs": len(r.json()) if r.status_code == 200 else 0}
    except Exception as e:
        results["SportsDataIO_NBA"] = {"erreur": str(e)}

    # BallDontLie
    try:
        r = requests.get("https://api.balldontlie.io/v1/games",
            headers={"Authorization": BALLDONTLIE_KEY}, timeout=5)
        results["BallDontLie"] = {"status": r.status_code, "matchs": len(r.json().get('data', []))}
    except Exception as e:
        results["BallDontLie"] = {"erreur": str(e)}

    # API Football
    try:
        r = requests.get("https://v3.football.api-sports.io/fixtures?live=all",
            headers={"x-apisports-key": API_FOOTBALL_KEY}, timeout=5)
        results["API_Football"] = {"status": r.status_code, "matchs": len(r.json().get('response', []))}
    except Exception as e:
        results["API_Football"] = {"erreur": str(e)}

    # Sports API Pro
    try:
        r = requests.get("https://api.sportsapipro.com/v1/sports",
            headers={"Authorization": SPORTS_API_PRO}, timeout=5)
        results["SportsApiPro"] = {"status": r.status_code}
    except Exception as e:
        results["SportsApiPro"] = {"erreur": str(e)}

    # OpenWeather
    try:
        r = requests.get(f"http://api.openweathermap.org/data/2.5/weather?q=Paris&appid={OPENWEATHER_KEY}&units=metric", timeout=5)
        data = r.json()
        results["OpenWeather"] = {
            "status": r.status_code,
            "temp": data['main']['temp'] if r.status_code == 200 else None,
            "conditions": data['weather'][0]['description'] if r.status_code == 200 else None
        }
    except Exception as e:
        results["OpenWeather"] = {"erreur": str(e)}

    # GNews
    try:
        r = requests.get(f"https://gnews.io/api/v4/top-headlines?topic=sports&lang=fr&token={GNEWS_API_KEY}", timeout=5)
        results["GNews"] = {"status": r.status_code, "articles": len(r.json().get('articles', []))}
    except Exception as e:
        results["GNews"] = {"erreur": str(e)}

    # Highlightly
    try:
        r = requests.get("https://api.highlightly.net/highlights",
            headers={"x-rapidapi-key": HIGHLIGHTLY_KEY}, timeout=5)
        results["Highlightly"] = {"status": r.status_code}
    except Exception as e:
        results["Highlightly"] = {"erreur": str(e)}

    # Odds API
    try:
        r = requests.get(f"https://api.the-odds-api.com/v4/sports/?apiKey={ODDS_API_KEY}", timeout=5)
        results["Odds_API"] = {"status": r.status_code, "sports": len(r.json()) if r.status_code == 200 else 0}
    except Exception as e:
        results["Odds_API"] = {"erreur": str(e)}

    return jsonify({"date": today, "resultats": results})

# ============================================================
# ENDPOINTS RADAR
# ============================================================
@app.route('/radar/global_basket')
def get_basket():
    return jsonify({"data": fetch_nba_sportsdata()})

@app.route('/radar/nhl')
def get_nhl():
    return jsonify({"data": fetch_nhl_sportsdata()})

@app.route('/radar/global_hockey')
def get_hockey():
    return jsonify({"data": fetch_nhl_sportsdata()})

@app.route('/radar/mlb')
def get_mlb():
    return jsonify({"data": fetch_mlb_sportsdata()})

@app.route('/radar/nfl')
def get_nfl():
    return jsonify({"data": fetch_nfl_sportsdata()})

@app.route('/radar/mma')
def get_mma():
    return jsonify({"data": fetch_mma_sportsdata()})

@app.route('/radar/football')
def get_foot():
    return jsonify({"data": fetch_global_data("football")})

@app.route('/radar/tennis')
def get_tennis():
    return jsonify({"data": fetch_global_data("tennis")})

@app.route('/radar/hockey_eu')
def get_hockey_eu():
    return jsonify({"data": fetch_global_data("hockey_eu")})

@app.route('/radar/rugby')
def get_rugby():
    return jsonify({"data": fetch_global_data("rugby")})

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
        "sport": match.get('sport_key', ''),
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
# =============
