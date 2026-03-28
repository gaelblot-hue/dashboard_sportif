from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

ODDS_API_KEY = os.getenv('ODDS_API_KEY')

LIGUES = {
    "basket": ["basketball_nba", "basketball_ncaa", "basketball_spain_acb", "basketball_france_lnb"],
    "hockey": ["icehockey_nhl", "icehockey_sweden_allsvenskan"],
    "football": ["soccer_epl", "soccer_uefa_champions_league"]
}

def fetch_all(sport_key):
    all_data = []
    targets = LIGUES.get(sport_key, [])
    for league in targets:
        try:
            # On demande TOUT : Vainqueur, Handicap, Totaux
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal"
            r = requests.get(url)
            if r.status_code == 200:
                all_data.extend(r.json())
        except: continue
    return all_data

@app.route('/radar/global_basket')
def rb(): return jsonify({"data": fetch_all("basket")})

@app.route('/radar/global_hockey')
def rh(): return jsonify({"data": fetch_all("hockey")})

@app.route('/radar/football')
def rf(): return jsonify({"data": fetch_all("football")})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
  
