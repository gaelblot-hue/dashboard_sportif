from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

ODDS_API_KEY = os.getenv('ODDS_API_KEY')
CACHE_TTL = 45 # Fraîcheur maximale

# --- LE MULTIPLEXEUR MONDIAL ---
LIGUES = {
    "basket": [
        "basketball_nba", "basketball_ncaa", "basketball_wnba", 
        "basketball_spain_acb", "basketball_france_lnb", "basketball_italy_lega_a", 
        "basketball_germany_bbl", "basketball_china_cba", "basketball_korea_kbl", 
        "basketball_brazil_lnb"
    ],
    "hockey": [
        "icehockey_nhl", "icehockey_sweden_allsvenskan", "icehockey_finland_liiga"
    ],
    "football": [
        "soccer_epl", "soccer_france_ligue_1", "soccer_spain_la_liga", 
        "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_uefa_champions_league",
        "soccer_usa_mls", "soccer_brazil_campeonato"
    ]
}

def fetch_all_world(sport_key):
    results = []
    targets = LIGUES.get(sport_key, [])
    for league in targets:
        try:
            # On demande H2H (Vainqueur), Spreads (Handicap) et Totals (Over/Under)
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal"
            r = requests.get(url)
            if r.status_code == 200:
                results.extend(r.json())
            time.sleep(0.1) # Sécurité API
        except: continue
    
    # Tri par date (les plus proches ou en cours en premier)
    results.sort(key=lambda x: x.get('commence_time', ''))
    return results

@app.route('/')
def status(): return "RADAR V5.1 ULTRA : GLOBAL SCAN ACTIVE 📡"

@app.route('/radar/global_basket')
def rb(): return jsonify({"data": fetch_all_world("basket")})

@app.route('/radar/global_hockey')
def rh(): return jsonify({"data": fetch_all_world("hockey")})

@app.route('/radar/football')
def rf(): return jsonify({"data": fetch_all_world("football")})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
  
