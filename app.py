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

def search_tavily(query: str, search_type: str = "owner") -> Optional[Dict]:
    """Cherche via Tavily API avec différents types de recherche"""
    if not TAVILY_API_KEY:
        logger.error("TAVILY_API_KEY not set")
        return None
    
    try:
        url = "https://api.tavily.com/search"
        
        # Adapte la query selon le type
        if search_type == "owner":
            search_query = f'"{query}" "owned by" OR "parent company" OR "subsidiary of" -founder -CEO'
        elif search_type == "structure":
            search_query = f'"{query}" ownership structure shareholders'
        else:
            search_query = query
            
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": search_query,
            "include_answer": True,
            "max_results": 8,
            "search_depth": "advanced"  # Pour plus de détails
        }
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        result = r.json()
        logger.info(f"Tavily ({search_type}) found {len(result.get('results', []))} results for '{query}'")
        return result
    except Exception as e:
        logger.error(f"Tavily error: {e}")
        return None

def extract_owner_from_tavily(query: str, tavily_result: Dict) -> Dict:
    """Extrait le propriétaire avec contexte"""
    try:
        ai_answer = tavily_result.get("answer", "")
        results = tavily_result.get("results", [])
        
        logger.info(f"Tavily AI answer: {ai_answer[:500] if ai_answer else 'EMPTY'}")
        
        # Patterns améliorés pour VRAIS propriétaires
        ownership_patterns = [
            # Structures de propriété
            (r"(?:is )?(?:a )?(?:wholly[- ]owned )?subsidiary (?:of|by)\s+([A-Z][a-zA-Z\s&\-\.\']+?)(?:\.|,|;|\s+and|\s+which)", "subsidiary"),
            (r"(?:is )?owned by\s+([A-Z][a-zA-Z\s&\-\.\']+?)(?:\.|,|;|\s+and)", "owned"),
            (r"(?:parent company|holding company)(?:\s+is)?\s+([A-Z][a-zA-Z\s&\-\.\']+?)(?:\.|,|;)", "parent"),
            (r"([A-Z][a-zA-Z\s&\-\.\']+?)\s+(?:owns|acquired|purchased)\s+" + re.escape(query), "owns"),
            
            # Entreprises privées
            (r"(?:private company|privately held).*?(?:by|owned by)\s+([A-Z][a-zA-Z\s&\-\.\']+?)(?:\.|,|;)", "private"),
            (r"([A-Z][a-zA-Z\s&\-\.\']+?)\s+(?:is|are) the (?:sole )?owner", "sole_owner"),
            
            # Marques/divisions
            (r"(?:brand|division|unit) of\s+([A-Z][a-zA-Z\s&\-\.\']+?)(?:\.|,|;)", "brand_of"),
        ]
        
        # Patterns à ÉVITER (fondateurs, ex-CEOs, etc.)
        exclude_patterns = [
            r"(?:founded|co-founded|started) (?:by|in)",
            r"(?:former|ex[-\s]?)(?:CEO|owner|founder)",
            r"(?:late|deceased)",
            r"(?:originally|initially) (?:founded|owned)",
        ]
        
        # Indicateurs d'entreprise publique
        public_company_indicators = [
            "publicly traded", "public company", "publicly held",
            "stock exchange", "NYSE", "NASDAQ", "listed on",
            "ticker symbol", "shareholders"
        ]
        
        # Vérifie si c'est une entreprise publique
        full_text = ai_answer + " ".join([r.get("content", "") for r in results])
        is_public = any(indicator in full_text.lower() for indicator in public_company_indicators)
        
        if is_public:
            logger.info(f"{query} appears to be a publicly traded company")
            return {
                "owner": None,
                "ownership_type": "public",
                "confidence": "high",
                "reason": "Publicly traded company with dispersed ownership"
            }
        
        # Cherche dans la réponse AI d'abord
        sources_to_check = [(ai_answer, "AI answer")]
        for result in results[:5]:
            sources_to_check.append((result.get("content", ""), result.get("url", "")))
        
        best_match = None
        best_confidence = 0
        
        for text, source in sources_to_check:
            # Skip si contient des patterns à exclure
            if any(re.search(pattern, text, re.IGNORECASE) for pattern in exclude_patterns):
                continue
            
            for pattern, match_type in ownership_patterns:
                matches = re.finditer(pattern, text, re.IGNORECASE)
                for match in matches:
                    owner = match.group(1).strip().rstrip('.,;')
                    
                    # Validation du nom
                    if (len(owner) < 3 or 
                        owner.lower() == query.lower() or
                        any(x in owner.lower() for x in ['the company', 'this company', 'it', 'they'])):
                        continue
                    
                    # Calcule la confiance
                    confidence = 0.5
                    if match_type in ["subsidiary", "owned", "parent"]:
                        confidence = 0.9
                    elif match_type in ["owns", "sole_owner", "private"]:
                        confidence = 0.8
                    elif match_type == "brand_of":
                        confidence = 0.7
                    
                    if source == "AI answer":
                        confidence += 0.1
                    
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_match = {
                            "owner": owner,
                            "ownership_type": match_type,
                            "confidence": "high" if confidence > 0.8 else "medium",
                            "source": source if source != "AI answer" else "Tavily AI"
                        }
                        logger.info(f"Found owner '{owner}' (type: {match_type}, confidence: {confidence:.2f})")
        
        if best_match:
            return best_match
        
        logger.warning(f"No clear owner found for '{query}'")
        return {
            "owner": None,
            "ownership_type": "unknown",
            "confidence": "low",
            "reason": "Could not determine ownership structure"
        }
        
    except Exception as e:
        logger.error(f"Extract from Tavily error: {e}")
        return {
            "owner": None,
            "ownership_type": "error",
            "confidence": "low",
            "reason": str(e)
        }

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
            "format": "json",
            "limit": 3
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=5)
        r.raise_for_status()
        results = r.json().get("search", [])
        
        # Filtre pour trouver la meilleure correspondance
        for result in results:
            description = result.get("description", "").lower()
            # Privilégie les entreprises/organisations
            if any(term in description for term in ["company", "corporation", "business", "brand", "organization"]):
                return result["id"]
        
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

def verify_with_wikidata(company_name: str) -> Optional[Dict]:
    """Vérifie et enrichit avec Wikidata - FOCUS sur propriétaire actuel"""
    try:
        company_qid = search_wikidata_entity(company_name)
        if not company_qid:
            return None
        
        company = get_wikidata_entity(company_qid)
        if not company:
            return None
        
        company_label = get_label(company)
        claims = company.get("claims", {})
        
        # ORDRE DE PRIORITÉ pour propriétaire actuel:
        # 1. P749 - parent organization (le plus fiable)
        # 2. P127 - owned by
        # 3. P112 - founder (seulement si toujours propriétaire)
        
        priority_props = [
            ("P749", "parent_company"),
            ("P127", "owned_by"),
        ]
        
        for prop, prop_type in priority_props:
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
                                "path": [company_label, owner_label],
                                "is_human": is_owner_human,
                                "ownership_type": prop_type,
                                "source": "Wikidata",
                                "verified": True,
                                "qid": owner_qid
                            }
        
        # Vérifie si c'est une entreprise publique
        if "P414" in claims:  # stock exchange
            return {
                "path": [company_label],
                "is_human": False,
                "ownership_type": "public",
                "source": "Wikidata",
                "verified": True,
                "note": "Publicly traded company"
            }
        
        return None
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
    """Vérifie avec Wikipedia - FOCUS sur parent/owner actuel"""
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
        
        # Mots-clés PRIORISÉS (évite founder sauf si aussi owner)
        priority_keywords = {
            'parent': ('parent', 'high'),
            'owner': ('owner', 'high'),
            'subsidiary of': ('subsidiary', 'high'),
            'owned by': ('owned', 'high'),
            'founder': ('founder', 'low'),  # Basse priorité
        }
        
        best_match = None
        best_priority = 'low'
        
        for row in infobox.find_all('tr'):
            th = row.find('th')
            td = row.find('td')
            
            if not th or not td:
                continue
            
            header = th.get_text(strip=True).lower()
            
            for keyword, (match_type, priority) in priority_keywords.items():
                if keyword in header:
                    # Skip founders à moins qu'il n'y ait "owner" aussi dans le header
                    if keyword == 'founder' and 'owner' not in header:
                        continue
                    
                    links = td.find_all('a')
                    if links:
                        owner_name = links[0].get_text(strip=True)
                        
                        # Priorité haute gagne toujours
                        if priority == 'high' or best_priority != 'high':
                            is_owner_human = keyword in ['owner', 'founder']
                            best_match = {
                                "path": [wiki_title, owner_name],
                                "is_human": is_owner_human,
                                "ownership_type": match_type,
                                "source": "Wikipedia",
                                "verified": True
                            }
                            best_priority = priority
                            
                            if priority == 'high':
                                return best_match  # Retourne immédiatement si haute priorité
        
        return best_match
    except Exception as e:
        logger.warning(f"Wikipedia verification error: {e}")
        return None

# ============================================================================
# PIPELINE PRINCIPAL
# ============================================================================

def find_owner(query: str) -> Dict:
    """Pipeline amélioré : focus sur propriétaire ACTUEL"""
    results = {
        "query": query,
        "primary_result": None,
        "verification": [],
        "best_result": None,
        "images": {
            "company_logo": None,
            "owner_photo": None
        },
        "ownership_chain": []
    }
    
    # 1. Recherche principale avec Tavily
    tavily_result = search_tavily(query, "owner")
    if not tavily_result:
        return results
    
    owner_info = extract_owner_from_tavily(query, tavily_result)
    
    # Résultat primaire
    if owner_info.get("owner"):
        results["primary_result"] = {
            "path": [query, owner_info["owner"]],
            "is_human": False,  # Par défaut, assume une entreprise
            "ownership_type": owner_info.get("ownership_type"),
            "confidence": owner_info.get("confidence"),
            "source": "Tavily Search"
        }
    elif owner_info.get("ownership_type") == "public":
        results["primary_result"] = {
            "path": [query],
            "is_human": False,
            "ownership_type": "public",
            "confidence": "high",
            "source": "Tavily Search",
            "note": owner_info.get("reason")
        }
    
    # 2. Images de la compagnie
    company_qid = search_wikidata_entity(query)
    if company_qid:
        company_images = get_wikidata_images(company_qid)
        results["images"]["company_logo"] = company_images.get("logo")
    
    # 3. Vérifications
    wikidata_verification = verify_with_wikidata(query)
    if wikidata_verification:
        results["verification"].append(wikidata_verification)
        
        # Photo du propriétaire si trouvé
        if owner_info.get("owner"):
            owner_qid = search_wikidata_entity(owner_info["owner"])
            if owner_qid:
                owner_images = get_wikidata_images(owner_qid)
                results["images"]["owner_photo"] = owner_images.get("image") or owner_images.get("logo")
    
    wikipedia_verification = verify_with_wikipedia(query)
    if wikipedia_verification:
        results["verification"].append(wikipedia_verification)
    
    # 4. Détermine le meilleur résultat
    all_candidates = []
    if results["primary_result"]:
        all_candidates.append(results["primary_result"])
    all_candidates.extend(results["verification"])
    
    # Trie par : vérification > confiance > type de propriété
    def score_result(r):
        score = 0
        if r.get("verified"):
            score += 100
        if r.get("confidence") == "high":
            score += 50
        elif r.get("confidence") == "medium":
            score += 25
        
        ownership_type = r.get("ownership_type", "")
        if ownership_type in ["parent_company", "owned_by", "subsidiary"]:
            score += 30
        elif ownership_type == "public":
            score += 20
        elif ownership_type in ["owns", "private"]:
            score += 15
        
        return score
    
    all_candidates.sort(key=score_result, reverse=True)
    results["best_result"] = all_candidates[0] if all_candidates else None
    
    # 5. Construit la chaîne de propriété si possible
    if results["best_result"] and len(results["best_result"].get("path", [])) > 1:
        results["ownership_chain"] = results["best_result"]["path"]
    
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
