from app.services.search_terms import zendrop_search_queries


def test_zendrop_search_queries_expand_source_title_into_buyable_terms():
    queries = zendrop_search_queries("Women's Maxi Dress | Strapless Cut & Flowing Silhouette")

    assert queries[:3] == [
        "women maxi strapless dress",
        "women dress",
        "Women's Maxi Dress | Strapless Cut & Flowing Silhouette",
    ]


def test_zendrop_search_queries_translate_german_fashion_terms():
    queries = zendrop_search_queries("Maxikleid Damen | Trägerloser Schnitt & Beinschlitz")

    assert "women maxi strapless side slit dress" in queries
    assert "women maxi dress" in queries
