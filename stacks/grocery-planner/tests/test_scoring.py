from grocery.scoring import bayesian_score, evaluate_candidate


def test_bayesian_pulls_low_count_toward_prior(cfg):
    m, C = cfg["scoring"]["prior_m"], cfg["scoring"]["prior_weight_C"]
    high_few = bayesian_score(5.0, 3, m, C)      # 5.0 with 3 reviews
    good_many = bayesian_score(4.7, 3000, m, C)  # 4.7 with 3000 reviews
    assert good_many > high_few                  # the point of the Bayesian blend
    assert bayesian_score(None, 10, m, C) is None


def test_verdict_good_and_acceptable(cfg):
    good = evaluate_candidate(
        {"site": "X", "rating": 4.9, "count": 500, "total_time_min": 20,
         "ingredients": ["chicken breast", "olive oil"]}, "chicken", cfg)
    assert good.verdict == "good"
    accept = evaluate_candidate(
        {"site": "X", "rating": 4.4, "count": 30, "total_time_min": 20,
         "ingredients": ["salmon", "olive oil"]}, "fish", cfg)
    assert accept.verdict == "acceptable"


def test_reject_beans_and_missing_rating(cfg):
    beans = evaluate_candidate(
        {"site": "X", "rating": 4.9, "count": 900, "total_time_min": 20,
         "has_beans_lentils": True, "ingredients": ["lentils"]}, "lunch", cfg)
    assert beans.verdict == "rejected" and "beans" in beans.reject_reasons[0]
    unrated = evaluate_candidate(
        {"site": "X", "rating": None, "count": None, "total_time_min": 20,
         "ingredients": ["salmon"]}, "fish", cfg)
    assert unrated.verdict == "rejected"


def test_chicken_breast_hard_filter(cfg):
    thigh = evaluate_candidate(
        {"site": "X", "rating": 4.9, "count": 900, "total_time_min": 20,
         "uses_chicken_breast": False, "ingredients": ["chicken thighs"]}, "chicken", cfg)
    assert thigh.verdict == "rejected"
    thigh_ok = evaluate_candidate(
        {"site": "X", "rating": 4.9, "count": 900, "total_time_min": 20,
         "uses_chicken_breast": False, "breast_ok": True,
         "ingredients": ["chicken thighs"]}, "chicken", cfg)
    assert thigh_ok.verdict == "good"
    assert any("breast" in f for f in thigh_ok.flags)


def test_time_cap_hard_and_flag(cfg):
    over = evaluate_candidate(
        {"site": "X", "rating": 4.9, "count": 900, "total_time_min": 200,
         "ingredients": ["salmon"]}, "fish", cfg)
    assert over.verdict == "rejected"
    passive = evaluate_candidate(
        {"site": "X", "rating": 4.9, "count": 900, "total_time_min": 40,
         "ingredients": ["salmon"]}, "fish", cfg)   # 40 < 45 hard cap, > 30 active
    assert passive.verdict == "good"
    assert any("active cap" in f for f in passive.flags)


def test_non_olive_fat_is_a_flag_not_reject(cfg):
    ev = evaluate_candidate(
        {"site": "X", "rating": 4.9, "count": 900, "total_time_min": 20,
         "non_olive_fats": ["butter"], "ingredients": ["salmon", "butter"]}, "fish", cfg)
    assert ev.verdict == "good"
    assert any("olive oil" in f for f in ev.flags)


def test_ingredient_cap_excludes_pantry_staples(cfg):
    # 10 lines but 4 are pantry staples -> 6 counted, under the cap of 8
    ev = evaluate_candidate(
        {"site": "X", "rating": 4.9, "count": 900, "total_time_min": 20,
         "ingredients": ["salt", "pepper", "olive oil", "water",
                         "salmon", "lemon", "dill", "garlic", "shallot", "capers"]},
        "fish", cfg)
    assert ev.verdict == "good"
