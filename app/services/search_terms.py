from __future__ import annotations

import re


GERMAN_FASHION_TERMS = {
    "damen": "women womens",
    "frau": "women",
    "kleid": "dress",
    "maxikleid": "maxi dress",
    "maxi kleid": "maxi dress",
    "jumpsuit": "jumpsuit romper",
    "hosenanzug": "pantsuit suit",
    "blazer": "blazer jacket",
    "schuhe": "shoes",
    "sneakers": "sneakers shoes",
    "jacke": "jacket",
    "mantel": "coat",
    "hose": "pants trousers",
    "elegant": "elegant",
    "eleganter": "elegant",
    "figurbetont": "bodycon fitted",
    "schlicht": "minimal simple",
    "langarm": "long sleeve",
    "langärmlig": "long sleeve",
    "trägerlos": "strapless",
    "beinschlitz": "side slit",
    "weit": "wide leg",
}


def zendrop_search_text(title: str) -> str:
    normalized = normalize_search_text(title)
    additions = [
        english_terms
        for german_term, english_terms in GERMAN_FASHION_TERMS.items()
        if german_term in normalized
    ]
    if not additions:
        return title
    return f"{title} {' '.join(additions)}"


def zendrop_search_queries(title: str, limit: int = 5) -> list[str]:
    normalized = normalize_search_text(zendrop_search_text(title))
    queries: list[str] = []
    category = detect_category(normalized)
    attributes = detect_attributes(normalized)
    if category:
        queries.append(" ".join(["women", *attributes, category]).strip())
        queries.append(f"women {category}")
    queries.append(zendrop_search_text(title))
    if "maxi" in normalized and category == "dress":
        queries.append("women maxi dress")
    if "orthopedic" in normalized and category in {"shoes", "sandals", "sneakers"}:
        queries.append(f"women orthopedic {category}")
    if "v neck" in normalized and category in {"blouse", "top"}:
        queries.append(f"women v neck {category}")
    return unique_queries(queries)[:limit]


def normalize_search_text(value: str) -> str:
    normalized = value.lower().replace("|", " ")
    for german_term, english_terms in GERMAN_FASHION_TERMS.items():
        if german_term in normalized:
            normalized = f"{normalized} {english_terms}"
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", normalized)).strip()


def detect_category(normalized: str) -> str | None:
    words = set(normalized.split())
    if "dress" in words or "dresses" in words or "gown" in words:
        return "dress"
    if "sandal" in words or "sandals" in words:
        return "sandals"
    if "sneaker" in words or "sneakers" in words:
        return "sneakers"
    if "shoe" in words or "shoes" in words:
        return "shoes"
    if "blouse" in words:
        return "blouse"
    if "top" in words:
        return "top"
    if "pants" in words or "trousers" in words:
        return "pants"
    if "swimsuit" in words or "swimwear" in words or "bikini" in words:
        return "swimsuit"
    if "jumpsuit" in words or "romper" in words:
        return "jumpsuit"
    return None


def detect_attributes(normalized: str) -> list[str]:
    attributes = []
    phrases = [
        "maxi",
        "midi",
        "strapless",
        "halter",
        "v neck",
        "ruffle",
        "wide leg",
        "tummy control",
        "orthopedic",
        "slip on",
        "side slit",
    ]
    for phrase in phrases:
        if phrase in normalized:
            attributes.extend(phrase.split())
    return unique_queries(attributes)


def unique_queries(queries: list[str]) -> list[str]:
    result = []
    seen = set()
    for query in queries:
        clean_query = re.sub(r"\s+", " ", query).strip()
        if clean_query and clean_query not in seen:
            result.append(clean_query)
            seen.add(clean_query)
    return result
