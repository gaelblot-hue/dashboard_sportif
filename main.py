from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

# --- ARSENAL (API KEYS) ---
ODDS_API_KEY = os.getenv('ODDS_API_KEY')

# --- LE COFFRE-FORT (CACHE) ---
cache = {
    "soccer": {"data": None, "last": 0},
    "tennis": {"data": None, "last": 0},
    "nba": {"data": None, "last": 0},
    "nhl": {"data": None, "last": 0}
}

def get_odds(sport_key, markets="h2h,totals"):
    current = time.time()
    # On gère le cache pour ne pas bouffer les clés (5 min)
    sport_type = sport_key.split('_')[0] if '_' in sport_key else sport_key
    if cache.get(sport_type) and (current - cache[sport_type]["last"] < 300):
        return cache[sport_type]["data"]

    try:
        # Configuration multi-bookmakers (Pinnacle est la référence mondiale)
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets={markets}&bookmakers=pinnacle,unibet_fr,betclic_fr"
        response = requests.get(url)
        data = response.json()
        cache[sport_type] = {"data": data, "last": current}
        return data
    except:
        return []

@app.route('/')
def home():
    return "RADAR V5 ULTRA : TOUS SYSTÈMES OPÉRATIONNELS 📡"

@app.route('/radar/football')
def radar_foot():
    # On cible la Ligue des Champions (UEfA) car c'est là qu'il y a le plus de jus
    # Si tu veux la France, remplace par soccer_france_ligue_1
    data = get_odds("soccer_uefa_champions_league")
    return jsonify({"sport": "FOOTBALL", "data": data})

@app.route('/radar/tennis')
def radar_tennis():
    # On cible l'ATP mondial
    data = get_odds("tennis_atp_wimbledon") 
    return jsonify({"sport": "TENNIS", "data": data})

@app.route('/radar/basketball')
def radar_basket():
    # NBA avec Marché Over/Under (totals) et Ecarts (spreads)
    data = get_odds("basketball_nba", markets="h2h,totals,spreads")
    return jsonify({"sport": "BASKET", "data": data})

@app.route('/radar/nhl')
def radar_nhl():
    # Hockey Américain
    data = get_odds("icehockey_nhl")
    return jsonify({"sport": "NHL", "data": data})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
  
