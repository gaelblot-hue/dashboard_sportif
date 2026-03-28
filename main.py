from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import anthropic
import time
import os
import json
from datetime import datetime

app = Flask(__name__)
# CORS Activé pour que ton dashboard GitHub puisse parler à Render
CORS(app)

# --- CLÉS API ---
ODDS_API_KEY = os.getenv('ODDS_API_KEY')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

# --- MÉMOIRE VOLATILE ---
ALERTES = []
CHAT_HISTORY = []

# --- FICHIERS ---
HISTORIQUE_FILE = "historique.json"
BANKROLL_FILE = "bankroll.json"
RESUME_FILE = "resume.json"

# --- LIGUES (Mise à jour Miami Open 2026) ---
LIGUES = {
    "basket": ["basketball_nba", "basketball_ncaa", "basketball_spain_acb", "basketball_france_lnb"],
    "hockey": ["icehockey_nhl", "icehockey_sweden_allsvenskan"],
    "football": ["soccer_epl", "soccer_france_ligue_1", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_usa_mls"],
    "tennis": [
        "tennis_atp_miami", "tennis_wta_miami", 
        "tennis_atp_miami_open", "tennis_wta_miami_open",
        "tennis_atp_challenger", "tennis_itf_men", "tennis_itf_women"
    ],
    "rugby": ["rugbyunion_top_14", "rugbyunion_premiership", "rugbyunion_six_nations", "rugbyunion_championship", "rugbyleague_nrl"]
}

# ============================================================
# TEST CONNEXION & MÉTÉO
# ============================================================

@app.route('/test')
def test_connexion():
    try:
        r = requests.get(f"https://api.the-odds-api.com/v4/sports/?apiKey={ODDS_API_KEY}", timeout=5)
        return jsonify({"status": r.status_code, "data": r.json()[:2]})
    except Exception as e:
        return jsonify({"erreur": str(e)})

def get_weather_for_match(lat=25.76, lon=-80.19):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        r = requests.get(url, timeout=5).json()
        w = r.get('current_weather', {})
        return {
            "temp": w.get('temperature'),
            "vent": w.get('windspeed'),
            "condition": w.get('weathercode')
        }
    except:
        return None

# ============================================================
# MOTEUR DE DONNÉES
# ============================================================

def fetch_global_data(sport_key):
    all_results = []
    for league in LIGUES.get(sport_key, []):
        try:
            # On scanne les cotes européennes (Decimal)
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                all_results.extend(r.json())
            time.sleep(0.1) # Anti-ban
        except:
            continue
    all_results.sort(key=lambda x: x.get('commence_time', ''))
    return all_results

def load_json(filename, default):
    try:
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                return json.load(f)
    except:
        pass
    return default

def save_json(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f)

# ============================================================
# ANALYSE IA (CLAUDE 3.5 SONNET)
# ============================================================

def analyze_with_claude(match):
    meteo = get_weather_for_match()
    odds_summary = ""
    for b in match.get('bookmakers', [])[:3]:
        odds_summary += f"\n- {b['title']}: "
        for m in b.get('markets', []):
            for o in m.get('outcomes', []):
                odds_summary += f"{o['name']}@{o['price']} "

    prompt = f"""Expert Radar V5. Analyse ce match :
Match: {match.get('home_team')} vs {match.get('away_team')}
Sport: {match.get('sport_key')}
Météo: {meteo if meteo else 'Stable'}
Cotes: {odds_summary}

Réponds UNIQUEMENT en JSON :
{{
  "value_bet": true/false,
  "confiance": 0-10,
  "pari_recommande": "conseil",
  "cote": nombre,
  "bookmaker": "nom",
  "raison": "explication",
  "impact_meteo": "description",
  "mise_conseillee": "X%"
}}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        res_text = response.content[0].text
        raw = res_text[res_text.find("{"):res_text.rfind("}")+1]
        return json.loads(raw)
    except:
        return None

# ============================================================
# ENDPOINTS (RADAR & CHAT)
# ============================================================

@app.route('/')
def health():
    return "RADAR V5.4 : SYSTEM READY 📡🌦️🏉"

@app.route('/radar/global_basket')
def get_basket(): return jsonify({"data": fetch_global_data("basket")})

@app.route('/radar/global_hockey')
def get_hockey(): return jsonify({"data": fetch_global_data("hockey")})

@app.route('/radar/football')
def get_foot(): return jsonify({"data": fetch_global_data("football")})

@app.route('/radar/tennis')
def get_tennis(): return jsonify({"data": fetch_global_data("tennis")})

@app.route('/radar/rugby')
def get_rugby(): return jsonify({"data": fetch_global_data("rugby")})

@app.route('/radar/chat', methods=['POST'])
def chat():
    global CHAT_HISTORY
    data = request.get_json()
    message = data.get('message')

    if not message:
        return jsonify({"error": "Message vide"}), 400

    bankroll = load_json("bankroll.json", {"disponible": 0})
    system_prompt = f"Tu es RADAR V5. Expert paris sportifs. Bankroll: {bankroll.get('disponible')}€. Sois bref et précis."

    CHAT_HISTORY.append({"role": "user", "content": message})

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=400,
            system=system_prompt,
            messages=CHAT_HISTORY[-6:] # Garde les 3 derniers échanges
        )
        reply = response.content[0].text
        CHAT_HISTORY.append({"role": "assistant", "content": reply})
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/radar/analyze', methods=['POST'])
def analyze_match_endpoint():
    data = request.get_json()
    match = data.get('match')
    if not match: return jsonify({"error": "No data"}), 400
    analyse = analyze_with_claude(match)
    return jsonify(analyse) if analyse else jsonify({"error": "IA Error"}), 500

# ============================================================
# LANCEMENT (Correction parenthèse finale)
# ============================================================

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
  
