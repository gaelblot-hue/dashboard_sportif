from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

# --- CLES STRATÉGIQUES ---
ODDS_API_KEY = os.getenv('ODDS_API_KEY')
# On garde Highlightly en réserve pour les vidéos uniquement
HIGHLIGHTLY_KEY = os.getenv('HIGHLIGHTLY_KEY') 

# --- CONFIGURATION DES LIGUES (LE MONDE ENTIER) ---
LIGUES = {
    "basket": [
        "basketball_nba", "basketball_ncaa", "basketball_wnba", 
        "basketball_spain_acb", "basketball_france_lnb", 
        "basketball_germany_bbl", "basketball_china_cba",
        "basketball_korea_kbl", "basketball_brazil_lnb"
    ],
    "hockey": ["icehockey_nhl", "icehockey_sweden_allsvenskan", "icehockey_finland_liiga"],
    "tennis": ["tennis_atp_wimbledon", "tennis_wta_wimbledon"]
}

cache = {}

def fetch_global_signal(sport_list):
    results = []
    for league in sport_list:
        try:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,totals,spreads"
            r = requests.get(url)
            if r.status_code == 200:
                results.extend(r.json())
            time.sleep(0.2) # Protection anti-ban
        except: continue
    return results

@app.route('/radar/global_basket')
def global_basket():
    # Scanne NBA + Europe + Asie + Brésil
    data = fetch_global_signal(LIGUES["basket"])
    return jsonify({"sport": "basket_world", "count": len(data), "data": data})

@app.route('/radar/global_hockey')
def global_hockey():
    data = fetch_global_signal(LIGUES["hockey"])
    return jsonify({"sport": "hockey_world", "data": data})

@app.route('/video/check/<match_id>')
def get_highlight(match_id):
    # Appel à Highlightly UNIQUEMENT si le bouton est cliqué
    # Économise tes 100 requêtes/jour
    return jsonify({"video_url": "Recherche en cours sur Highlightly/Reddit..."})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
  
