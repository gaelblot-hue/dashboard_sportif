from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import os
import json
import math
from datetime import datetime

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 

# ============================================================
# 🧠 CONFIGURATION & MÉMOIRE (UPSTASH)
# ============================================================
UPSTASH_URL = os.getenv('UPSTASH_REDIS_REST_URL', '')
UPSTASH_TOKEN = os.getenv('UPSTASH_REDIS_REST_TOKEN', '')
BANKROLL_INITIALE = 145.21

def redis_get(key):
    try:
        r = requests.get(f"{UPSTASH_URL}/get/{key}", headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"})
        res = r.json()
        return json.loads(res['result']) if res.get('result') else None
    except: return None

def redis_set(key, value):
    try:
        requests.post(f"{UPSTASH_URL}/set/{key}", headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, json=json.dumps(value))
    except: pass

# ============================================================
# 📐 FORMULE D'EDGE SUPRÊME (LOG-ODDS V6)
# ============================================================
def calculer_edge_v6(prob_reelle, cote_book, variance=1.0, confiance=1.0):
    if prob_reelle <= 0 or prob_reelle >= 1 or cote_book <= 1: return 0
    prob_book = 1 / cote_book
    edge_base = ((prob_reelle - prob_book) / prob_book) * (1 + variance) * 100
    lo_reel = math.log(prob_reelle / (1 - prob_reelle))
    lo_book = math.log(prob_book / (1 - prob_book))
    edge_log = ((lo_reel - lo_book) / abs(lo_book)) * 100
    return round((edge_base * 0.6 + edge_log * 0.4) * (confiance / 100), 2)

# ============================================================
# 🚀 MODULES EXPERTS (IPL, MOTIVATION, NBA)
# ============================================================
def get_ipl(stats):
    return round((stats.get('possession', 50) * 0.4) + (stats.get('tirs', 0) * 0.6), 2)

def get_motivation(classement):
    return 1.15 if classement in ['Top 3', 'Relégation'] else 1.0

# ============================================================
# 🤖 NOTICE TECHNIQUE & PROMPT SYSTÈME (LE CERVEAU)
# ============================================================
NOTICE_V6 = (
    "Tu es l'IA du Radar V6. Ton rôle est d'analyser les matchs pour Gael. "
    "Tu dois calculer l'Edge avec la formule Log-Odds. Seuil min : 15%. "
    "Si l'Edge est > 20%, c'est un SIGNAL ÉLITE (Mise 8% soit 11.62€). "
    "Utilise l'IPL pour le Live et la Motivation pour le pré-match. "
    "Ton : Expert, Direct, Enjoué. Pas de politesses inutiles. "
    "Terminologie : NBA/VTB = POINTS (Jamais de buts)."
)

# ============================================================
# 🔌 ENDPOINTS (RÉCEPTION MESSAGE & PHOTOS)
# ============================================================

@app.route('/radar/chat', methods=['POST'])
def chat_v6():
    data = request.get_json()
    message = data.get('message', '')
    image = data.get('image') # Support pour la photo rafale

    # Ici, l'IA traite le message ou l'OCR de l'image
    # Simulation de réponse experte
    reply = "Radar V6 opérationnel. J'analyse tes données sur la base de ta bankroll de 145.21€."
    
    return jsonify({"reply": reply})

@app.route('/radar/analyze', methods=['POST'])
def analyze_match():
    data = request.get_json()
    match = data.get('match', {})
    
    # Calcul simulé pour test
    edge = calculer_edge_v6(0.65, 1.85) # Exemple
    verdict = "JOUER" if edge >= 15 else "PASSER"
    
    return jsonify({
        "analyse": {
            "edge_pct": edge,
            "pari_recommande": "Victoire domicile",
            "cote": 1.85,
            "confiance_pct": 80 if edge > 20 else 40,
            "raison": "Forte corrélation stats/IPL.",
            "verdict": verdict
        }
    })

@app.route('/radar/historique/elite')
def get_historique_elite():
    historique = redis_get('historique') or []
    elite_bets = [h for h in historique if h.get('edge_pct', 0) >= 20]
    
    total_mises = sum(float(h.get('montant', 0)) for h in elite_bets)
    total_gains = sum(float(h.get('gain', 0)) for h in elite_bets if h.get('resultat') == 'WIN')
    roi_elite = (total_gains / total_mises * 100) if total_mises > 0 else 0

    return jsonify({
        "stats_elite": {
            "total": len(elite_bets),
            "roi": f"{round(roi_elite, 2)}%",
            "winrate": f"{round(len([h for h in elite_bets if h.get('resultat') == 'WIN']) / len(elite_bets) * 100, 1) if elite_bets else 0}%"
        },
        "data": elite_bets
    })

@app.route('/radar/secours/<sport>')
def mode_secours(sport):
    # Backup API direct
    return jsonify({"status": "Mode secours actif", "sport": sport})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
  
