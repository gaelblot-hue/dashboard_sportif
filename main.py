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
# CONFIGURATION & CLÉS
# ============================================================
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
BALLDONTLIE_KEY = os.getenv('BALLDONTLIE_KEY')
OPENWEATHER_KEY = os.getenv('OPENWEATHER_KEY')

ALERTES = []
CACHE = {}
CACHE_DURATION = 900 # 15 min

HISTORIQUE_FILE = "historique.json"
BANKROLL_FILE = "bankroll.json"
RESUME_FILE = "resume.json"

# ============================================================
# ROTATION DES SPORTS (FULL V6)
# ============================================================
SPORTS_ROTATION = [
    {"nom": "NBA",           "endpoint": "/nba/v1/games",      "odds": "/nba/v2/odds"},
    {"nom": "NHL",           "endpoint": "/nhl/v1/games",      "odds": "/nhl/v1/odds"},
    {"nom": "MLB",           "endpoint": "/mlb/v1/games",      "odds": "/mlb/v1/odds"},
    {"nom": "NFL",           "endpoint": "/nfl/v1/games",      "odds": "/nfl/v1/odds"},
    {"nom": "MMA",           "endpoint": "/mma/v1/fights",     "odds": "/mma/v1/odds"},
    {"nom": "EPL",           "endpoint": "/epl/v2/matches",    "odds": "/epl/v2/odds"},
    {"nom": "La Liga",       "endpoint": "/laliga/v1/matches", "odds": "/laliga/v1/odds"},
    {"nom": "Bundesliga",    "endpoint": "/bundesliga/v1/matches", "odds": "/bundesliga/v1/odds"},
    {"nom": "Ligue 1",       "endpoint": "/ligue1/v1/matches", "odds": "/ligue1/v1/odds"},
    {"nom": "Serie A",       "endpoint": "/seriea/v1/matches", "odds": "/seriea/v1/odds"},
    {"nom": "Champions League", "endpoint": "/ucl/v1/matches", "odds": "/ucl/v1/odds"},
    {"nom": "ATP Tennis",    "endpoint": "/atp/v1/matches",    "odds": "/atp/v1/odds"},
    {"nom": "WTA Tennis",    "endpoint": "/wta/v1/matches",    "odds": "/wta/v1/odds"},
    {"nom": "MLS",           "endpoint": "/mls/v1/matches",    "odds": "/mls/v1/odds"},
]

# ============================================================
# UTILITAIRES CACHE & JSON
# ============================================================
def get_cache(key):
    if key in CACHE:
        data, timestamp = CACHE[key]
        if time.time() - timestamp < CACHE_DURATION: return data
    return None

def set_cache(key, data):
    CACHE[key] = (data, time.time())

def load_json(filename, default):
    try:
        with open(filename, 'r') as f: return json.load(f)
    except: return default

def save_json(filename, data):
    with open(filename, 'w') as f: json.dump(data, f)

# ============================================================
# BALLDONTLIE & MÉTÉO
# ============================================================
def bdl_get(endpoint, params={}):
    cache_key = f"bdl_{endpoint}_{str(params)}"
    cached = get_cache(cache_key)
    if cached: return cached
    try:
        url = f"https://api.balldontlie.io{endpoint}"
        r = requests.get(url, headers={"Authorization": BALLDONTLIE_KEY}, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            set_cache(cache_key, data)
            return data
    except Exception as e: print(f"🚨 BDL Error: {e}")
    return None

def get_weather(city="Paris"):
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_KEY}&units=metric"
        r = requests.get(url, timeout=5)
        if r.status_code == 200: return r.json()
    except: return None
    return None

# ============================================================
# LOGIQUE MÉTIER
# ============================================================
def fetch_sport(sport):
    cache_key = f"sport_{sport['nom']}"
    cached = get_cache(cache_key)
    if cached: return cached

    today = datetime.now().strftime("%Y-%m-%d")
    data = bdl_get(sport["endpoint"], {"dates[]": today, "per_page": 100})
    if not data or not data.get('data'): return []

    odds_data = bdl_get(sport["odds"], {"date": today, "per_page": 100})
    odds_map = {}
    if odds_data and odds_data.get('data'):
        for odd in odds_data['data']:
            gid = odd.get('game_id') or odd.get('match_id') or odd.get('fight_id')
            if gid:
                if gid not in odds_map: odds_map[gid] = []
                odds_map[gid].append(odd)

    matchs = []
    for g in data['data']:
        gid = g.get('id')
        home = g.get('home_team', {})
        away = g.get('visitor_team', g.get('away_team', {}))
        
        cotes = []
        for o in odds_map.get(gid, [])[:5]:
            cotes.append({
                "bookmaker": o.get('book') or o.get('sportsbook', 'Unknown'),
                "home_cote": o.get('home_moneyline') or o.get('home_odds'),
                "away_cote": o.get('away_moneyline') or o.get('away_odds'),
                "over_under": o.get('over_under'),
                "spread": o.get('home_spread') or o.get('spread')
            })

        matchs.append({
            "id": str(gid),
            "sport_title": sport['nom'],
            "home_team": home.get('full_name') or home.get('name') or "Home",
            "away_team": away.get('full_name') or away.get('name') or "Away",
            "status": g.get('status', 'scheduled'),
            "cotes": cotes
        })
    set_cache(cache_key, matchs)
    return matchs

# ============================================================
# ENDPOINTS API (SANTÉ & DATA)
# ============================================================
@app.route('/')
def health(): return "RADAR V6 READY 📡"

@app.route('/radar/<sport_id>')
def get_sport(sport_id):
    sport_map = {
        "nba": "NBA", "nhl": "NHL", "mlb": "MLB", "nfl": "NFL",
        "mma": "MMA", "epl": "EPL", "laliga": "La Liga",
        "bundesliga": "Bundesliga", "ligue1": "Ligue 1",
        "seriea": "Serie A", "ucl": "Champions League",
        "atp": "ATP Tennis", "wta": "WTA Tennis", "mls": "MLS",
        "football": "EPL", "tennis": "ATP Tennis", "rugby": "EPL"
    }
    nom = sport_map.get(sport_id.lower())
    sport = next((s for s in SPORTS_ROTATION if s['nom'] == nom), None)
    return jsonify({"data": fetch_sport(sport) if sport else []})

@app.route('/radar/alertes')
def get_alertes(): return jsonify({"alertes": ALERTES})

# ============================================================
# ANALYSE & BANKROLL
# ============================================================
@app.route('/radar/analyze', methods=['POST'])
def analyze_match():
    data = request.get_json()
    match = data.get('match')
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = f"Analyse ce match (JSON strict) : {json.dumps(match)}"
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text
        return jsonify({"analyse": json.loads(raw[raw.find("{"):raw.rfind("}")+1])})
    except: return jsonify({"error": "Analyse échouée"}), 500

@app.route('/radar/bankroll')
def get_bankroll(): return jsonify(load_json(BANKROLL_FILE, {"total": 0, "disponible": 0, "mises": []}))

@app.route('/radar/bankroll/init', methods=['POST'])
def init_bankroll():
    montant = float(request.get_json().get('montant', 0))
    bk = {"total": montant, "disponible": montant, "mises": []}
    save_json(BANKROLL_FILE, bk)
    return jsonify({"status": "ok", "bankroll": bk})

@app.route('/radar/historique_pnl')
def get_historique_pnl():
    bk = load_json(BANKROLL_FILE, {"total": 0, "mises": []})
    mises = [m for m in bk.get('mises', []) if m.get('gain') is not None]
    mises.reverse()
    solde = bk.get('total', 0)
    points = [{"date": "Départ", "solde": solde, "gain": 0}]
    for m in mises:
        solde = round(solde + m['gain'], 2)
        points.append({"date": m['timestamp'], "solde": solde, "gain": m['gain']})
    return jsonify({"points": points, "total_initial": bk.get('total', 0)})

# ============================================================
# LANCEMENT (RENDER COMPLIANT)
# ============================================================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
  
