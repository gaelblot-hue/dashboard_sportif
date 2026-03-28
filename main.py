from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import anthropic
import time
import os
import json
from datetime import datetime

app = Flask(__name__)
CORS(app) # CRITIQUE : Pour que ton Dashboard puisse parler au serveur

# ============================================================
# CONFIGURATION & CLÉS (Récupérées sur Render)
# ============================================================
ODDS_API_KEY = os.getenv('ODDS_API_KEY')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
SPORTSDATA_API_KEY = os.getenv('SPORTSDATA_API_KEY')
BALLDONTLIE_KEY = os.getenv('BALLDONTLIE_KEY')

# Cache simple en mémoire
CACHE = {}
CACHE_DURATION = 900 # 15 minutes

# ============================================================
# LIGUES CONFIGURATION
# ============================================================
LIGUES = {
    "football": ["soccer_epl", "soccer_france_ligue_1", "soccer_spain_la_liga", "soccer_germany_bundesliga"],
    "tennis": ["tennis_atp_miami", "tennis_wta_miami"],
    "rugby": ["rugbyunion_top_14"]
}

# ============================================================
# FONCTIONS UTILES
# ============================================================
def american_to_decimal(american):
    try:
        if not american: return 1.0
        american = float(american)
        return round((american / 100) + 1, 2) if american > 0 else round((100 / abs(american)) + 1, 2)
    except: return 1.0

def get_cache(key):
    if key in CACHE:
        data, ts = CACHE[key]
        if time.time() - ts < CACHE_DURATION: return data
    return None

# ============================================================
# EXTRACTION SPORTSDATAIO (Bloc US : NBA, NHL, MLB)
# ============================================================
def fetch_us_odds(sport):
    cache_key = f"us_{sport}"
    cached = get_cache(cache_key)
    if cached: return cached

    today = datetime.now().strftime("%Y-%b-%d").upper()
    url = f"https://api.sportsdata.io/v3/{sport}/odds/json/GameOddsByDate/{today}?key={SPORTSDATA_API_KEY}"
    
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            games = r.json()
            matchs = []
            for g in games:
                odds = g.get('PregameOdds', [])
                if not odds: continue
                matchs.append({
                    "id": str(g.get('GameId')),
                    "sport_key": sport,
                    "sport_title": sport.upper(),
                    "home_team": g.get('HomeTeamName'),
                    "away_team": g.get('AwayTeamName'),
                    "commence_time": g.get('DateTime'),
                    "source": "sportsdata",
                    "odds_eu": {
                        "home": american_to_decimal(odds[0].get('HomeMoneyLine')),
                        "away": american_to_decimal(odds[0].get('AwayMoneyLine'))
                    }
                })
            CACHE[cache_key] = (matchs, time.time())
            return matchs
    except: return []
    return []

# ============================================================
# EXTRACTION ODDS API (Bloc Europe : Foot, Rugby, Tennis)
# ============================================================
def fetch_eu_odds(sport_type):
    cache_key = f"eu_{sport_type}"
    cached = get_cache(cache_key)
    if cached: return cached

    results = []
    for league in LIGUES.get(sport_type, []):
        url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu&oddsFormat=decimal"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                results.extend(r.json())
        except: continue
    
    CACHE[cache_key] = (results, time.time())
    return results

# ============================================================
# ROUTES POUR LE DASHBOARD (Harmonisées)
# ============================================================
@app.route('/')
def health():
    return jsonify({"status": "active", "engine": "Radar V5.4 Ultra"})

@app.route('/radar/nba') # Harmonisé avec l'Index
def get_nba():
    return jsonify({"data": fetch_us_odds("nba")})

@app.route('/radar/nhl')
def get_nhl():
    return jsonify({"data": fetch_us_odds("nhl")})

@app.route('/radar/football')
def get_foot():
    return jsonify({"data": fetch_eu_odds("football")})

@app.route('/radar/tennis')
def get_tennis():
    return jsonify({"data": fetch_eu_odds("tennis")})

# ============================================================
# ANALYSE IA (Le Cerveau Claude)
# ============================================================
@app.route('/radar/analyze', methods=['POST'])
def analyze():
    match = request.json.get('match')
    if not match: return jsonify({"error": "No match"}), 400

    prompt = f"""Analyse ce match pour un pari : {match['home_team']} vs {match['away_team']}. 
    Sport: {match.get('sport_title')}. Cotes: {match.get('odds_eu', 'Standard')}.
    Réponds UNIQUEMENT en JSON: 
    {{"pari_recommande": "X", "raison": "X", "confiance": 8, "mise_conseillee": "2%"}}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        # Nettoyage et envoi du JSON à l'index
        return jsonify({"analyse": json.loads(resp.content[0].text)})
    except:
        return jsonify({"analyse": {"pari_recommande": "Erreur IA", "raison": "Liaison Claude perdue", "confiance": 0, "mise_conseillee": "0"}})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
