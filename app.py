from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
import json
from typing import Optional, List, Dict
import logging

app = Flask(__name__)

# Configuration
HEADERS = {
    "User-Agent": "KitupéApp/1.0 (contact@example.com)"
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# WIKIDATA
# ============================================================================

def search_wikidata_entity(name: str) -> Optional[str]:
    """Cherche le QID d'une entité sur Wikidata"""
    try:
        url = "https://www.wikidata.org/w/api.php"
        params = {
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "format": "json"
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=5)
        r.raise_for_status()
        results = r.json().get("search", [])
        if not results:
            return None
        return results[0]["id"]
    except Exception as e:
        logger.warning(f"Wikidata search error for '{name}': {e}")
        return None

def get_wikidata_entity(qid: str) -> Optional[Dict]:
    """Récupère les données d'une entité Wikidata"""
    try:
        url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
        r = requests.get(url, headers=HEADERS, timeout=5)
        r.raise_for_status()
        return r.json()["entities"][qid]
    except Exception as e:
        logger.warning(f"Wikidata fetch error for '{qid}': {e}")
        return None

def get_label(entity: Dict) -> str:
    """Extrait le label d'une entité"""
    labels = entity.get("labels", {})
    return labels.get("en", {}).get("value", "Unknown")

def is_human(entity: Dict) -> bool:
    """Vérifie si c'est une personne (Q5)"""
    claims = entity.get("claims", {})
    instance_of = claims.get("P31", [])
    for claim in instance_of:
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})
        if datavalue.get("type") == "wikibase-entityid":
            if datavalue["value"]["id"] == "Q5":
                return True
    return False

def extract_owners_wikidata(qid: str, visited=None, path=None) -> List[Dict]:
    """Récupère récursivement les propriétaires sur Wikidata"""
    if visited is None:
        visited = set()
    if path is None:
        path = []
    
    if qid in visited or len(path) > 10:  # Limite de profondeur
        return []
    
    visited.add(qid)
    entity = get_wikidata_entity(qid)
    if not entity:
        return []
    
    label = get_label(entity)
    current_path = path + [label]
    
    # Cherche les propriétaires (P127) et organisations mères (P749)
    owners = []
    claims = entity.get("claims", {})
    
    for prop in ["P127", "P749"]:
        if prop in claims:
            for claim in claims[prop]:
                mainsnak = claim.get("mainsnak", {})
                datavalue = mainsnak.get("datavalue", {})
                if datavalue.get("type") == "wikibase-entityid":
                    owner_qid = datavalue["value"]["id"]
                    owner_entity = get_wikidata_entity(owner_qid)
                    if owner_entity:
                        owner_label = get_label(owner_entity)
                        if is_human(owner_entity):
                            owners.append({
                                "path": current_path + [owner_label],
                                "is_human": True,
                                "wikipedia_url": f"https://fr.wikipedia.org/wiki/Special:GoToLinkedPage/frwiki/{owner_qid}"
                            })
                        else:
                            # Récurse
                            sub_owners = extract_owners_wikidata(owner_qid, visited, current_path)
                            owners.extend(sub_owners)
    
    if not owners:
        owners.append({
            "path": current_path,
            "is_human": False,
            "wikipedia_url": f"https://fr.wikipedia.org/wiki/Special:GoToLinkedPage/frwiki/{qid}"
        })
    
    return owners

# ============================================================================
# WIKIPEDIA
# ============================================================================

def search_wikipedia_entity(name: str) -> Optional[str]:
    """Cherche une page Wikipedia"""
    try:
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": name,
            "format": "json"
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=5)
        r.raise_for_status()
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return None
        return results[0]["title"]
    except Exception as e:
        logger.warning(f"Wikipedia search error for '{name}': {e}")
        return None

def extract_owners_wikipedia(title: str) -> List[Dict]:
    """Scrape l'infobox Wikipedia pour trouver les propriétaires"""
    try:
        url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
        r = requests.get(url, headers=HEADERS, timeout=5)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        
        infobox = soup.find('table', {'class': 'infobox'})
        if not infobox:
            return []
        
        owners = []
        path = [title]
        
        # Cherche "Parent company", "Owner", "Headquarters" dans l'infobox
        for row in infobox.find_all('tr'):
            th = row.find('th')
            td = row.find('td')
            
            if not th or not td:
                continue
            
            header = th.get_text(strip=True).lower()
            content = td.get_text(strip=True)
            
            if any(keyword in header for keyword in ['parent company', 'owner', 'owned by']):
                path.append(content)
                owners.append({
                    "path": path,
                    "is_human": len(path) > 1,  # Heuristique simple
                    "wikipedia_url": url
                })
                break
        
        if not owners:
            owners.append({
                "path": path,
                "is_human": False,
                "wikipedia_url": url
            })
        
        return owners
    except Exception as e:
        logger.warning(f"Wikipedia scrape error for '{title}': {e}")
        return []

# ============================================================================
# OPENCORPORATES
# ============================================================================

def search_opencorporates(name: str) -> Optional[Dict]:
    """Cherche sur OpenCorporates"""
    try:
        url = "https://api.opencorporates.com/v0.4/companies/search"
        params = {
            "q": name,
            "format": "json"
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=5)
        r.raise_for_status()
        results = r.json().get("results", {}).get("companies", [])
        if not results:
            return None
        return results[0]
    except Exception as e:
        logger.warning(f"OpenCorporates search error for '{name}': {e}")
        return None

def extract_owners_opencorporates(name: str) -> List[Dict]:
    """Récupère les propriétaires via OpenCorporates"""
    company = search_opencorporates(name)
    if not company:
        return []
    
    try:
        owners = []
        path = [company.get("name", name)]
        
        parent = company.get("parent_company_name")
        if parent:
            path.append(parent)
        
        # OpenCorporates ne donne pas directement les propriétaires finaux
        # C'est surtout pour avoir la structure légale
        owners.append({
            "path": path,
            "is_human": False,
            "wikipedia_url": company.get("url", "")
        })
        
        return owners
    except Exception as e:
        logger.warning(f"OpenCorporates extract error: {e}")
        return []

# ============================================================================
# AGREGATION
# ============================================================================

def find_all_owners(query: str) -> Dict:
    """Cherche les propriétaires sur toutes les sources"""
    results = {
        "query": query,
        "wikidata": [],
        "wikipedia": [],
        "opencorporates": [],
        "best_result": None,
        "alternatives": []
    }
    
    # Wikidata
    wikidata_qid = search_wikidata_entity(query)
    if wikidata_qid:
        results["wikidata"] = extract_owners_wikidata(wikidata_qid)
    
    # Wikipedia
    wiki_title = search_wikipedia_entity(query)
    if wiki_title:
        results["wikipedia"] = extract_owners_wikipedia(wiki_title)
    
    # OpenCorporates
    results["opencorporates"] = extract_owners_opencorporates(query)
    
    # Détermine le meilleur résultat (celui qui trouve un humain avec le chemin le plus long)
    all_results = [
        ("Wikidata", r) for r in results["wikidata"]
    ] + [
        ("Wikipedia", r) for r in results["wikipedia"]
    ] + [
        ("OpenCorporates", r) for r in results["opencorporates"]
    ]
    
    # Tri : humain d'abord, puis par longueur du chemin
    all_results.sort(
        key=lambda x: (-x[1].get("is_human", False), -len(x[1].get("path", []))),
        reverse=True
    )
    
    if all_results:
        best_source, best_data = all_results[0]
        results["best_result"] = {
            "source": best_source,
            "path": best_data.get("path", []),
            "is_human": best_data.get("is_human", False),
            "url": best_data.get("wikipedia_url", "")
        }
        
        # Alternatives (résultats différents du meilleur)
        seen_paths = {tuple(results["best_result"]["path"])}
        for source, data in all_results[1:]:
            path_tuple = tuple(data.get("path", []))
            if path_tuple not in seen_paths:
                results["alternatives"].append({
                    "source": source,
                    "path": data.get("path", []),
                    "is_human": data.get("is_human", False),
                    "url": data.get("wikipedia_url", "")
                })
                seen_paths.add(path_tuple)
    
    return results

# ============================================================================
# ROUTES
# ============================================================================

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/search", methods=["POST"])
def search():
    data = request.get_json()
    query = data.get("query", "").strip()
    
    if not query:
        return jsonify({"error": "Empty query"}), 400
    
    results = find_all_owners(query)
    return jsonify(results)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
