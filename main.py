from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

ODDS_API_KEY = os.getenv('ODDS_API_KEY')

# CACHE DE 5 MIN POUR NE PAS CRAMER LES CLES
cache = {"soccer": {"data": None, "last": 0}, "tennis": {"data": None, "last": 0}, "basketball": {"data": None, "last": 0}, "icehockey": {"data": None, "last": 0}}

def get_complete_signal(sport_key, markets="h2h,totals"):
    current = time.time()
    sport_type = sport_key.split('_')[0]
    if cache.get(sport_type) and (current - cache[sport_type]["last"] < 300):
        return cache[sport_type]["data"]
    try:
        # On récupère les cotes PRE-MATCH et LIVE en même temps
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets={markets}&bookmakers=pinnacle,unibet_fr"
        response = requests.get(url)
        data = response.json()
        cache[sport_type] = {"data": data, "last": current}
        return data
    except: return []

@app.route('/')
def home(): return "RADAR V5 ULTRA : MODE TOTAL ACTIF 🛰️"

@app.route('/radar/football')
def radar_foot():
    return jsonify({"sport": "football", "data": get_complete_signal("soccer_epl")})

@app.route('/radar/basketball')
def radar_basket():
    return jsonify({"sport": "basketball", "data": get_complete_signal("basketball_nba", "h2h,totals,spreads")})

@app.route('/radar/tennis')
def radar_tennis():
    return jsonify({"sport": "tennis", "data": get_complete_signal("tennis_atp_wimbledon")})

@app.route('/radar/nhl')
def radar_nhl():
    return jsonify({"sport": "nhl", "data": get_complete_signal("icehockey_nhl")})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
  
