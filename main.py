from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION DES MUNITIONS (API KEYS) ---
# On récupère les clés que tu as mises dans l'onglet Environment de Render
ODDS_API_KEY = os.getenv('ODDS_API_KEY')
BALLDONTLIE_KEY = os.getenv('BALLDONTLIE_KEY')

# --- LE COFFRE-FORT (CACHE) ---
cache = {
    "nba_odds": None,
    "last_update": 0
}

@app.route('/')
def home():
    return "RADAR V5 ULTRA : ANTENNE OPÉRATIONNELLE 📡"

@app.route('/nba-value')
def get_nba_value():
    current_time = time.time()
    
    # RÈGLE ÉCO : On ne rafraîchit que toutes les 10 minutes (600 secondes)
    if cache["nba_odds"] and (current_time - cache["last_update"] < 600):
        return jsonify({"data": cache["nba_odds"], "source": "cache"})

    # Sinon, on sort l'artillerie (Appel API)
    try:
        # On interroge The Odds API pour la NBA (H2H = Victoire simple)
        url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h"
        response = requests.get(url)
        data = response.json()
        
        # On sauvegarde dans le cache pour économiser les clés
        cache["nba_odds"] = data
        cache["last_update"] = current_time
        
        return jsonify({"data": data, "source": "live_api"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
  
