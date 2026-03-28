from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import anthropic
import time
import os

app = Flask(__name__)
CORS(app)

# Clés API depuis les variables d'environnement
ODDS_API_KEY = os.getenv('ODDS_API_KEY')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

# --- ARCHITECTURE MONDIALE DU RADAR ---
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
        "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_usa_mls"
    ],
    "tennis": [
        "tennis_atp_miami", "tennis_wta_miami", "tennis_atp_challenger", 
        "tennis_atp_challenger_doubles", "tennis_itf_men", "tennis_itf_women"
    ]
}

def fetch_global_data(sport_key):
    """Fonction de scan par sport avec gestion de latence"""
    all_results = []
    targets = LIGUES.get(sport_key, [])
    
    for league in targets:
        try:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal"
            r = requests.get(url)
            if r.status_code == 200:
                all_results.extend(r.json())
            time.sleep(0.1)
        except Exception as e:
            print(f"Signal perdu sur {league}: {e}")
            continue
    
    all_results.sort(key=lambda x: x.get('commence_time', ''))
    return all_results


# --- ENDPOINTS RADAR ---

@app.route('/')
def health_check():
    return "RADAR V5.2 : SYSTÈME OPÉRATIONNEL 📡"

@app.route('/radar/global_basket')
def get_basket():
    return jsonify({"data": fetch_global_data("basket")})

@app.route('/radar/global_hockey')
def get_hockey():
    return jsonify({"data": fetch_global_data("hockey")})

@app.route('/radar/football')
def get_foot():
    return jsonify({"data": fetch_global_data("football")})

@app.route('/radar/tennis')
def get_tennis():
    return jsonify({"data": fetch_global_data("tennis")})


# --- ENDPOINT ANALYSE IA ---

@app.route('/radar/analyze', methods=['POST'])
def analyze_match():
    data = request.get_json()
    match = data.get('match')

    if not match:
        return jsonify({"error": "Aucune donnée de match reçue"}), 400

    # Extraction des cotes pour le prompt
    bookmakers_info = ""
    for bookie in match.get('bookmakers', [])[:5]:  # Max 5 bookmakers
        bookmakers_info += f"\n📌 {bookie['title']} :\n"
        for market in bookie.get('markets', []):
            bookmakers_info += f"  - {market['key']} : "
            for outcome in market.get('outcomes', []):
                bookmakers_info += f"{outcome['name']} @ {outcome['price']}  "
            bookmakers_info += "\n"

    prompt = f"""Tu es un expert en analyse de paris sportifs.

Match : {match.get('home_team')} vs {match.get('away_team')}
Sport : {match.get('sport_key')}
Début : {match.get('commence_time')}

Cotes disponibles : {bookmakers_info}

Analyse ce match et réponds avec :
1. 🎯 VALUE BET détecté (oui/non et lequel)
2. 📊 Niveau de confiance (sur 10)
3. 💡 Recommandation claire
4. ⚠️ Risques à considérer
"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return jsonify({"analyse": response.content[0].text})

    except Exception as e:
        return jsonify({"error": f"Erreur IA : {str(e)}"}), 500


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000
