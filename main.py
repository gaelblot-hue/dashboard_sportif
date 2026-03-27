from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
ODDS_API_KEY = os.getenv('ODDS_API_KEY')

# --- SYSTEME DE CACHE (5 MIN) ---
cache = {
    "soccer": {"data": None, "last": 0},
    "tennis": {"data": None, "last": 0},
    "basketball": {"data": None, "last": 0},
    "icehockey": {"data": None, "last": 0}
}

def get_odds(sport_key, markets="h2h,totals"):
    current = time.time()
    sport_type = sport_key.split('_')[0]
    
    if cache.get(sport_type) and (current - cache[sport_type]["last"] < 300):
        return cache[sport_type]["data"]

    try:
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets={markets}"
        response = requests.get(url)
        data = response.json()
        cache[sport_type] = {"data": data, "last": current}
        return data
    except:
        return []

@app.route('/')
def home():
    return "RADAR V5 ULTRA : TOUS SYSTEMES OPERATIONNELS 🚀"

@app.route('/radar/football')
def radar_foot():
    # On utilise soccer_epl (Angleterre) car il y a toujours des matchs
    data = get_odds("soccer_epl")
    return jsonify({"sport": "football", "data": data})

@app.route('/radar/tennis')
def radar_tennis():
    data = get_odds("tennis_atp_wimbledon")
    return jsonify({"sport": "tennis", "data": data})

@app.route('/radar/basketball')
def radar_basket():
    data = get_odds("basketball_nba", markets="h2h,totals,spreads")
    return jsonify({"sport": "basketball", "data": data})

@app.route('/radar/nhl')
def radar_nhl():
    data = get_odds("icehockey_nhl")
    return jsonify({"sport": "nhl", "data": data})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
  
