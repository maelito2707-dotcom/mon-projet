import os
import re
import psycopg2
import psycopg2.extras
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, jsonify
import io
import json
from flask import send_file

import live  # version DB-driven de live_ffn

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://nageurs_meeting_user:owo8xWD8aSaDlmWBM7vQLvnHbTOxk9Fo@dpg-d55t02e3jp1c73a2r610-a.oregon-postgres.render.com/nageurs_meeting"
)

app = Flask(__name__)


# ---------------- DB ---------------- #

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def get_competition_id():
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id_compet FROM competitions ORDER BY id DESC LIMIT 1;")
        row = cur.fetchone()
        return row["id_compet"] if row else None
    finally:
        conn.close()


def set_competition_id(new_id, nom="Nouvelle compétition"):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM competitions WHERE id_compet = %s;", (new_id,))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO competitions (nom, id_compet, id_cats)
                VALUES (%s, %s, %s)
            """, (nom, new_id, '{"dames": {}, "messieurs": {}}'))
        conn.commit()
    finally:
        conn.close()


# ---------------- INDEX ---------------- #

@app.route("/")
def index():
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id_compet, nom
            FROM competitions
            ORDER BY id;
        """)
        competitions = cur.fetchall()

        current_comp = get_competition_id()
        if not current_comp and competitions:
            current_comp = competitions[0]["id_compet"]

        return render_template(
            "index.html",
            competitions=competitions,
            current_comp=current_comp
        )
    finally:
        conn.close()


@app.route("/set-active-competition", methods=["POST"])
def set_active_competition():
    data = request.get_json()
    id_compet = data.get("id_compet")
    if id_compet:
        set_competition_id(id_compet)
    return jsonify({"status": "ok"})


# ---------------- PARAMÈTRES ---------------- #

@app.route("/parametres", methods=["GET", "POST"])
def parametres():
    conn = get_db_connection()
    try:
        cur = conn.cursor()

        cur.execute("SELECT id_compet, nom FROM competitions ORDER BY id;")
        competitions = cur.fetchall()

        current_comp = (
                request.args.get("competition_id")
                or request.form.get("competition_id")
                or get_competition_id()
        )

        if request.method == "POST":
            for key, value in request.form.items():
                if "::" not in key:
                    continue
                try:
                    genre, cat = key.split("::")
                    cur.execute("""
                        UPDATE competitions
                        SET id_cats = jsonb_set(
                            id_cats,
                            ARRAY[%s, %s],
                            %s,
                            true
                        )
                        WHERE id_compet = %s;
                    """, (genre.lower(), cat, f'"{value.strip()}"', current_comp))
                except Exception as e:
                    print("Erreur MAJ cat:", e)
            conn.commit()

        cur.execute("SELECT id_cats FROM competitions WHERE id_compet = %s;", (current_comp,))
        row = cur.fetchone()
        data = row["id_cats"] if row and row["id_cats"] else {"dames": {}, "messieurs": {}}

        return render_template(
            "parametres.html",
            data=data,
            competitions=competitions,
            current_comp=current_comp
        )
    finally:
        conn.close()


# ---------------- FINALISATION ---------------- #

@app.route("/associer-photos", methods=["POST"])
def associer_photos():
    data = request.get_json()
    courses = data.get("courses", [])
    id_compet = data.get("id_competition")

    if id_compet:
        set_competition_id(id_compet)

    finales = []
    for element in courses:
        try:
            course, rest = element.split(" - ")
            match = re.match(r"([DM])\s*\((.+)\)", rest)
            genre = "dames" if match.group(1) == "D" else "messieurs"
            categorie = match.group(2)
            finales.append((course.strip(), genre, categorie.strip()))
        except Exception as e:
            print("Erreur parsing:", element, e)

    print(live.generer_finales_db(finales, id_compet))

    return jsonify({
        "message": "Finales générées avec succès",
        "nombre_courses": len(finales)
    })


# ---------------- NAGEURS ---------------- #

def get_all_nageurs():
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, nom, prenom, club, photo_url FROM nageurs ORDER BY id;")
        return cur.fetchall()
    finally:
        conn.close()


def update_nageurs_photos_batch(updates):
    if not updates:
        return
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        values = ",".join(["(%s,%s)"] * len(updates))
        query = f"""
            UPDATE nageurs AS n
            SET photo_url = v.photo_url
            FROM (VALUES {values}) AS v(id, photo_url)
            WHERE n.id = v.id;
        """
        cur.execute(query, [x for tup in updates for x in tup])
        conn.commit()
    finally:
        conn.close()


@app.route("/update_nageurs", methods=["POST"])
def update_nageurs():
    data = request.get_json()
    nageurs = get_all_nageurs()

    index = {
        (n["nom"].lower(), n["prenom"].lower(), (n["club"] or "").lower()): n["id"]
        for n in nageurs
    }

    updates = []
    for n in data:
        key = (n["nom"].lower(), n["prenom"].lower(), (n.get("club") or "").lower())
        if key in index and n.get("photo"):
            updates.append((index[key], n["photo"]))

    update_nageurs_photos_batch(updates)
    return jsonify({"updated": len(updates)})


@app.route("/nageurs")
def afficher_nageurs():
    return render_template("nageurs.html", nageurs=get_all_nageurs())


@app.route("/generer-presentation", methods=["POST"])
def generer_presentation():
    data = request.get_json()
    courses = data.get("courses", [])
    id_compet = data.get("id_competition")

    if not courses or not id_compet:
        return jsonify({"error": "Données manquantes"}), 400

    finales = []

    for element in courses:
        try:
            # Séparer "Course - D (Catégorie)"
            course, rest = element.split(" - ")
            match = re.match(r"([DM])\s*\((.+)\)", rest)
            if not match:
                print("Format inattendu:", element)
                continue
            genre = "dames" if match.group(1).upper() == "D" else "messieurs"
            categorie = match.group(2)
            finales.append((course.strip(), genre, categorie.strip()))
        except Exception as e:
            print("Erreur parsing:", element, e)

    # 🔥 Générer le JSON via live.py
    json_final = live.generer_finales_db(finales, id_compet)

    # 🔥 Compléter avec les photos depuis la DB
    json_final = live.synchroniser_json_avec_photos(json_final)

    # 🖨️ Debug : print JSON final
    print("\n========== JSON PRÉSENTATION ==========")
    for epreuve in json_final:
        print(epreuve)
    print("======================================\n")

    return jsonify(json_final)


from datetime import datetime

@app.route("/download-html", methods=["POST"])
def download_html():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Aucune donnée reçue"}), 400

    courses = data.get("courses", [])
    id_compet = data.get("id_competition")
    if not courses or not id_compet:
        return jsonify({"error": "Données manquantes"}), 400

    # Préparer les finales comme dans generer_presentation
    finales = []
    for element in courses:
        try:
            course, rest = element.split(" - ")
            match = re.match(r"([DM])\s*\((.+)\)", rest)
            if not match:
                continue
            genre = "dames" if match.group(1).upper() == "D" else "messieurs"
            categorie = match.group(2)
            finales.append((course.strip(), genre, categorie.strip()))
        except Exception as e:
            print("Erreur parsing:", element, e)

    json_final = live.generer_finales_db(finales, id_compet)
    json_final = live.synchroniser_json_avec_photos(json_final)
    json_str = json.dumps(json_final, ensure_ascii=False, indent=2)

    # 2️⃣ Template HTML avec le JSON injecté
    html_content = """
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Frame Responsive avec Stinger</title>
<style>
html, body {
  margin: 0;
  padding: 0;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: black;
  display: flex;
  justify-content: center;
  align-items: center;
}

#frame {
  width: 1920px;
  height: 1080px;
  position: relative;
  transform-origin: top left;
  overflow: hidden;
}

/* Iframes */
iframe {
  position: absolute;
  top: 0; left: 0;
  width: 100%;
  height: 100%;
  border: none;
}

#page1 { z-index: 2; display: block; }
#page2 { z-index: 1; display: block; }

/* Stinger */
#stinger {
  position: fixed;
  top: 0; left: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: none;
  z-index: 3;
  pointer-events: none;
}
</style>
<style>
/* Animation existante pour l'apparition */
@keyframes zoomRotate {
    0% { transform: scale(0.5) rotate(-10deg); opacity: 0; }
    100% { transform: scale(1) rotate(0deg); opacity: 1; }
}

/* Nouvelle animation pour la disparition */
@keyframes bounceDown {
    0% { transform: translateY(0) scale(1); opacity: 1; }
    30% { transform: translateY(20px) scale(0.95); }
    60% { transform: translateY(-10px) scale(0.9); }
    100% { transform: translateY(100vh) scale(0.5); opacity: 0; }
}
</style>
</head>
<body>
<div id="frame">

<iframe 
    id="confettiFrame" 
    src="confettis2.html"
    style="position:absolute; top:0; left:0; width:100%; height:100%; border:none; z-index:3; pointer-events:none;">
</iframe>


  <!-- Pages -->
  <iframe id="page1" src="nouveau_manege.html?mode=normal"></iframe>
  <iframe id="page2" src="nouveau_manege.html?mode=zoom"></iframe>

  <!-- Stinger -->
  <video id="stinger" src="video.webm"></video>

<div id="CarteContainer" style="
    position: absolute;   
    top: 30%;           
    left: 12%;           
    z-index: 4;
    transform: rotate(-5deg) scale(1.6);
    width: 28%;
">
  <div id="carteOverlay" style="
       display:none;
       position:relative;
       width:100%;
       padding-top:45%; 
       border-radius:10px;
       z-index:5;
       font-family:'Hamish', sans-serif;
       opacity:0;
       transform:rotate(-5deg);
       container-type:inline-size;
  ">
      <img src="etiquette1.png" alt="Overlay Image" 
           style="position:absolute; top:0; left:0; width:100%; height:100%; object-fit:cover; border-radius:10px;">
      <div id="prenom" style="position:absolute; top:20%; left:12%; font-size:7cqw; color:#FF3333;">Maël</div>
      <div id="nom" style="position:absolute; top:37%; left:12%; font-size:7cqw; color:#FF3333;">THEROUANNE</div>
      <div id="club" style="position:absolute; top:57%; left:12%; font-size:4.8cqw; color:#FCB330;">Amiens Métropole Natation</div>
      <div id="temps" style="position:absolute; bottom:10%; right:27%; font-size:6.2cqw; color:#A0E2E2;">2'19"21</div>
  </div>
</div>
</div>

<script>
const frame = document.getElementById('frame');
const page1 = document.getElementById('page1');
const page2 = document.getElementById('page2');
const stinger = document.getElementById('stinger');
const iframeConfetti = document.getElementById("confettiFrame");

const cutTimeMs = 1100; // moment du cut en ms

// --- Frame responsive ---
function resizeFrame() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const scale = Math.min(w / 1920, h / 1080);
  frame.style.transform = `scale(${scale})`;
  frame.style.position = 'absolute';
  frame.style.left = `${(w - 1920 * scale)/2}px`;
  frame.style.top = `${(h - 1080 * scale)/2}px`;
}
window.addEventListener('resize', resizeFrame);
window.addEventListener('DOMContentLoaded', resizeFrame);

// --- Stinger + Cut ---
function playStingerAndCut() {
  stinger.style.display = 'block';
  stinger.currentTime = 0;
  stinger.play();

  setTimeout(() => {
    page1.style.display = 'none'; // cut vers la page zoom
  }, cutTimeMs);

  stinger.onended = () => {
    stinger.style.display = 'none';
  };
}

// --- Fonction pour envoyer des messages à n'importe quelle iframe ---
function sendMessageToIframe(iframe, message) {
  if (iframe && iframe.contentWindow) {
    iframe.contentWindow.postMessage(message, "*");
  }
}

// --- Fonction spécifique pour spawn confettis ---
function spawnConfetti(count = 150) {
  sendMessageToIframe(iframeConfetti, { type: "spawnConfetti", count });
}

// --- Fonction pour mettre à jour uniquement img2 des nacelles d'une iframe ---
function updateNacellesImg2Iframe(iframe, newImg2Array) {
  sendMessageToIframe(iframe, {
    action: "updateImg2",
    img2Array: newImg2Array
  });
}

// --- Exemple d'utilisation ---
// Supposons que tu veuilles mettre à jour page2
const nouvellesImg2 = [
  "",
  "",
  "",
  "",
  "",
  ""
];


// --- Gestion centralisée du clavier ---
function handleKeyDown(e) {
  switch(e.key.toLowerCase()) {
    case " ": // espace : stinger + actions page2
      e.preventDefault();
      playStingerAndCut();
      sendMessageToIframe(page2, { action: "setAngle", angle: 90 });
      setTimeout(() => {
        sendMessageToIframe(page2, { action: "arreterProgressif", duree: 4000 });
      }, cutTimeMs + 1000);
      break;

    case "s":
      sendMessageToIframe(page2, { action: "arreterProgressif", duree: 2000 });
      break;
    case "t":
      sendMessageToIframe(page2, { action: "tournerProgressif", angle: 60, duree: 3000 });
      break;
    case "a":
      updateNacellesImg2Iframe(page1, nouvellesImg2);
      break;
  }
}

// --- Écoute clavier sur document et window ---
document.addEventListener("keydown", handleKeyDown);
window.addEventListener("keydown", handleKeyDown);

console.log("Gestion clavier parent et confetti prête !");

function spawnConfettiAtCarteOverlay(count = 150, offsetX = 0, offsetY = 0) {
    const overlay = document.getElementById("carteOverlay");
    if (!overlay) return;

    // Position et taille de l'étiquette
    const rect = overlay.getBoundingClientRect();
    const frameRect = iframeConfetti.getBoundingClientRect();

    // Récupérer le scale appliqué au frame
    const style = window.getComputedStyle(document.getElementById("frame"));
    const match = style.transform.match(/matrix\(([^,]+),[^,]+,[^,]+,[^,]+,[^,]+,[^,]+\)/);
    const scale = match ? parseFloat(match[1]) : 1;

    // Coordonnées exactes du centre, ajustées avec scale et offset
    const x = (rect.left + rect.width / 2 - frameRect.left + offsetX) / scale;
    const y = (rect.top + rect.height / 2 - frameRect.top + offsetY) / scale;

    sendMessageToIframe(iframeConfetti, {
        type: "spawnConfetti",
        x: x,
        y: y,
        count: count
    });
}

</script>
<script>
window.addEventListener("keydown", (e) => {
    const overlay = document.getElementById("carteOverlay");
    const confettiFrame = document.getElementById("confettiFrame");

    if (e.code === "KeyO") { // touche O pour lancer overlay + confettis
        overlay.style.display = "block";
        overlay.style.animation = "none";   // reset animation
        overlay.offsetHeight;               // reflow
        overlay.style.animation = "zoomRotate 1s forwards";

        const rect = overlay.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;
        const centerY = rect.top + rect.height / 2;

        spawnConfettiAtCarteOverlay(count = 150);
    }

    if (e.code === "KeyP") { // touche P pour cacher l'overlay avec animation
        overlay.style.animation = "bounceDown 0.8s forwards";
        // Après la fin de l'animation, on cache complètement l'overlay
        overlay.addEventListener("animationend", function handler() {
            overlay.style.display = "none";
            overlay.removeEventListener("animationend", handler);
        });
    }
});
</script>
<script>
// --- Fonction pour mettre à jour le contenu de l'étiquette ---
function updateCarteOverlay({ prenom, nom, club, temps }) {
    const overlay = document.getElementById("carteOverlay");
    if (!overlay) return;

    if (prenom !== undefined) {
        const prenomElem = document.getElementById("prenom");
        if (prenomElem) prenomElem.textContent = prenom;
    }
    if (nom !== undefined) {
        const nomElem = document.getElementById("nom");
        if (nomElem) nomElem.textContent = nom;
    }
    if (club !== undefined) {
        const clubElem = document.getElementById("club");
        if (clubElem) clubElem.textContent = club;
    }
    if (temps !== undefined) {
        const tempsElem = document.getElementById("temps");
        if (tempsElem) tempsElem.textContent = temps;
    }
}

// --- Gestion de message depuis parent pour mettre à jour l'étiquette ---
window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || typeof data !== "object") return;

    if (data.action === "updateCarteOverlay") {
        updateCarteOverlay(data.content);
        console.log("Carte overlay mise à jour :", data.content);
    }
});

// --- Exemple : mise à jour depuis le clavier ---
window.addEventListener("keydown", (e) => {
    if (e.code === "KeyU") { // touche U pour tester la mise à jour
        updateCarteOverlay({
            prenom: "Léo",
            nom: "DUPONT",
            club: "Paris Natation",
            temps: "1'45\"32"
        });
    }
});
</script>
<script>
// --- Ton gros data ---
const finales = {"finales":""" + json_str + """};
let finaleIndex = 0;
let nageurIndex = 0;

function afficherNageurCourant() {
    const finale = finales.finales[finaleIndex];
    const nageur = finale.nageurs[nageurIndex];

    // Met à jour la carte sans toucher à l'image
    const overlay = document.getElementById("carteOverlay");
    if (!overlay) return;
    overlay.querySelector("#prenom").textContent = nageur.prenom;
    overlay.querySelector("#nom").textContent = nageur.nom;
    overlay.querySelector("#club").textContent = nageur.club;
    overlay.querySelector("#temps").textContent = nageur.temps;
}

function envoyerPhotosNacelles() {
    const finale = finales.finales[finaleIndex];
    if (!finale) return;

    const img2Array = finale.nageurs.map(n => n.photo);
    // Complète si moins de 6 nacelles
    while (img2Array.length < 6) img2Array.push("");

    // Envoi à page1 et page2
    updateNacellesImg2Iframe(page1, img2Array);
    updateNacellesImg2Iframe(page2, img2Array);
}


// Gestion clavier pour navigation
window.addEventListener("keydown", (e) => {
    const finale = finales.finales[finaleIndex];
    if (!finale) return;

    switch(e.code) {
        case "ArrowUp": // finale précédente
            finaleIndex = (finaleIndex - 1 + finales.finales.length) % finales.finales.length;
            nageurIndex = 0; // reset nageur
            afficherNageurCourant();
            envoyerPhotosNacelles();
            break;
        case "ArrowDown": // finale suivante
            finaleIndex = (finaleIndex + 1) % finales.finales.length;
            nageurIndex = 0;
            afficherNageurCourant();
            envoyerPhotosNacelles();
            break;
        case "ArrowLeft": // nageur précédent
            nageurIndex = (nageurIndex - 1 + finale.nageurs.length) % finale.nageurs.length;
            afficherNageurCourant();
            break;
        case "ArrowRight": // nageur suivant
            nageurIndex = (nageurIndex + 1) % finale.nageurs.length;
            afficherNageurCourant();
            break;
    }
});

// --- Touche U pour passer au nageur suivant ---
window.addEventListener("keydown", (e) => {
    if (e.code === "KeyU") {
        updateCarteInfos();
    }
});

// --- fonction utilitaire delay ---
function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// --- fonction principale pour dérouler la finale ---
// --- remplacer updateCarte() par afficherNageurCourant() ---
async function lancerFinale() {
    const finale = finales.finales[finaleIndex];
    if (!finale) return;

    const nombreNageurs = finale.nageurs.length;
    const overlay = document.getElementById("carteOverlay");

    // 1️⃣ Stinger + arreterProgressif
    playStingerAndCut();
    sendMessageToIframe(page2, { action: "setAngle", angle: 90 });
    setTimeout(() => {
        sendMessageToIframe(page2, { action: "arreterProgressif", duree: 4000 });
    }, cutTimeMs + 1000);

    await delay(3000); // attendre 3 sec après le début de arreterProgressif

    // Afficher carte du 1er nageur
    afficherNageurCourant();
    await new Promise(r => requestAnimationFrame(r)); // ⬅️ attend que le navigateur applique le reflow
    overlay.style.display = "block";
    overlay.style.animation = "none";
    overlay.offsetHeight; // reset animation
    overlay.style.animation = "zoomRotate 1s forwards";
    spawnConfettiAtCarteOverlay();
    await delay(3000);

    overlay.style.animation = "bounceDown 0.8s forwards";
    await delay(800);
    overlay.style.display = "none";

    // 🔁 Pour les nageurs suivants
    for (let i = 1; i < nombreNageurs; i++) {
        nageurIndex = i;
        afficherNageurCourant();
        await new Promise(r => requestAnimationFrame(r)); // ⬅️ force rendu avant animation

        // Tourner de 60° en 3 sec
        sendMessageToIframe(page2, { action: "tournerProgressif", angle: 60, duree: 3000 });

        // 2 sec après début rotation, faire apparaître la carte
        await delay(2000);
        overlay.style.display = "block";
        overlay.style.animation = "none";
        overlay.offsetHeight;
        overlay.style.animation = "zoomRotate 1s forwards";
        spawnConfettiAtCarteOverlay();
        await delay(3000);

        overlay.style.animation = "bounceDown 0.8s forwards";
        await delay(800);
        overlay.style.display = "none";

        await delay(1200); // attendre fin rotation avant le prochain nageur
    }
}


// --- Initialisation finale 1, nageur 0 ---
window.addEventListener("DOMContentLoaded", () => {
    finaleIndex = 0;
    nageurIndex = 0;
    afficherNageurCourant();
    envoyerPhotosNacelles();
});


// --- gestion touche & ---
window.addEventListener("keydown", (e) => {
    if (e.key === "&") {
        lancerFinale();
    }
});

let page1Loaded = false;
let page2Loaded = false;

page1.addEventListener("load", () => {
    page1Loaded = true;
    if (page2Loaded) envoyerPhotosNacelles();
});

page2.addEventListener("load", () => {
    page2Loaded = true;
    if (page1Loaded) envoyerPhotosNacelles();
});

// Carte du premier nageur dès que DOM prêt
window.addEventListener("DOMContentLoaded", () => {
    finaleIndex = 0;
    nageurIndex = 0;
    afficherNageurCourant();
});


</script>
</body>
</html>
"""

    # 3️⃣ Écriture en mémoire
    html_bytes = io.BytesIO()
    html_bytes.write(html_content.encode("utf-8"))
    html_bytes.seek(0)

    # 4️⃣ Nom du fichier avec date/heure
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f'presentation_{timestamp}.html'

    # 5️⃣ Retour du fichier
    return send_file(
        html_bytes,
        mimetype="text/html",
        as_attachment=True,
        download_name=filename
    )


if __name__ == "__main__":
    app.run(debug=True)
