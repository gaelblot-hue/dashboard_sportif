from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

ODDS_API_KEY = os.getenv('ODDS_API_KEY')

LIGUES = {
    "basket": ["basketball_nba", "basketball_ncaa", "basketball_spain_acb", "basketball_france_lnb", "basketball_china_cba", "basketball_korea_kbl"],
    "hockey": ["icehockey_nhl", "icehockey_sweden_allsvenskan", "icehockey_finland_liiga"],
    "football": ["soccer_epl", "soccer_france_ligue_1", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_usa_mls", "soccer_brazil_campeonato"],
    "tennis": ["tennis_atp_miami", "tennis_wta_miami", "tennis_atp_wimbledon", "tennis_wta_wimbledon"]
}

def fetch_all_world(sport_key):
    results = []
    targets = LIGUES.get(sport_key, [])
    for league in targets:
        try:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal"
            r = requests.get(url)
            if r.status_code == 200:
                results.extend(r.json())
            time.sleep(0.1)
        except: continue
    results.sort(key=lambda x: x.get('commence_time', ''))
    return results

@app.route('/radar/global_basket')
def rb(): return jsonify({"data": fetch_all_world("basket")})
@app.route('/radar/global_hockey')
def rh(): return jsonify({"data": fetch_all_world("hockey")})
@app.route('/radar/football')
def rf(): return jsonify({"data": fetch_all_world("football")})
@app.route('/radar/tennis')
def rt(): return jsonify({"data": fetch_all_world("tennis")})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)

