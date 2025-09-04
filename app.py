from flask import Flask, render_template, send_from_directory
import requests
from bs4 import BeautifulSoup
from collections import defaultdict
import json
import os

app = Flask(__name__)

JSON_PATH = os.path.join('static', 'top3_categories.json')

def scrape_and_save():
    url = "https://www.liveffn.com/cgi-bin/resultats.php?competition=88281&langue=fra&go=epreuve&epreuve=51"
    response = requests.get(url)
    response.raise_for_status()
    response.encoding = 'utf-8'

    soup = BeautifulSoup(response.text, 'html.parser')

    top_results = defaultdict(list)
    current_category = ""

    for row in soup.find_all("tr"):
        ep = row.find("td", class_="epreuve")
        if ep:
            current_category = ep.get_text(strip=True)
            continue

        if "survol" in row.get("class", []):
            cols = row.find_all("td")
            if len(cols) >= 9:
                if len(top_results[current_category]) < 3:
                    top_results[current_category].append({
                        "place": cols[0].get_text(strip=True),
                        "nom": cols[1].get_text(strip=True),
                        "naissance": cols[2].get_text(strip=True),
                        "pays": cols[3].get_text(strip=True),
                        "club": cols[4].get_text(strip=True),
                        "temps": cols[5].get_text(strip=True),
                        "points": cols[8].get_text(strip=True),
                    })

    os.makedirs('static', exist_ok=True)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(top_results, f, ensure_ascii=False, indent=2)

@app.route('/')
def index():
    # Optionnel : actualiser les données à chaque chargement
    # scrape_and_save()
    return render_template('index.html')

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    scrape_and_save()
    app.run(host='0.0.0.0', port=5000)
