from flask import Flask, jsonify, render_template

app = Flask(__name__)

@app.route('/')
def home():
    return render_template("index.html")

@app.route('/api/data')
def get_data():
    return jsonify({"message": "Hello depuis le backend Flask sur Render!"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
