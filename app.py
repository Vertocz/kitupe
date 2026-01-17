from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
from typing import Optional, List, Dict
import logging
import re
import os

app = Flask(__name__)

# Configuration
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# TAVILY - Source principale (recherche web)
# ============================================================================

def search_tavily(query: str) -> Optional[Dict]:
    """Cherche via Tavily API"""
    if not TAVILY_API_KEY:
        logger.error("TAVILY_API_KEY not set")
        return None
    
    try:
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": f"{query} owner founder company",
            "include_answer": True,
            "max_results": 5
        }
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        result = r.json()
        logger.info(f"Tavily found {len(result.get('results', []))} results for '{query}'")
        return result
    except Exception as e:
        logger.error(f"Tavily error: {e}")
        return None

def extract_owner_from_tavily(query: str, tavily_result: Dict) -> Optional[str]:
    """Extrait le propriétaire des résultats Tavily"""
    try:
        # D'abord, regarde la réponse AI
        ai_answer = tavily_result.get("answer", "")
        logger.info(f"Tavily AI answer: {ai_answer[:300] if ai_answer else 'EMPTY'}")
        
        if ai_answer:
            # Patterns pour extraire le propriétaire
            patterns = [
                r"(?:owned by|founder|founded by|CEO|owner is)\s+([A-Z][a-zA-Z\s&\-\.\']+?)(?:\.|,|;|\s+and)",
                r"([A-Z][a-zA-Z\s&\-\.\']+?)\s+(?:founded|owns|owned|is the founder)",
            ]
            
            for pattern in patterns:
                match = re.search(pattern, ai_answer)
                if match:
                    owner = match.group(1).strip().rstrip('.,;')
                    if (len(owner) > 2 and 
                        owner.lower() != query.lower() and
                        not any(x in owner.lower() for x in ['the ', 'company', 'corporation'])):
                        logger.info(f"Found owner from AI answer: {owner}")
                        return owner
        
        # Sinon, regarde les résultats
        results = tavily_result.get("results", [])
        for result in results:
            snippet = result.get("content", "")
            
            # Cherche les patterns dans le snippet
            patterns = [
                r"(?:owned by|founder|founded by)\s+([A-Z][a-zA-Z\s&\-\.\']+?)(?:\.|,|;)",
                r"([A-Z][a-zA-Z\s&\-\.\']+?)\s+(?:founded|owns)",
            ]
            
            for pattern in patterns:
                match = re.search(pattern, snippet)
                if match:
                    owner = match.group(1).strip().rstrip('.,;')
                    if len(owner) > 2 and owner.lower() != query.lower():
                        logger.info(f"Found owner from snippet: {owner}")
                        return owner
        
        logger.warning(f"No owner extracted from Tavily for '{query}'")
        return None
    except Exception as e:
        logger.error(f"Extract from Tavily error: {e}")
        return None

# ============================================================================
# WIKIDATA - Vérification
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

def get_wikidata_images(qid: str) -> Dict:
    """Récupère les images associées à une entité Wikidata"""
    try:
        entity = get_wikidata_entity(qid)
        if not entity:
            return {"logo": None, "image": None}
        
        claims = entity.get("claims", {})
        images = {
            "logo": None,
            "image": None
        }
        
        # P154 = logo
        if "P154" in claims:
            for claim in claims["P154"]:
                mainsnak = claim.get("mainsnak", {})
                datavalue = mainsnak.get("datavalue", {})
                if datavalue.get("type") == "string":
                    filename = datavalue.get("value", "")
                    if filename:
                        images["logo"] = f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename}?width=200"
                        break
        
        # P18 = image
        if "P18" in claims:
            for claim in claims["P18"]:
                mainsnak = claim.get("mainsnak", {})
                datavalue = mainsnak.get("datavalue", {})
                if datavalue.get("type") == "string":
                    filename = datavalue.get("value", "")
                    if filename:
                        images["image"] = f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename}?width=200"
                        break
        
        return images
    except Exception as e:
        logger.warning(f"Get Wikidata images error: {e}")
        return {"logo": None, "image": None}
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

def verify_with_wikidata(company_name: str) -> Optional[Dict]:
    """Vérifie et enrichit avec Wikidata"""
    try:
        company_qid = search_wikidata_entity(company_name)
        if not company_qid:
            return None
        
        company = get_wikidata_entity(company_qid)
        if not company:
            return None
        
        company_label = get_label(company)
        path = [company_label]
        claims = company.get("claims", {})
        
        # Cherche propriétaires/fondateurs
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
                            is_owner_human = is_human(owner_entity)
                            
                            return {
                                "path": path + [owner_label],
                                "is_human": is_owner_human,
                                "source": "Wikidata (vérification)",
                                "verified": True
                            }
        
        return {
            "path": path,
            "is_human": False,
            "source": "Wikidata (vérification)",
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
                        "source": "Wikipedia (vérification)",
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
    """Pipeline : Tavily cherche, Wikidata/Wikipedia vérifient"""
    results = {
        "query": query,
        "primary_result": None,
        "verification": [],
        "best_result": None,
        "images": {
            "company_logo": None,
            "owner_photo": None
        }
    }
    
    # 1. Tavily cherche
    tavily_result = search_tavily(query)
    if not tavily_result:
        return results
    
    owner_name = extract_owner_from_tavily(query, tavily_result)
    if not owner_name:
        return results
    
    # Résultat primaire de Tavily
    results["primary_result"] = {
        "path": [query, owner_name],
        "is_human": True,
        "source": "Recherche"
    }
    
    # 2. Cherche les images de la compagnie
    company_qid = search_wikidata_entity(query)
    if company_qid:
        company_images = get_wikidata_images(company_qid)
        results["images"]["company_logo"] = company_images.get("logo")
        
        # Vérifie avec Wikidata
        wikidata_verification = verify_with_wikidata(query)
        if wikidata_verification:
            results["verification"].append(wikidata_verification)
            
            # Cherche la photo du propriétaire
            owner_qid = search_wikidata_entity(owner_name)
            if owner_qid:
                owner_images = get_wikidata_images(owner_qid)
                results["images"]["owner_photo"] = owner_images.get("image")
    
    # 3. Vérifie avec Wikipedia
    wikipedia_verification = verify_with_wikipedia(query)
    if wikipedia_verification:
        results["verification"].append(wikipedia_verification)
    
    # Détermine le meilleur résultat
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
    
    if not TAVILY_API_KEY:
        return jsonify({"error": "API not configured. Please set TAVILY_API_KEY environment variable."}), 500
    
    results = find_owner(query)
    return jsonify(results)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
