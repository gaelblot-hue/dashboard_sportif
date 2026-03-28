from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
ODDS_API_KEY = os.getenv('ODDS_API_KEY')
CACHE_TTL = 60  # On descend à 60 secondes pour ne pas rater le Live
cache = {}

LIGUES = {
    "basket": [
        "basketball_nba", "basketball_ncaa", "basketball_wnba", 
        "basketball_spain_acb", "basketball_france_lnb", "basketball_china_cba"
    ],
    "hockey": ["icehockey_nhl", "icehockey_sweden_allsvenskan", "icehockey_finland_liiga"],
    "football": ["soccer_epl", "soccer_uefa_champions_league", "soccer_france_ligue_1"]
}

def fetch_data(sport_key):
    # Système de cache intelligent
    now = time.time()
    if sport_key in cache and (now - cache[sport_key]['last']) < CACHE_TTL:
        return cache[sport_key]['data']

    all_matches = []
    # On boucle sur les ligues du sport demandé
    targets = LIGUES.get(sport_key, [])
    
    for league in targets:
        try:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,totals"
            r = requests.get(url)
            if r.status_code == 200:
                all_matches.extend(r.json())
        except: continue
    
    # --- LE TRI "LIVE-FIRST" ---
    # On met en haut les matchs qui ont des bookmakers actifs (souvent le Live)
    all_matches.sort(key=lambda x: len(x.get('bookmakers', [])), reverse=True)
    
    cache[sport_key] = {'data': all_matches, 'last': now}
    return all_matches

@app.route('/')
def home():
    return "RADAR V5 ULTRA : MOTEUR EN LIGNE 📡"

@app.route('/radar/global_basket')
def radar_basket():
    return jsonify({"data": fetch_data("basket")})

@app.route('/radar/global_hockey')
def radar_hockey():
    return jsonify({"data": fetch_data("hockey")})

@app.route('/radar/football')
def radar_foot():
    return jsonify({"data": fetch_data("football")})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
  
