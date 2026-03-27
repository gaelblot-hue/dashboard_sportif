from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

# --- ARSENAL (API KEYS) ---
ODDS_API_KEY = os.getenv('ODDS_API_KEY')

# --- LE COFFRE-FORT (CACHE MULTI-SPORTS) ---
# On sépare les mémoires pour ne pas tout rafraîchir en même temps
cache = {
    "soccer": {"data": None, "last": 0},
    "tennis": {"data": None, "last": 0},
    "nba": {"data": None, "last": 0},
    "nhl": {"data": None, "last": 0}
}

def get_odds(sport_key, markets="h2h,totals"):
    """Fonction générique pour récupérer les cotes avec cache de 5 min"""
    current = time.time()
    sport_short = sport_key.split('_')[0] if '_' in sport_key else sport_key
    
    # RÈGLE ÉCO : 5 minutes (300s) pour coller au Live sans cramer le quota
    if cache.get(sport_short) and (current - cache[sport_short]["last"] < 300):
        return cache[sport_short]["data"]

    try:
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets={markets}&bookmakers=pinnacle,unibet_fr,betclic_fr"
        response = requests.get(url)
        data = response.json()
        cache[sport_short] = {"data": data, "last": current}
        return data
    except:
        return []

@app.route('/')
def home():
    return "RADAR V5 ULTRA : MULTIPLEX ACTIF 📡⚽🎾🏀🏒"

@app.route('/radar/football')
def radar_foot():
    # On cible la Ligue 1 et la Champions League
    data = get_odds("soccer_france_ligue_1")
    return jsonify({"sport": "FOOTBALL", "markets": "Over/Under & MT", "data": data})

@app.route('/radar/tennis')
def radar_tennis():
    # On cible l'ATP
    data = get_odds("tennis_atp_wimbledon") # Dynamique selon tournoi en cours
    return jsonify({"sport": "TENNIS", "markets": "Sets & Jeux", "data": data})

@app.route('/radar/basketball')
def radar_basket():
    data = get_odds("basketball_nba", markets="h2h,totals,spreads")
    return jsonify({"sport": "BASKET", "markets": "Match/MT/Quarts", "data": data})

@app.route('/radar/nhl')
def radar_nhl():
    data = get_odds("icehockey_nhl")
    return jsonify({"sport": "NHL", "markets": "PowerPlay Target", "data": data})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
  
