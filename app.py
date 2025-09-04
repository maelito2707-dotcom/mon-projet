from flask import Flask, jsonify, render_template, request
import requests

app = Flask(__name__)

@app.route('/')
def home():
    return render_template("index.html")

@app.route('/api/fetch')
def fetch_external():
    url = "https://www.liveffn.com/cgi-bin/programme.php?competition=79435&langue=fra&cat_id=558941&epr_id=21&typ_id=11"
    try:
        response = requests.get(url)
        response.raise_for_status()
        content = response.text
        # Pour simplifier, on renvoie tout le HTML (mais tu peux filtrer plus tard)
        return jsonify({"html": content})
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
