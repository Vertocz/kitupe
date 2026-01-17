from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
import json
from typing import Optional, List, Dict
import logging
import re

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# DUCKDUCKGO - Source principale
# ============================================================================

def search_duckduckgo(query: str) -> Optional[Dict]:
    """Cherche via DuckDuckGo API et retourne un résultat structuré"""
    try:
        url = "https://api.duckduckgo.com/"
        params = {
            "q": f"{query} owner founder CEO",
            "format": "json"
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"DuckDuckGo error: {e}")
        return None

def extract_owner_from_duckduckgo(query: str, ddg_result: Dict) -> Optional[str]:
    """Extrait le nom du propriétaire des résultats DuckDuckGo"""
    try:
        # D'abord, regarde l'abstract
        abstract = ddg_result.get("AbstractText", "")
        logger.info(f"DuckDuckGo abstract for '{query}': {abstract[:200] if abstract else 'EMPTY'}")
        
        if abstract:
            # Patterns plus flexibles
            patterns = [
                r"(?:owned by|founder|founded by|CEO|is owned by|parent company)\s+([A-Z][a-zA-Z\s&\-\.]+?)(?:\.|,|;|$)",
                r"([A-Z][a-zA-Z\s&\-\.]+?)\s+(?:founded|owns|owned|founder)",
            ]
            
            for pattern in patterns:
                matches = re.finditer(pattern, abstract)
                for match in matches:
                    owner = match.group(1).strip().rstrip('.,;')
                    if len(owner) > 2 and owner.lower() != query.lower() and not any(x in owner.lower() for x in ['the', 'and is', 'was']):
                        logger.info(f"Found owner from pattern: {owner}")
                        return owner
        
        # Cherche dans Infobox (si disponible)
        infobox = ddg_result.get("Infobox", "")
        if infobox:
            logger.info(f"DuckDuckGo infobox: {infobox}")
            # Essaie d'extraire depuis l'infobox
            if "founded" in infobox.lower() or "owner" in infobox.lower():
                words = infobox.split('|')
                for word in words:
                    if any(kw in word.lower() for kw in ['founder', 'owner', 'ceo']):
                        potential_owner = word.split(':')[-1].strip()
                        if potential_owner and len(potential_owner) > 2:
                            logger.info(f"Found owner from infobox: {potential_owner}")
                            return potential_owner
        
        # Regarde aussi les Definition si c'est structuré
        definition = ddg_result.get("Definition", "")
        if definition and len(definition) > 10:
            logger.info(f"DuckDuckGo definition: {definition}")
        
        logger.warning(f"No owner extracted from DuckDuckGo for '{query}'")
        return None
    except Exception as e:
        logger.error(f"Extract from DuckDuckGo error: {e}")
        return None

# ============================================================================
# WIKIDATA - Vérification et enrichissement
# ============================================================================

def search_wikidata_entity(name: str) -> Optional[str]:
    """Cherche le QID d'une entité"""
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
        return results[0]["id"] if results else None
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
    """Vérifie si c'est une personne"""
    claims = entity.get("claims", {})
    instance_of = claims.get("P31", [])
    for claim in instance_of:
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})
        if datavalue.get("type") == "wikibase-entityid":
            if datavalue["value"]["id"] == "Q5":
                return True
    return False

def verify_with_wikidata(company_name: str, owner_name: str) -> Optional[Dict]:
    """Vérifie et enrichit les données avec Wikidata"""
    try:
        # Cherche la compagnie
        company_qid = search_wikidata_entity(company_name)
        if not company_qid:
            return None
        
        company = get_wikidata_entity(company_qid)
        if not company:
            return None
        
        company_label = get_label(company)
        path = [company_label]
        
        # Cherche les propriétaires/fondateurs
        claims = company.get("claims", {})
        
        for prop in ["P127", "P749", "P112"]:  # propriétaire, org mère, fondateur
            if prop in claims:
                for claim in claims[prop]:
                    mainsnak = claim.get("mainsnak", {})
                    datavalue = mainsnak.get("datavalue", {})
                    if datavalue.get("type") == "wikibase-entityid":
                        owner_qid = datavalue["value"]["id"]
                        owner_entity = get_wikidata_entity(owner_qid)
                        if owner_entity:
                            owner_label = get_label(owner_entity)
                            is_owner_human = is_human(owner_entity)
                            
                            return {
                                "path": path + [owner_label],
                                "is_human": is_owner_human,
                                "source": "Wikidata (verified)",
                                "verified": True
                            }
        
        return {
            "path": path,
            "is_human": False,
            "source": "Wikidata (verified)",
            "verified": True
        }
    except Exception as e:
        logger.warning(f"Wikidata verification error: {e}")
        return None

# ============================================================================
# WIKIPEDIA - Vérification
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
        return results[0]["title"] if results else None
    except Exception as e:
        logger.warning(f"Wikipedia search error: {e}")
        return None

def verify_with_wikipedia(company_name: str) -> Optional[Dict]:
    """Vérifie avec Wikipedia"""
    try:
        wiki_title = search_wikipedia(company_name)
        if not wiki_title:
            return None
        
        url = f"https://en.wikipedia.org/wiki/{wiki_title.replace(' ', '_')}"
        r = requests.get(url, headers=HEADERS, timeout=5)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        
        infobox = soup.find('table', {'class': 'infobox'})
        if not infobox:
            return None
        
        keywords = ['owner', 'founder', 'ceo', 'parent company']
        
        for row in infobox.find_all('tr'):
            th = row.find('th')
            td = row.find('td')
            
            if not th or not td:
                continue
            
            header = th.get_text(strip=True).lower()
            
            if any(kw in header for kw in keywords):
                links = td.find_all('a')
                if links:
                    owner_name = links[0].get_text(strip=True)
                    is_owner_human = any(x in header for x in ['owner', 'founder', 'ceo'])
                    
                    return {
                        "path": [wiki_title, owner_name],
                        "is_human": is_owner_human,
                        "source": "Wikipedia (verified)",
                        "verified": True
                    }
        
        return None
    except Exception as e:
        logger.warning(f"Wikipedia verification error: {e}")
        return None

# ============================================================================
# PIPELINE PRINCIPAL
# ============================================================================

def find_owner(query: str) -> Dict:
    """Pipeline : DuckDuckGo cherche, Wikidata/Wikipedia vérifient"""
    results = {
        "query": query,
        "primary_result": None,
        "verification": [],
        "best_result": None
    }
    
    # 1. DuckDuckGo cherche
    ddg_result = search_duckduckgo(query)
    if not ddg_result:
        return results
    
    owner_name = extract_owner_from_duckduckgo(query, ddg_result)
    if not owner_name:
        return results
    
    # Résultat primaire de DuckDuckGo
    results["primary_result"] = {
        "path": [query, owner_name],
        "is_human": True,
        "source": "DuckDuckGo"
    }
    
    # 2. Vérifie avec Wikidata
    wikidata_verification = verify_with_wikidata(query, owner_name)
    if wikidata_verification:
        results["verification"].append(wikidata_verification)
    
    # 3. Vérifie avec Wikipedia
    wikipedia_verification = verify_with_wikipedia(query)
    if wikipedia_verification:
        results["verification"].append(wikipedia_verification)
    
    # Détermine le meilleur résultat
    # Priorise les résultats vérifiés (avec humain trouvé)
    all_candidates = [results["primary_result"]] + results["verification"]
    all_candidates.sort(
        key=lambda x: (-x.get("is_human", False), -len(x.get("path", [])))
    )
    
    results["best_result"] = all_candidates[0] if all_candidates else None
    
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
    
    results = find_owner(query)
    return jsonify(results)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
