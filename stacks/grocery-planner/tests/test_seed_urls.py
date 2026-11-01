from grocery.seed import dedupe_plan, url_key


def test_url_key_ignores_www_scheme_and_trailing_slash():
    a = "https://www.downshiftology.com/recipes/greek-chicken-souvlaki/"
    b = "http://downshiftology.com/recipes/greek-chicken-souvlaki"
    assert url_key(a) == url_key(b)
    assert url_key(a) != url_key("https://downshiftology.com/recipes/steak-fajitas/")


def test_dedupe_groups_www_variants():
    recipes = [
        {"id": 237, "source_url": "https://downshiftology.com/recipes/greek-chicken-souvlaki/"},
        {"id": 83, "source_url": "https://www.downshiftology.com/recipes/greek-chicken-souvlaki/"},
        {"id": 5, "source_url": ""},
    ]
    plan = dedupe_plan(recipes)
    assert [(k["id"], [d["id"] for d in ds]) for k, ds in plan] == [(83, [237])]
