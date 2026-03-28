from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import anthropic
import time
import os

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
ODDS_API_KEY = os.getenv('ODDS_API_KEY')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

LIGUES = {
    "basket": ["basketball_nba", "basketball_ncaa", "basketball_spain_acb", "basketball_france_lnb", "basketball_china_cba", "basketball_korea_kbl"],
    "hockey": ["icehockey_nhl", "icehockey_sweden_allsvenskan", "icehockey_finland_liiga"],
    "football": ["soccer_epl", "soccer_france_ligue_1", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_usa_mls"],
    "tennis": ["tennis_atp_miami", "tennis_wta_miami", "tennis_atp_challenger", "tennis_itf_men", "tennis_itf_women"]
}

# --- MODULE MÉTÉO GRATUIT ---
def get_weather_impact():
    """Récupère les conditions météo globales (Simulé sur Paris/Miami par défaut ici)"""
    try:
        # Open-Meteo : Pas besoin de clé API
        url = "https://api.open-meteo.com/v1/forecast?latitude=25.76&longitude=-80.19&current_weather=true"
        r = requests.get(url, timeout=5).json()
        w = r.get('current_weather', {})
        return f"Temp: {w.get('temperature')}°C, Vent: {w.get('windspeed')}km/h, Code: {w.get('weathercode')}"
    except:
        return "Météo non disponible pour ce secteur."

def fetch_global_data(sport_key):
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

# --- ENDPOINTS ---

@app.route('/')
def health_check():
    return "RADAR V5.3 : IA + WEATHER MODULE ACTIVE 📡🌦️"

@app.route('/radar/global_basket')
def get_basket(): return jsonify({"data": fetch_global_data("basket")})

@app.route('/radar/global_hockey')
def get_hockey(): return jsonify({"data": fetch_global_data("hockey")})

@app.route('/radar/football')
def get_foot(): return jsonify({"data": fetch_global_data("football")})

@app.route('/radar/tennis')
def get_tennis(): return jsonify({"data": fetch_global_data("tennis")})

# --- ENDPOINT ANALYSE IA (Version Papi + Météo) ---

@app.route('/radar/analyze', methods=['POST'])
def analyze_match():
    data = request.get_json()
    match = data.get('match')

    if not match:
        return jsonify({"error": "Aucune donnée de match reçue"}), 400

    # 🌦️ On récupère la météo au moment de l'analyse
    current_weather = get_weather_impact()

    # Extraction des cotes pour le prompt (Max 5 bookies)
    bookmakers_info = ""
    for bookie in match.get('bookmakers', [])[:5]:
        bookmakers_info += f"\n📌 {bookie['title']} :\n"
        for market in bookie.get('markets', []):
            bookmakers_info += f"  - {market['key']} : "
            for outcome in market.get('outcomes', []):
                bookmakers_info += f"{outcome['name']} @ {outcome['price']}  "
            bookmakers_info += "\n"

    prompt = f"""Tu es un expert en analyse de paris sportifs de haut niveau.

Match : {match.get('home_team')} vs {match.get('away_team')}
Sport : {match.get('sport_key')}
Début : {match.get('commence_time')}

🌦️ CONDITIONS MÉTÉO ACTUELLES : {current_weather}

Cotes disponibles : {bookmakers_info}

Analyse ce match en prenant en compte les cotes et l'impact potentiel de la météo (vent, chaleur, pluie) sur la performance :
1. 🎯 VALUE BET détecté (oui/non et lequel)
2. 📊 Niveau de confiance (sur 10)
3. 💡 Recommandation claire (Mise conseillée en % de bankroll)
4. ⚠️ Risques spécifiques (Blessures, météo, fatigue)
"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022", # Modèle plus rapide et efficace que Opus pour du Live
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return jsonify({"analyse": response.content[0].text})

    except Exception as e:
        return jsonify({"error": f"Erreur IA : {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
  
