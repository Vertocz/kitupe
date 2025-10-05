import requests

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

# User-Agent recommandé par Wikidata
HEADERS = {
    "User-Agent": "KitupéApp/1.0 (ton-email@example.com)"
}

def run_sparql(query):
    headers = HEADERS.copy()
    headers["Accept"] = "application/sparql+json"
    r = requests.get(WIKIDATA_SPARQL, params={"query": query}, headers=headers)
    r.raise_for_status()
    return r.json()["results"]["bindings"]

def search_wikidata_entity(name):
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
    return results[0]["id"] if results else None

def get_label(qid):
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()
    data = r.json()["entities"][qid]
    return data["labels"].get("fr", data["labels"].get("en", {"value": qid}))["value"]

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
      VALUES ?prop {{ wdt:P127 wdt:P749 }}
      wd:{qid} ?prop ?owner .
      OPTIONAL {{ ?owner wdt:P31 ?ownerType . }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "fr,en". }}
    }}
    """
    owners = run_sparql(query)

    if not owners:
        results.append({"chemin": path, "type": "organisation"})
        return results

    for o in owners:
        owner_qid = o["owner"]["value"].split("/")[-1]
        owner_label = o["ownerLabel"]["value"]
        owner_type = o.get("ownerType", {}).get("value", "")

        new_path = path + [owner_label]

        if "Q5" in owner_type:  # instance of human
            results.append({"chemin": new_path, "type": "humain"})
        else:
            find_owners(owner_qid, visited, new_path, results)

    return results

def find_final_owners(name):
    qid = search_wikidata_entity(name)
    if not qid:
        return [{"chemin": [name], "type": "inconnu"}]
    return find_owners(qid)
