import os
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# --- CONFIGURATION SÉCURISÉE ---
# Assure-toi que 'SPORTSDATA_API_KEY' est créé dans l'onglet Environment de Render
SPORTSDATA_API_KEY = os.environ.get('SPORTSDATA_API_KEY')

def test_signal_render():
    """Vérifie la connexion à SportsDataIO au lancement du serveur"""
    # Date du jour pour le test NBA
    url = f"https://api.sportsdata.io/v3/nba/odds/json/GameOddsByDate/2026-MAR-28?key={SPORTSDATA_API_KEY}"
    
    print("🛰️ RADAR V5 : Tentative de connexion via Render...")
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            print(f"✅ RENDER OK : Signal reçu ! ({len(r.json())} matchs détectés)")
            return True
        else:
            print(f"❌ RENDER BLOQUÉ : Code {r.status_code} - Vérifie ta clé !")
            return False
    except Exception as e:
        print(f"🚨 ERREUR RÉSEAU RENDER : {e}")
        return False

@app.route('/')
def home():
    return "Radar V5 Ultra - Système en ligne 🛰️"

@app.route('/test_sportsdata')
def test_sportsdata():
    """Route manuelle pour vérifier le flux JSON en direct"""
    url = f"https://api.sportsdata.io/v3/nba/odds/json/GameOddsByDate/2026-MAR-28?key={SPORTSDATA_API_KEY}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            # On renvoie le status et les deux premiers matchs pour vérification
            return jsonify({
                "status": "SUCCESS",
                "code": r.status_code,
                "nb_matchs": len(data),
                "apercu": data[:2]
            })
        else:
            return jsonify({
                "status": "ERROR",
                "code": r.status_code,
                "message": "Signal bloqué ou clé invalide"
            })
    except Exception as e:
        return jsonify({"status": "CRASH", "erreur": str(e)})

# --- DÉMARRAGE DU MOTEUR ---
if __name__ == "__main__":
    # On lance le test de connexion avant d'ouvrir le serveur
    test_signal_render()
    
    # Port standard pour Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
  
