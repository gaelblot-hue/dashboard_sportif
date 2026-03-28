from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import anthropic
import time
import os
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION DES CLÉS ---
ODDS_API_KEY = os.getenv('ODDS_API_KEY')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
# On utilise Open-Meteo (Gratuit, pas besoin de clé WEATHER_API_KEY)

# --- MÉMOIRE VOLATILE (Render reset ces listes au redémarrage) ---
ALERTES = []
CHAT_HISTORY = []

# --- LIGUES ---
LIGUES = {
    "basket": ["basketball_nba", "basketball_ncaa", "basketball_spain_acb", "basketball_france_lnb"],
    "hockey": ["icehockey_nhl", "icehockey_sweden_allsvenskan"],
    "football": ["soccer_epl", "soccer_france_ligue_1", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_usa_mls"],
    "tennis": ["tennis_atp_miami", "tennis_wta_miami", "tennis_atp_challenger", "tennis_itf_men", "tennis_itf_women"]
}

# ============================================================
# MODULE MÉTÉO UNIVERSEL (Open-Meteo)
# ============================================================

def get_weather_for_match(lat=25.76, lon=-80.19):
    """Récupère la météo précise selon les coordonnées du match"""
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&hourly=relativehumidity_2m"
        r = requests.get(url, timeout=5).json()
        w = r.get('current_weather', {})
        return {
            "temp": w.get('temperature'),
            "vent": w.get('windspeed'),
            "condition_code": w.get('weathercode'),
            "is_day": w.get('is_day')
        }
    except:
        return None

# ============================================================
# MOTEUR DE SCAN
# ============================================================

def fetch_global_data(sport_key):
    all_results = []
    for league in LIGUES.get(sport_key, []):
        try:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal"
            r = requests.get(url)
            if r.status_code == 200:
                all_results.extend(r.json())
            time.sleep(0.1)
        except: continue
    all_results.sort(key=lambda x: x.get('commence_time', ''))
    return all_results

# ============================================================
# ANALYSE IA BOOSTÉE
# ============================================================

@app.route('/radar/analyze', methods=['POST'])
def analyze_match():
    data = request.get_json()
    match = data.get('match')
    if not match: return jsonify({"error": "No data"}), 400

    # Récupération Météo (Miami par défaut si pas de coordonnées)
    meteo = get_weather_for_match()
    
    # Formatage des cotes pour Claude
    odds_summary = ""
    for b in match.get('bookmakers', [])[:3]:
        odds_summary += f"\n- {b['title']}: "
        for m in b.get('markets', []):
            for o in m.get('outcomes', []):
                odds_summary += f"{o['name']}@{o['price']} "

    prompt = f"""Expert Radar V5.
Match: {match.get('home_team')} vs {match.get('away_team')}
Météo: {meteo if meteo else 'Stable'}
Cotes: {odds_summary}

Réponds en JSON:
{{
  "value_bet": true/false,
  "confiance": 0-10,
  "pari": "votre conseil",
  "raison": "explication courte avec impact météo",
  "mise_conseillee": "% bankroll"
}}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        # Nettoyage de la réponse pour extraire le JSON
        res_text = response.content[0].text
        return jsonify(json.loads(res_text[res_text.find("{"):res_text.rfind("}")+1]))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================
# ENDPOINTS STANDARDS
# ============================================================

@app.route('/')
def health(): return "RADAR V5.4 : SYSTEM READY 📡🌦️"

@app.route('/radar/global_basket')
def get_basket(): return jsonify({"data": fetch_global_data("basket")})

@app.route('/radar/global_hockey')
def get_hockey(): return jsonify({"data": fetch_global_data("hockey")})

@app.route('/radar/football')
def get_foot(): return jsonify({"data": fetch_global_data("football")})

@app.route('/radar/tennis')
def get_tennis(): return jsonify({"data": fetch_global_data("tennis")})

# ============================================================
# CHAT INTELLIGENT
# ============================================================

@app.route('/radar/chat', methods=['POST'])
def chat():
    global CHAT_HISTORY
    msg = request.get_json().get('message')
    if not msg: return jsonify({"error": "No message"}), 400

    CHAT_HISTORY.append({"role": "user", "content": msg})
    
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=400,
            system="Tu es RADAR, l'IA du dashboard. Sois bref, cynique et très précis sur les stats.",
            messages=CHAT_HISTORY[-6:]
        )
        reply = response.content[0].text
        CHAT_HISTORY.append({"role": "assistant", "content": reply})
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)

