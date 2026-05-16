from __future__ import annotations


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
    "beinschlitz": "slit",
    "weit": "wide leg",
}


def zendrop_search_text(title: str) -> str:
    normalized = title.lower().replace("|", " ")
    additions = [
        english_terms
        for german_term, english_terms in GERMAN_FASHION_TERMS.items()
        if german_term in normalized
    ]
    if not additions:
        return title
    return f"{title} {' '.join(additions)}"
