import requests
from bs4 import BeautifulSoup
import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = "postgresql://nageurs_meeting_user:owo8xWD8aSaDlmWBM7vQLvnHbTOxk9Fo@dpg-d55t02e3jp1c73a2r610-a.oregon-postgres.render.com/nageurs_meeting"  # Remplace par ton URL Render

VALEURS_genre = ["Dames", "Messieurs"]
VALEURS_courses = ["50 NAGE LIBRE", "100 NAGE LIBRE", "200 NAGE LIBRE", "400 NAGE LIBRE", "800 NAGE LIBRE",
                   "1500 NAGE LIBRE", "50 DOS", "100 DOS", "200 DOS", "50 BRASSE", "100 BRASSE", "200 BRASSE",
                   "50 PAP", "100 PAP", "200 PAP", "100 4N", "200 4N", "400 4N"]

VALEURS_categories = ["Benjamins et moins", "Juniors 1 et 2", "Juniors 3 et plus"]

epreuves = VALEURS_courses

class EpreuveID:
    ref_messieurs = [51, 52, 53, 54, 55, 56, 61, 62, 63, 71, 72, 73, 81, 82, 83, 90, 91, 92]
    ref_dames = [1, 2, 3, 4, 5, 6, 11, 12, 13, 21, 22, 23, 31, 32, 33, 40, 41, 42]


def get_CAT_ID_from_db(id_compet):
    """Récupère les catégories depuis la DB pour une compétition"""
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute("SELECT id_cats FROM competitions WHERE id_compet = %s;", (id_compet,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return row['id_cats']
    else:
        raise ValueError(f"Compétition avec id_compet={id_compet} introuvable.")


def generate_link(course, cat, sexe, id_compet):
    sexe_lower = sexe.lower()
    course_upper = course.upper()

    CAT_ID = get_CAT_ID_from_db(id_compet)

    if sexe_lower == 'm':
        epreuve_ref_tc = EpreuveID.ref_messieurs
        cat_dict = CAT_ID.get("messieurs", {})
    elif sexe_lower == 'd':
        epreuve_ref_tc = EpreuveID.ref_dames
        cat_dict = CAT_ID.get("dames", {})
    else:
        raise ValueError("Sexe invalide, doit être 'M' ou 'D'")

    if course not in epreuves:
        raise ValueError("Course invalide")
    if cat not in VALEURS_categories:
        raise ValueError("Catégorie invalide")

    epreuve_index = epreuves.index(course)
    epreuve = epreuve_ref_tc[epreuve_index]
    catid = cat_dict.get(cat)
    if not catid:
        raise ValueError(f"Catégorie '{cat}' non trouvée pour sexe '{sexe}'")

    link = f"https://www.liveffn.com/cgi-bin/programme.php?competition={id_compet}&langue=fra&cat_id={catid}&epr_id={epreuve}&typ_id=11"
    return link


def collect_results(link):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/116.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(link, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Erreur lors de la requête HTTP : {e}")
        return 'error'

    soup = BeautifulSoup(response.text, 'html.parser')
    tbody = soup.find('tbody', id='epr_')
    if not tbody:
        print("Tbody introuvable (id='epr_')")
        return 'error'

    rows = tbody.find_all('tr', class_=lambda x: x and 'survol' in x)
    results = []
    for row in rows:
        tds = row.find_all('td')
        if len(tds) < 6:
            continue
        img = tds[0].find('img')
        plot = img['src'][-5] if img and 'src' in img.attrs else ''
        name = tds[1].text.strip()
        club_nobr = tds[4].find('nobr') if tds[4] else None
        club = club_nobr.text.strip() if club_nobr else ''
        temps = tds[5].text.strip()
        results.append((plot, name, club, temps))
    return results


def ajouter_nageurs_si_absents_db(resultats):
    """Ajoute les nageurs dans la table 'nageurs' s'ils n'existent pas"""
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    # Récupérer tous les nageurs existants
    cur.execute("SELECT nom, prenom, club FROM nageurs;")
    existants = {(r['nom'].lower(), r['prenom'].lower(), r['club'].lower()) for r in cur.fetchall()}

    for _, nom_complet, club, _ in resultats:
        morceaux = nom_complet.strip().split()
        if len(morceaux) < 2:
            continue
        prenom = morceaux[-1]
        nom = " ".join(morceaux[:-1])
        cle = (nom.lower(), prenom.lower(), club.lower())
        if cle not in existants:
            cur.execute("""
                INSERT INTO nageurs (nom, prenom, club, photo_url)
                VALUES (%s, %s, %s, %s);
            """, (nom, prenom, club, ""))
            existants.add(cle)

    conn.commit()
    cur.close()
    conn.close()


def formater_nom_epreuve(course, genre, categorie):
    abbr_map = {
        "NAGE LIBRE": "NL",
        "BRASSE": "BR",
        "DOS": "DOS",
        "PAP": "PAP",
        "4N": "4N"
    }
    course_abbr = course.upper()
    for key, val in abbr_map.items():
        course_abbr = course_abbr.replace(key, val)
    genre_fmt = genre.title()
    categorie_fmt = " ".join(word.capitalize() for word in categorie.split())
    return f"{course_abbr} - {genre_fmt} - {categorie_fmt}"


def generer_finales_db(liste_finales, id_compet):
    finales_data = []

    for course, genre, categorie in liste_finales:
        sexe = 'd' if genre.lower().startswith('d') else 'm'
        try:
            link = generate_link(course, categorie, sexe, id_compet)
            resultats = collect_results(link)
            if resultats == 'error':
                continue
        except Exception as e:
            print(f"Erreur pour {course} {genre} {categorie} : {e}")
            continue

        ajouter_nageurs_si_absents_db(resultats)

        nom_epreuve = formater_nom_epreuve(course, genre, categorie)
        nageurs_epreuve = []
        for plot, nom_complet, club, temps in resultats:
            morceaux = nom_complet.strip().split()
            if len(morceaux) < 2:
                continue
            prenom = morceaux[-1]
            nom = " ".join(morceaux[:-1])
            nageurs_epreuve.append({
                "plot": plot,
                "nom": nom,
                "prenom": prenom,
                "club": club,
                "temps": temps,
                "age": "",
                "photo": ""
            })

        finales_data.append({
            "epreuve": nom_epreuve,
            "nageurs": nageurs_epreuve
        })

    return finales_data

def synchroniser_json_avec_photos(finales_data):
    """
    Complète le JSON généré par generer_finales_db avec les URLs de photos
    issues de la base de données pour chaque nageur.
    """
    if not finales_data:
        return finales_data

    # Connexion DB
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        cur = conn.cursor()
        # Récupérer tous les nageurs avec photo
        cur.execute("SELECT nom, prenom, club, photo_url FROM nageurs;")
        nageurs_db = cur.fetchall()

        # Indexer par (nom, prenom, club) en minuscules pour comparaison
        index_nageurs = {
            (n['nom'].lower(), n['prenom'].lower(), (n['club'] or '').lower()): n['photo_url']
            for n in nageurs_db
        }

        # Parcourir le JSON et compléter la photo
        for finale in finales_data:
            for nageur in finale['nageurs']:
                key = (
                    nageur['nom'].lower(),
                    nageur['prenom'].lower(),
                    (nageur.get('club') or '').lower()
                )
                if key in index_nageurs:
                    nageur['photo'] = index_nageurs[key]
    finally:
        conn.close()

    return finales_data

def completer_finale_6_plots(finale):
    nageurs_par_plot = {}

    for n in finale.get("nageurs", []):
        try:
            plot = int(n.get("plot"))
            nageurs_par_plot[plot] = n
        except Exception:
            continue

    nageurs_complets = []

    for plot in range(1, 7):
        if plot in nageurs_par_plot:
            # 🔧 forcer plot en string pour cohérence JS
            nageurs_par_plot[plot]["plot"] = str(plot)
            nageurs_complets.append(nageurs_par_plot[plot])
        else:
            nageurs_complets.append({
                "plot": str(plot),
                "nom": "",
                "prenom": "",
                "club": "",
                "temps": "",
                "age": "",
                "photo": "default"
            })

    finale["nageurs"] = nageurs_complets
