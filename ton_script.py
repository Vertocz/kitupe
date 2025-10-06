import requests

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

# User-Agent recommandé par Wikidata
HEADERS = {
    "User-Agent": "KitupéApp/1.0 (ton-email@example.com)"
}

def run_sparql(query):
    headers = HEADERS.copy()
    headers["Accept"] = "application/sparql+json"
    try:
        r = requests.get(WIKIDATA_SPARQL, params={"query": query}, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json().get("results", {}).get("bindings", [])
    except requests.exceptions.RequestException as e:
        print("Erreur HTTP SPARQL:", e)
        return []
    except ValueError:
        print("Réponse SPARQL non JSON")
        return []

def search_wikidata_entity(name):
    url = "https://www.wikidata.org/w/api.php"
    params = {
        "action": "wbsearchentities",
        "search": name,
        "language": "fr",
        "format": "json"
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        results = data.get("search", [])
        if not results:
            print(f"Aucun résultat trouvé pour '{name}'")
            return None
        return results[0]["id"]
    except requests.exceptions.RequestException as e:
        print("Erreur HTTP search_wikidata_entity:", e)
        return None
    except ValueError:
        print("Réponse search_wikidata_entity non JSON")
        return None

def get_label(qid):
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        entity = data.get("entities", {}).get(qid, {})
        labels = entity.get("labels", {})
        return labels.get("fr", labels.get("en", {"value": qid}))["value"]
    except requests.exceptions.RequestException as e:
        print("Erreur HTTP get_label:", e)
        return qid
    except ValueError:
        print("Réponse get_label non JSON")
        return qid

def find_owners(qid, visited=None, path=None, results=None):
    if visited is None:
        visited = set()
    if path is None:
        path = [get_label(qid)]
    if results is None:
        results = []

    if qid in visited:
        return results
    visited.add(qid)

    query = f"""
    SELECT ?owner ?ownerLabel ?ownerType WHERE {{
      VALUES ?prop {{ wdt:P127 wdt:P749 ^wdt:P127 ^wdt:P749 }}
      wd:{qid} ?prop ?owner .
      OPTIONAL {{ ?owner wdt:P31 ?ownerType . }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "fr,en". }}
    }}
    """
    owners = run_sparql(query)

    if not owners:
        results.append({
            "chemin": path,
            "type": "organisation",
            "wikipedia": f"https://fr.wikipedia.org/wiki/Special:GoToLinkedPage/frwiki/{qid}"
        })
        return results

    for o in owners:
        owner_qid = o["owner"]["value"].split("/")[-1]
        owner_label = o["ownerLabel"]["value"]
        owner_type = o.get("ownerType", {}).get("value", "")
        wiki_link = f"https://fr.wikipedia.org/wiki/Special:GoToLinkedPage/frwiki/{owner_qid}"

        new_path = path + [owner_label]

        if "Q5" in owner_type:  # humain
            results.append({
                "chemin": new_path,
                "type": "humain",
                "wikipedia": wiki_link
            })
        else:
            # continuer récursivement
            find_owners(owner_qid, visited, new_path, results)

    return results

def find_final_owners(name):
    qid = search_wikidata_entity(name)
    if not qid:
        return [{"chemin": [name], "type": "inconnu"}]
    return find_owners(qid)
