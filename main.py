from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

ODDS_API_KEY = os.getenv('ODDS_API_KEY')

cache = {
    "football": {"data": None, "last": 0},
    "tennis": {"data": None, "last": 0},
    "nba": {"data": None, "last": 0},
    "nhl": {"data": None, "last": 0}
}

def get_odds(sport_key, markets="h2h,totals"):
    current = time.time()
    sport_short = sport_key.split('_')[0] if '_' in sport_key else sport_key
    if cache.get(sport_short) and (current - cache[sport_short]["last"] < 300):
        return cache[sport_short]["data"]
    try:
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets={markets}"
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
    data = get_odds("soccer_france_ligue_1")
    return jsonify({"sport": "FOOTBALL", "data": data})

@app.route('/radar/tennis')
def radar_tennis():
    data = get_odds("tennis_atp_wimbledon")
    return jsonify({"sport": "TENNIS", "data": data})

@app.route('/radar/basketball')
def radar_basket():
    data = get_odds("basketball_nba", markets="h2h,totals,spreads")
    return jsonify({"sport": "BASKET", "data": data})

@app.route('/radar/nhl')
def radar_nhl():
    data = get_odds("icehockey_nhl")
    return jsonify({"sport": "NHL", "data": data})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
  
