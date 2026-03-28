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

# --- CLÉS API ---
ODDS_API_KEY = os.getenv('ODDS_API_KEY')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
SPORTSDATA_API_KEY = os.getenv('SPORTSDATA_API_KEY')

# --- MÉMOIRE VOLATILE ---
ALERTES = []
CHAT_HISTORY = []

# --- FICHIERS ---
HISTORIQUE_FILE = "historique.json"
BANKROLL_FILE = "bankroll.json"
RESUME_FILE = "resume.json"

# --- LIGUES ODDS API ---
LIGUES = {
    "hockey": ["icehockey_nhl", "icehockey_sweden_allsvenskan"],
    "football": ["soccer_epl", "soccer_france_ligue_1", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_usa_mls"],
    "tennis": ["tennis_atp_miami", "tennis_wta_miami", "tennis_atp_challenger", "tennis_itf_men", "tennis_itf_women"],
    "rugby": ["rugbyunion_top_14", "rugbyunion_premiership", "rugbyunion_six_nations", "rugbyunion_championship", "rugbyleague_nrl"]
}

# ============================================================
# CONVERSION UNIVERSELLE MONEYLINE → DÉCIMAL
# ============================================================

def american_to_decimal(american):
    """Convertit cote américaine en décimale européenne"""
    try:
        american = float(american)
        if american > 0:
            return round((american / 100) + 1, 2)
        else:
            return round((100 / abs(american)) + 1, 2)
    except:
        return None

def format_odds_sportsdata(game):
    """Formate les cotes SportsDataIO en format lisible avec conversion décimale"""
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
# MÉTÉO (Open-Meteo, gratuit)
# ============================================================

def get_weather_for_match(lat=25.76, lon=-80.19):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        r = requests.get(url, timeout=5).json()
        w = r.get('current_weather', {})
        return {
            "temp": w.get('temperature'),
            "vent": w.get('windspeed'),
            "condition_code": w.get('weathercode'),
            "is_day": w.get('is_day')
        }
    except:
        return None

# ============================================================
# SPORTSDATA IO
# ============================================================

def fetch_sportsdata(sport, endpoint):
    today = datetime.now().strftime("%Y-%b-%d").upper()
    try:
        url = f"https://api.sportsdata.io/v3/{sport}/odds/json/{endpoint}/{today}?key={SPORTSDATA_API_KEY}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and isinstance(r.json(), list):
            return r.json()
        return []
    except Exception as e:
        print(f"Erreur SportsDataIO {sport}: {e}")
        return []

def fetch_nba_sportsdata():
    matchs = []
    for game in fetch_sportsdata("nba", "GameOddsByDate"):
        bookmakers = format_odds_sportsdata(game)
        if not bookmakers:
            continue
        matchs.append({
            "id": str(game.get('GameId')),
            "sport_key": "basketball_nba",
            "sport_title": "NBA",
            "commence_time": game.get('DateTime'),
            "home_team": game.get('HomeTeamName'),
            "away_team": game.get('AwayTeamName'),
            "status": game.get('Status'),
            "bookmakers_sportsdata": bookmakers,
            "source": "sportsdata"
        })
    return matchs

def fetch_tennis_sportsdata():
    matchs = []
    for game in fetch_sportsdata("tennis", "TennisOddsByDate"):
        bookmakers = format_odds_sportsdata(game)
        if not bookmakers:
            continue
        matchs.append({
            "id": str(game.get('MatchId', game.get('GameId', ''))),
            "sport_key": "tennis",
            "sport_title": "Tennis",
            "commence_time": game.get('DateTime'),
            "home_team": game.get('HomeTeamName', game.get('Player1', '')),
            "away_team": game.get('AwayTeamName', game.get('Player2', '')),
            "status": game.get('Status'),
            "bookmakers_sportsdata": bookmakers,
            "source": "sportsdata"
        })
    return matchs

def fetch_football_sportsdata():
    matchs = []
    for game in fetch_sportsdata("soccer", "GameOddsByDate"):
        bookmakers = format_odds_sportsdata(game)
        if not bookmakers:
            continue
        matchs.append({
            "id": str(game.get('GameId')),
            "sport_key": "soccer",
            "sport_title": "Football",
            "commence_time": game.get('DateTime'),
            "home_team": game.get('HomeTeamName'),
            "away_team": game.get('AwayTeamName'),
            "status": game.get('Status'),
            "bookmakers_sportsdata": bookmakers,
            "source": "sportsdata"
        })
    return matchs

def fetch_mlb_sportsdata():
    matchs = []
    for game in fetch_sportsdata("mlb", "GameOddsByDate"):
        bookmakers = format_odds_sportsdata(game)
        if not bookmakers:
            continue
        matchs.append({
            "id": str(game.get('GameId')),
            "sport_key": "baseball_mlb",
            "sport_title": "MLB",
            "commence_time": game.get('DateTime'),
            "home_team": game.get('HomeTeamName'),
            "away_team": game.get('AwayTeamName'),
            "status": game.get('Status'),
            "bookmakers_sportsdata": bookmakers,
            "source": "sportsdata"
        })
    return matchs

# ============================================================
# ODDS API
# ============================================================

def fetch_global_data(sport_key):
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

# ============================================================
# ANALYSE IA
# ============================================================

def analyze_with_claude(match):
    meteo = get_weather_for_match()
    source = match.get('source', 'odds_api')
    odds_summary = ""
    if source == 'sportsdata':
        for b in match.get('bookmakers_sportsdata', [])[:5]:
            odds_summary += f"\n- {b['title']}: Away {b['away_cote_eu']} | Home {b['home_cote_eu']}"
    else:
        for b in match.get('bookmakers', [])[:3]:
            odds_summary += f"\n- {b['title']}: "
            for m in b.get('markets', []):
                for o in m.get('outcomes', []):
                    odds_summary += f"{o['name']}@{o['price']} "

    prompt = f"Analyse match: {match.get('away_team')} @ {match.get('home_team')}\nCotes: {odds_summary}\nRéponds en JSON avec value_bet (bool), confiance (0-10), pari_recommande, cote, bookmaker, raison."

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
    except:
        return None

# ============================================================
# SCAN & ENDPOINTS
# ============================================================

def scan_value_bets():
    global ALERTES
    # Scan simplifié pour le test
    ALERTES = [{"timestamp": datetime.now().strftime("%H:%M"), "match": "SCAN ACTIF", "status": "OK"}]

@app.route('/')
def health():
    return "RADAR V5.4 : SYSTEM READY 📡"

@app.route('/test_ultime_sports')
def test_all_sports():
    today = datetime.now().strftime("%Y-%b-%d").upper()
    results = {}
    targets = {
        "NBA": f"https://api.sportsdata.io/v3/nba/odds/json/GameOddsByDate/{today}",
        "TENNIS": f"https://api.sportsdata.io/v3/tennis/odds/json/TennisOddsByDate/{today}",
        "FOOT_EPL": f"https://api.sportsdata.io/v3/soccer/odds/json/GameOddsByDate/{today}"
    }
    for sport, url in targets.items():
        try:
            r = requests.get(f"{url}?key={SPORTSDATA_API_KEY}", timeout=10)
            results[sport] = {"status": "✅ OK", "matchs": len(r.json())} if r.status_code == 200 else {"status": f"❌ {r.status_code}"}
        except:
            results[sport] = {"status": "🚨 CRASH"}
    return jsonify({"resultats": results})

@app.route('/radar/historique')
def get_historique():
    historique = load_historique()
    total = len(historique)
    value_bets = [h for h in historique if h.get('value_bet')]
    return jsonify({
        "historique": historique,
        "stats": {
            "total": total,
            "value_bets": len(value_bets)
        }
    })

# --- DÉMARRAGE DU MOTEUR ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
  
