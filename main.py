from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

# --- CLÉ D'ACCÈS ---
ODDS_API_KEY = os.getenv('ODDS_API_KEY')

# --- CONFIGURATION MULTI-SPORTS MONDIALE V5.2 (FULL TENNIS) ---
LIGUES = {
    "basket": [
        "basketball_nba", "basketball_ncaa", "basketball_spain_acb", 
        "basketball_france_lnb", "basketball_china_cba", "basketball_korea_kbl"
    ],
    "hockey": [
        "icehockey_nhl", "icehockey_sweden_allsvenskan", "icehockey_finland_liiga"
    ],
    "football": [
        "soccer_epl", "soccer_france_ligue_1", "soccer_spain_la_liga", 
        "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_usa_mls", 
        "soccer_brazil_campeonato"
    ],
    "tennis": [
        # CIRCUIT ELITE
        "tennis_atp_miami", "tennis_wta_miami", "tennis_atp_monte_carlo",
        "tennis_atp_marrakech", "tennis_atp_houston", "tennis_atp_estoril",
        # CIRCUIT CHALLENGER & ITF (Pour capter tes captures !)
        "tennis_atp_challenger", "tennis_atp_challenger_doubles",
        "tennis_wta_challenger", "tennis_itf_men", "tennis_itf_women",
        "tennis_wta_charleston", "tennis_wta_bogota"
    ]
}

def fetch_all_world(sport_key):
    results = []
    targets = LIGUES.get(sport_key, [])
    
    # On scanne chaque ligue du dictionnaire
    for league in targets:
        try:
            # On demande H2H, Spreads et Totals en Decimal
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal"
            r = requests.get(url)
            if r.status_code == 200:
                data = r.json()
                results.extend(data)
            # Petite pause pour éviter le ban IP
            time.sleep(0.1)
        except Exception as e:
            print(f"Erreur sur {league}: {e}")
            continue
    
    # TRI : Les matchs qui commencent bientôt en premier
    results.sort(key=lambda x: x.get('commence_time', ''))
    return results

@app.route('/')
def status(): 
    return "RADAR V5.2 ULTRA : SCANNER MONDIAL + ITF/CHALLENGER ACTIF 📡"

@app.route('/radar/global_basket')
def rb(): return jsonify({"data": fetch_all_world("basket")})

@app.route('/radar/global_hockey')
def rh(): return jsonify({"data": fetch_all_world("hockey")})

@app.route('/radar/football')
def rf(): return jsonify({"data": fetch_all_world("football")})

@app.route('/radar/tennis')
def rt(): return jsonify({"data": fetch_all_world("tennis")})

if __name__ == "__main__":
    # Render impose le port 10000
    app.run(host='0.0.0.0', port=10000)
  
