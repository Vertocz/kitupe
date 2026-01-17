from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
import json
from typing import Optional, List, Dict
import logging
import re
from urllib.parse import quote

app = Flask(__name__)

# Configuration
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# WIKIDATA - Source 1
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
        logger.warning(f"Wikidata search error: {e}")
        return None

def get_wikidata_entity(qid: str) -> Optional[Dict]:
    """Récupère les données d'une entité Wikidata"""
    try:
        url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
        r = requests.get(url, headers=HEADERS, timeout=5)
        r.raise_for_status()
        return r.json()["entities"][qid]
    except Exception as e:
        logger.warning(f"Wikidata fetch error: {e}")
        return None

def get_label(entity: Dict) -> str:
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

def extract_owners_wikidata(qid: str, visited=None, path=None, depth=0) -> List[Dict]:
    """Récupère récursivement les propriétaires sur Wikidata"""
    if visited is None:
        visited = set()
    if path is None:
        path = []
    if depth > 8 or qid in visited:
        return []
    
    visited.add(qid)
    entity = get_wikidata_entity(qid)
    if not entity:
        return []
    
    label = get_label(entity)
    current_path = path + [label]
    owners = []
    claims = entity.get("claims", {})
    
    # P127 (propriétaire), P749 (org mère), P112 (fondateur)
    for prop in ["P127", "P749", "P112"]:
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
                                "source": "Wikidata"
                            })
                        else:
                            sub_owners = extract_owners_wikidata(owner_qid, visited, current_path, depth + 1)
                            owners.extend(sub_owners)
    
    if not owners:
        owners.append({
            "path": current_path,
            "is_human": is_human(entity),
            "source": "Wikidata"
        })
    
    return owners

# ============================================================================
# OPENCORPORATES - Source 2 (améliorée)
# ============================================================================

def search_opencorporates(name: str) -> Optional[Dict]:
    """Cherche sur OpenCorporates avec meilleure extraction"""
    try:
        url = "https://api.opencorporates.com/v0.4/companies/search"
        params = {
            "q": name,
            "format": "json",
            "per_page": 3
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=5)
        r.raise_for_status()
        results = r.json().get("results", {}).get("companies", [])
        if not results:
            return None
        return results[0]
    except Exception as e:
        logger.warning(f"OpenCorporates search error: {e}")
        return None

def extract_owners_opencorporates(name: str) -> List[Dict]:
    """Extrait les propriétaires via OpenCorporates"""
    company = search_opencorporates(name)
    if not company:
        return []
    
    try:
        owners = []
        path = [company.get("name", name)]
        
        # Parent company
        parent = company.get("parent_company_name")
        if parent:
            path.append(parent)
        
        # Officers/directeurs (souvent les propriétaires)
        officers_name = company.get("officer_names", [])
        if officers_name:
            for officer in officers_name[:1]:  # Top 1 officer
                owners.append({
                    "path": path + [officer],
                    "is_human": True,
                    "source": "OpenCorporates"
                })
        
        if not owners:
            owners.append({
                "path": path,
                "is_human": False,
                "source": "OpenCorporates"
            })
        
        return owners
    except Exception as e:
        logger.warning(f"OpenCorporates extract error: {e}")
        return []

# ============================================================================
# WIKIPEDIA - Source 3
# ============================================================================

def search_wikipedia(name: str) -> Optional[str]:
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
        logger.warning(f"Wikipedia search error: {e}")
        return None

def extract_owners_wikipedia(title: str) -> List[Dict]:
    """Scrape l'infobox Wikipedia pour les propriétaires"""
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
        
        # Mots-clés de recherche
        keywords = ['owner', 'founder', 'ceo', 'parent company', 'owned by', 'headquarters']
        
        for row in infobox.find_all('tr'):
            th = row.find('th')
            td = row.find('td')
            
            if not th or not td:
                continue
            
            header = th.get_text(strip=True).lower()
            content = td.get_text(strip=True)
            
            # Extrait les liens (noms) de propriétaires
            if any(kw in header for kw in keywords):
                links = td.find_all('a')
                if links:
                    for link in links[:2]:  # Top 2
                        owner_name = link.get_text(strip=True)
                        if owner_name and len(owner_name) > 2:
                            owners.append({
                                "path": path + [owner_name],
                                "is_human": any(x in header for x in ['owner', 'founder', 'ceo']),
                                "source": "Wikipedia"
                            })
                else:
                    owners.append({
                        "path": path + [content],
                        "is_human": any(x in header for x in ['owner', 'founder']),
                        "source": "Wikipedia"
                    })
                break
        
        if not owners:
            owners.append({
                "path": path,
                "is_human": False,
                "source": "Wikipedia"
            })
        
        return owners
    except Exception as e:
        logger.warning(f"Wikipedia scrape error: {e}")
        return []

# ============================================================================
# YAHOO FINANCE - Source 4 (actionnaires publics)
# ============================================================================

def extract_owners_yahoo(name: str) -> List[Dict]:
    """Cherche les actionnaires majeurs via Yahoo Finance"""
    try:
        # Cherche d'abord le ticker
        search_url = f"https://query1.finance.yahoo.com/v10/finance/autocomplete"
        params = {"query": name}
        r = requests.get(search_url, params=params, headers=HEADERS, timeout=5)
        r.raise_for_status()
        
        results = r.json().get("ResultSet", {}).get("Result", [])
        if not results:
            return []
        
        ticker = results[0].get("symbol")
        if not ticker:
            return []
        
        # Récupère les actionnaires
        holders_url = f"https://finance.yahoo.com/quote/{ticker}/holders"
        r = requests.get(holders_url, headers=HEADERS, timeout=5)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        
        # Cherche le tableau des actionnaires
        tables = soup.find_all('table')
        owners = []
        
        for table in tables:
            rows = table.find_all('tr')
            for row in rows[1:4]:  # Top 3 actionnaires
                cells = row.find_all('td')
                if len(cells) >= 2:
                    holder_name = cells[0].get_text(strip=True)
                    percentage = cells[1].get_text(strip=True)
                    
                    # Filtre > 10%
                    try:
                        pct = float(percentage.replace('%', '').replace(',', ''))
                        if pct > 10:
                            owners.append({
                                "path": [name, holder_name],
                                "is_human": True,
                                "source": f"Yahoo Finance ({percentage})"
                            })
                    except:
                        pass
        
        return owners
    except Exception as e:
        logger.warning(f"Yahoo Finance error: {e}")
        return []

# ============================================================================
# GOOGLE SEARCH - Source 5 (fallback)
# ============================================================================

def extract_owners_google(name: str) -> List[Dict]:
    """Scrape les résultats Google pour trouver les propriétaires"""
    try:
        query = f"{name} owner founder company"
        search_url = "https://www.google.com/search"
        params = {"q": query}
        r = requests.get(search_url, params=params, headers=HEADERS, timeout=5)
        r.raise_for_status()
        
        soup = BeautifulSoup(r.content, 'html.parser')
        
        # Cherche les snippets de réponse
        owners = []
        snippets = soup.find_all('span', {'class': 'st'})
        
        for snippet in snippets[:3]:
            text = snippet.get_text()
            # Pattern simple : "X owns Y" ou "founded by Z"
            if 'founded' in text.lower() or 'owner' in text.lower():
                # Extrait des noms (heuristique)
                words = text.split()
                for i, word in enumerate(words):
                    if word.lower() in ['founded', 'owner', 'owned']:
                        if i + 1 < len(words):
                            owner = ' '.join(words[i+1:i+3])
                            if len(owner) > 3:
                                owners.append({
                                    "path": [name, owner.strip('.')],
                                    "is_human": True,
                                    "source": "Google Search"
                                })
                                break
        
        return owners
    except Exception as e:
        logger.warning(f"Google search error: {e}")
        return []

# ============================================================================
# AGREGATION ET FUSION
# ============================================================================

def find_all_owners(query: str) -> Dict:
    """Cherche les propriétaires sur toutes les sources"""
    results = {
        "query": query,
        "wikidata": [],
        "wikipedia": [],
        "opencorporates": [],
        "yahoo_finance": [],
        "google_search": [],
        "best_result": None,
        "alternatives": []
    }
    
    # Wikidata
    wikidata_qid = search_wikidata_entity(query)
    if wikidata_qid:
        results["wikidata"] = extract_owners_wikidata(wikidata_qid)
    
    # Wikipedia
    wiki_title = search_wikipedia(query)
    if wiki_title:
        results["wikipedia"] = extract_owners_wikipedia(wiki_title)
    
    # OpenCorporates
    results["opencorporates"] = extract_owners_opencorporates(query)
    
    # Yahoo Finance
    results["yahoo_finance"] = extract_owners_yahoo(query)
    
    # Google Search (fallback)
    if not results["opencorporates"] and not results["wikidata"]:
        results["google_search"] = extract_owners_google(query)
    
    # Fusion intelligente
    all_results = []
    for source in ['yahoo_finance', 'opencorporates', 'wikidata', 'wikipedia', 'google_search']:
        for r in results[source]:
            all_results.append(r)
    
    if not all_results:
        return results
    
    # Trie : humain d'abord, puis par longueur du chemin
    all_results.sort(
        key=lambda x: (-x.get("is_human", False), -len(x.get("path", []))),
        reverse=True
    )
    
    # Meilleur résultat
    best = all_results[0]
    results["best_result"] = best
    
    # Alternatives (chemins différents)
    seen_paths = {tuple(best["path"])}
    for data in all_results[1:]:
        path_tuple = tuple(data.get("path", []))
        if path_tuple not in seen_paths and len(results["alternatives"]) < 3:
            results["alternatives"].append(data)
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
