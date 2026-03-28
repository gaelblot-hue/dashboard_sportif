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

# --- LIGUES ---
LIGUES = {
    "basket": ["basketball_nba", "basketball_ncaa", "basketball_spain_acb", "basketball_france_lnb"],
    "hockey": ["icehockey_nhl", "icehockey_sweden_allsvenskan"],
    "football": ["soccer_epl", "soccer_france_ligue_1", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_usa_mls"],
    "tennis": ["tennis_atp_miami", "tennis_wta_miami", "tennis_atp_challenger", "tennis_itf_men", "tennis_itf_women"],
    "rugby": ["rugbyunion_top_14", "rugbyunion_premiership", "rugbyunion_six_nations", "rugbyunion_championship", "rugbyleague_nrl"]
}

# ============================================================
# MÉTÉO (Open-Meteo, gratuit)
# ============================================================

def get_weather_for_match(lat=25.76, lon=-80.19):
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
# UTILITAIRES
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
        except:
            continue
    all_results.sort(key=lambda x: x.get('commence_time', ''))
    return all_results

def load_historique():
    try:
        with open(HISTORIQUE_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_historique(entry):
    historique = load_historique()
    historique.insert(0, entry)
    historique = historique[:100]
    with open(HISTORIQUE_FILE, 'w') as f:
        json.dump(historique, f)

def load_bankroll():
    try:
        with open(BANKROLL_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"total": 0, "disponible": 0, "mises": []}

def save_bankroll(data):
    with open(BANKROLL_FILE, 'w') as f:
        json.dump(data, f)

def calculer_mise(bankroll_disponible, confiance):
    pourcentages = {7: 0.02, 8: 0.04, 9: 0.06, 10: 0.08}
    pct = pourcentages.get(confiance, 0.02)
    return round(bankroll_disponible * pct, 2)

# ============================================================
# ANALYSE IA
# ============================================================

def analyze_with_claude(match):
    meteo = get_weather_for_match()

    odds_summary = ""
    for b in match.get('bookmakers', [])[:3]:
        odds_summary += f"\n- {b['title']}: "
        for m in b.get('markets', []):
            for o in m.get('outcomes', []):
                odds_summary += f"{o['name']}@{o['price']} "

    prompt = f"""Expert Radar V5.
Match: {match.get('home_team')} vs {match.get('away_team')}
Sport: {match.get('sport_key')}
Météo: {meteo if meteo else 'Stable'}
Cotes: {odds_summary}

Réponds UNIQUEMENT en JSON valide :
{{
  "value_bet": true ou false,
  "confiance": nombre entre 0 et 10,
  "pari_recommande": "conseil court",
  "cote": nombre,
  "bookmaker": "nom",
  "raison": "explication courte avec impact météo",
  "risque": "risque principal",
  "impact_meteo": "aucun ou description",
  "mise_conseillee": "% bankroll"
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
    except Exception as e:
        print(f"Erreur Claude : {e}")
        return None

# ============================================================
# SCAN VALUE BETS
# ============================================================

def scan_value_bets():
    global ALERTES
    nouvelles_alertes = []

    for sport_key in LIGUES.keys():
        matchs = fetch_global_data(sport_key)
        for match in matchs[:5]:
            if not match.get('bookmakers'):
                continue
            analyse = analyze_with_claude(match)
            if not analyse:
                continue
            if analyse.get('value_bet') and analyse.get('confiance', 0) >= 7:
                nouvelles_alertes.append({
                    "id": f"{match.get('id', '')}_{int(time.time())}",
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "match": f"{match.get('home_team')} vs {match.get('away_team')}",
                    "sport": sport_key,
                    "confiance": analyse.get('confiance'),
                    "pari": analyse.get('pari_recommande'),
                    "cote": analyse.get('cote'),
                    "bookmaker": analyse.get('bookmaker'),
                    "raison": analyse.get('raison'),
                    "risque": analyse.get('risque'),
                    "impact_meteo": analyse.get('impact_meteo', 'aucun')
                })
            time.sleep(0.5)

    ALERTES = (nouvelles_alertes + ALERTES)[:20]
    print(f"✅ Scan terminé : {len(nouvelles_alertes)} value bets détectés")

# ============================================================
# RÉSUMÉ QUOTIDIEN
# ============================================================

def generate_daily_resume():
    tous_matchs = []
    for sport_key in LIGUES.keys():
        matchs = fetch_global_data(sport_key)
        tous_matchs.extend(matchs[:3])

    if not tous_matchs:
        return None

    matchs_str = ""
    for m in tous_matchs:
        bookmakers_count = len(m.get('bookmakers', []))
        h2h = ""
        if m.get('bookmakers'):
            market = m['bookmakers'][0].get('markets', [])
            h2h_market = next((mk for mk in market if mk['key'] == 'h2h'), None)
            if h2h_market:
                h2h = " | ".join([f"{o['name']} @ {o['price']}" for o in h2h_market['outcomes']])
        matchs_str += f"- {m.get('sport_key')} : {m.get('home_team')} vs {m.get('away_team')} | {h2h} | {bookmakers_count} bookmakers\n"

    prompt = f"""Tu es un expert en paris sportifs. Matchs disponibles aujourd'hui :
{matchs_str}

Réponds UNIQUEMENT en JSON :
{{
  "date": "{datetime.now().strftime('%d/%m/%Y')}",
  "resume_general": "2-3 phrases sur la journée",
  "top_matchs": [
    {{
      "match": "nom",
      "sport": "sport",
      "raison": "pourquoi intéressant",
      "pari_suggere": "pari recommandé",
      "niveau_interet": 1-5
    }}
  ],
  "conseil_du_jour": "conseil général",
  "sports_chauds": ["sport1", "sport2"]
}}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        res_text = response.content[0].text
        raw = res_text[res_text.find("{"):res_text.rfind("}")+1]
        resume = json.loads(raw)
        with open(RESUME_FILE, 'w') as f:
            json.dump(resume, f)
        return resume
    except Exception as e:
        print(f"Erreur résumé : {e}")
        return None

# ============================================================
# ENDPOINTS RADAR
# ============================================================

@app.route('/')
def health():
    return "RADAR V5.4 : SYSTEM READY 📡🌦️🏉"

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

@app.route('/radar/rugby')
def get_rugby():
    return jsonify({"data": fetch_global_data("rugby")})

# ============================================================
# ENDPOINTS ANALYSE
# ============================================================

@app.route('/radar/analyze', methods=['POST'])
def analyze_match():
    data = request.get_json()
    match = data.get('match')
    if not match:
        return jsonify({"error": "No data"}), 400

    analyse = analyze_with_claude(match)
    if not analyse:
        return jsonify({"error": "Erreur IA"}), 500

    save_historique({
        "id": f"{match.get('id', '')}_{int(time.time())}",
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "match": f"{match.get('home_team')} vs {match.get('away_team')}",
        "sport": match.get('sport_key', ''),
        "value_bet": analyse.get('value_bet'),
        "confiance": analyse.get('confiance'),
        "pari": analyse.get('pari_recommande'),
        "cote": analyse.get('cote'),
        "bookmaker": analyse.get('bookmaker'),
        "raison": analyse.get('raison'),
        "impact_meteo": analyse.get('impact_meteo', 'aucun'),
        "resultat": None
    })

    return jsonify({"analyse": analyse})

# ============================================================
# ENDPOINTS ALERTES
# ============================================================

@app.route('/radar/alertes')
def get_alertes():
    return jsonify({"alertes": ALERTES, "last_scan": datetime.now().strftime("%H:%M:%S")})

@app.route('/radar/scan', methods=['POST'])
def trigger_scan():
    scan_value_bets()
    return jsonify({"status": "Scan terminé", "alertes": len(ALERTES)})

# ============================================================
# ENDPOINTS HISTORIQUE
# ============================================================

@app.route('/radar/historique')
def get_historique():
    historique = load_historique()
    total = len(historique)
    value_bets = [h for h in historique if h.get('value_bet')]
    wins = [h for h in historique if h.get('resultat') == 'WIN']
    confiance_moy = round(
        sum(h.get('confiance', 0) for h in historique) / total, 1
    ) if total > 0 else 0
    return jsonify({
        "historique": historique,
        "stats": {
            "total": total,
            "value_bets": len(value_bets),
            "wins": len(wins),
            "losses": len([h for h in historique if h.get('resultat') == 'LOSS']),
            "confiance_moyenne": confiance_moy
        }
    })

@app.route('/radar/historique/resultat', methods=['POST'])
def update_resultat():
    data = request.get_json()
    historique = load_historique()
    for entry in historique:
        if entry.get('id') == data.get('id'):
            entry['resultat'] = data.get('resultat')
            break
    with open(HISTORIQUE_FILE, 'w') as f:
        json.dump(historique, f)
    return jsonify({"status": "ok"})

# ============================================================
# ENDPOINTS BANKROLL
# ============================================================

@app.route('/radar/bankroll')
def get_bankroll():
    return jsonify(load_bankroll())

@app.route('/radar/bankroll/init', methods=['POST'])
def init_bankroll():
    data = request.get_json()
    montant = float(data.get('montant', 0))
    bankroll = {"total": montant, "disponible": montant, "mises": []}
    save_bankroll(bankroll)
    return jsonify({"status": "ok", "bankroll": bankroll})

@app.route('/radar/bankroll/miser', methods=['POST'])
def ajouter_mise():
    data = request.get_json()
    bankroll = load_bankroll()
    mise = {
        "id": f"mise_{int(time.time())}",
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "match": data.get('match'),
        "pari": data.get('pari'),
        "cote": float(data.get('cote', 0)),
        "montant": float(data.get('montant', 0)),
        "confiance": int(data.get('confiance', 0)),
        "statut": "EN COURS",
        "gain": None
    }
    bankroll['disponible'] = round(bankroll['disponible'] - mise['montant'], 2)
    bankroll['mises'].insert(0, mise)
    save_bankroll(bankroll)
    return jsonify({"status": "ok", "bankroll": bankroll})

@app.route('/radar/bankroll/resultat', methods=['POST'])
def resultat_mise():
    data = request.get_json()
    bankroll = load_bankroll()
    for mise in bankroll['mises']:
        if mise['id'] == data.get('id'):
            mise['statut'] = data.get('statut')
            if data.get('statut') == 'WIN':
                gain = round(mise['montant'] * mise['cote'] - mise['montant'], 2)
                mise['gain'] = gain
                bankroll['disponible'] = round(bankroll['disponible'] + mise['montant'] + gain, 2)
            else:
                mise['gain'] = -mise['montant']
            break
    save_bankroll(bankroll)
    return jsonify({"status": "ok", "bankroll": bankroll})

@app.route('/radar/bankroll/historique_pnl')
def get_historique_pnl():
    bankroll = load_bankroll()
    mises = [m for m in bankroll.get('mises', []) if m.get('gain') is not None]
    mises.reverse()
    solde = bankroll.get('total', 0)
    points = [{"date": "Départ", "solde": solde, "gain": 0}]
    for mise in mises:
        solde = round(solde + mise['gain'], 2)
        points.append({
            "date": mise['timestamp'],
            "solde": solde,
            "gain": mise['gain'],
            "match": mise['match']
        })
    return jsonify({"points": points, "total_initial": bankroll.get('total', 0)})

# ============================================================
# ENDPOINTS RÉSUMÉ
# ============================================================

@app.route('/radar/resume')
def get_resume():
    try:
        with open(RESUME_FILE, 'r') as f:
            return jsonify(json.load(f))
    except:
        return jsonify({"error": "Aucun résumé disponible"})

@app.route('/radar/resume/generate', methods=['POST'])
def generate_resume():
    resume = generate_daily_resume()
    if resume:
        return jsonify({"status": "ok", "resume": resume})
    return jsonify({"error": "Erreur génération"}), 500

# ============================================================
# ENDPOINT CHAT
# ============================================================

@app.route('/radar/chat', methods=['POST'])
def chat():
    global CHAT_HISTORY
    data = request.get_json()
    message = data.get('message')

    if data.get('reset'):
        CHAT_HISTORY = []
        return jsonify({"status": "reset"})

    if not message:
        return jsonify({"error": "Message vide"}), 400

    system_prompt = f"""Tu es RADAR, l'IA du dashboard paris sportifs.
Sois bref, précis et professionnel. Réponds toujours en français.
Bankroll disponible : {load_bankroll().get('disponible', 0)}€"""

    CHAT_HISTORY.append({"role": "user", "content": message})

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=400,
            system=system_prompt,
            messages=CHAT_HISTORY[-6:]
        )
        reply = response.content[0].text
        CHAT_HISTORY.append({"role": "assistant", "content": reply})
        return jsonify({"reply": reply})
    except Exception as e:
        print(f"Erreur chat : {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
