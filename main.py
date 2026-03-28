@app.route('/test_all_apis')
def test_all_apis():
    results = {}

    # BallDontLie - NBA stats
    try:
        r = requests.get("https://api.balldontlie.io/v1/games",
            headers={"Authorization": os.getenv('BALLDONTLIE_KEY')},
            timeout=5)
        results["BallDontLie"] = {"status": r.status_code, "matchs": len(r.json().get('data', []))}
    except Exception as e:
        results["BallDontLie"] = {"erreur": str(e)}

    # API Football - Foot EU
    try:
        r = requests.get("https://v3.football.api-sports.io/fixtures?live=all",
            headers={"x-apisports-key": os.getenv('API_FOOTBALL_KEY')},
            timeout=5)
        results["API_Football"] = {"status": r.status_code, "matchs": len(r.json().get('response', []))}
    except Exception as e:
        results["API_Football"] = {"erreur": str(e)}

    # Sports API Pro
    try:
        r = requests.get("https://api.sportsapipro.com/v1/sports",
            headers={"Authorization": os.getenv('SPORTS_API_PRO')},
            timeout=5)
        results["SportsApiPro"] = {"status": r.status_code}
    except Exception as e:
        results["SportsApiPro"] = {"erreur": str(e)}

    # OpenWeather - Météo
    try:
        r = requests.get(f"https://api.openweathermap.org/data/2.5/weather?q=Paris&appid={os.getenv('OPENWEATHER_KEY')}&units=metric",
            timeout=5)
        data = r.json()
        results["OpenWeather"] = {
            "status": r.status_code,
            "ville": data.get('name'),
            "temp": data['main']['temp'] if r.status_code == 200 else None
        }
    except Exception as e:
        results["OpenWeather"] = {"erreur": str(e)}

    # GNews
    try:
        r = requests.get(f"https://gnews.io/api/v4/top-headlines?topic=sports&lang=fr&token={os.getenv('GNEWS_API_KEY')}",
            timeout=5)
        results["GNews"] = {"status": r.status_code, "articles": len(r.json().get('articles', []))}
    except Exception as e:
        results["GNews"] = {"erreur": str(e)}

    # Highlightly
    try:
        r = requests.get("https://api.highlightly.net/highlights",
            headers={"x-rapidapi-key": os.getenv('HIGHLIGHTLY_KEY')},
            timeout=5)
        results["Highlightly"] = {"status": r.status_code}
    except Exception as e:
        results["Highlightly"] = {"erreur": str(e)}

    return jsonify(results)
