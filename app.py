from flask import Flask, render_template, request
import requests

app = Flask(__name__)

HEADERS = {
    "User-Agent": "KitupéApp/1.0 (contact@example.com)"
}

# --- Fonctions Wikidata simples sans SPARQL ---

def search_wikidata_entity(name):
    """Recherche le QID d'une entité à partir de son nom"""
    url = "https://www.wikidata.org/w/api.php"
    params = {
        "action": "wbsearchentities",
        "search": name,
        "language": "fr",
        "format": "json"
    }
    r = requests.get(url, params=params, headers=HEADERS)
    r.raise_for_status()
    results = r.json().get("search", [])
    if not results:
        return None
    return results[0]["id"]

def get_entity_data(qid):
    """Télécharge les données complètes d'une entité Wikidata"""
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    return data["entities"][qid]

def get_label(entity):
    """Retourne le label FR ou EN d'une entité"""
    labels = entity.get("labels", {})
    return labels.get("fr", labels.get("en", {"value": "Inconnu"}))["value"]

def extract_owners(entity):
    """Récupère les QIDs des propriétaires ou organisations parentes"""
    claims = entity.get("claims", {})
    owners = []
    for prop in ["P127", "P749"]:  # propriétaire / organisation mère
        if prop in claims:
            for claim in claims[prop]:
                mainsnak = claim.get("mainsnak", {})
                datavalue = mainsnak.get("datavalue", {})
                if datavalue.get("type") == "wikibase-entityid":
                    owners.append(datavalue["value"]["id"])
    return owners

def find_owners_recursive(qid, visited=None, path=None, results=None):
    """Parcourt récursivement les propriétaires jusqu'à trouver un humain ou un fond"""
    if visited is None:
        visited = set()
    if path is None:
        path = []
    if results is None:
        results = []

    if qid in visited:
        return results
    visited.add(qid)

    entity = get_entity_data(qid)
    label = get_label(entity)
    path = path + [label]

    owners = extract_owners(entity)
    if not owners:
        results.append({
            "chemin": path,
            "type": "organisation",
            "wikipedia": f"https://fr.wikipedia.org/wiki/Special:GoToLinkedPage/frwiki/{qid}"
        })
        return results

    for owner_qid in owners:
        owner_entity = get_entity_data(owner_qid)
        owner_label = get_label(owner_entity)

        # Vérifie si c’est un humain
        instance_of = [
            c["mainsnak"]["datavalue"]["value"]["id"]
            for c in owner_entity.get("claims", {}).get("P31", [])
            if "datavalue" in c["mainsnak"]
        ]

        if "Q5" in instance_of:  # Q5 = humain
            results.append({
                "chemin": path + [owner_label],
                "type": "humain",
                "wikipedia": f"https://fr.wikipedia.org/wiki/Special:GoToLinkedPage/frwiki/{owner_qid}"
            })
        else:
            find_owners_recursive(owner_qid, visited, path + [owner_label], results)

    return results

def find_final_owners(name):
    """Recherche principale depuis un nom simple"""
    qid = search_wikidata_entity(name)
    if not qid:
        return [{"chemin": [name], "type": "inconnu"}]
    return find_owners_recursive(qid)


# --- Interface Flask ---

@app.route("/", methods=["GET", "POST"])
def index():
    results = None
    query = ""
    if request.method == "POST":
        query = request.form.get("brand")
        if query:
            results = find_final_owners(query)
    return render_template("index.html", results=results, query=query)


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

