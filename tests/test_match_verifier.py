from app.tools import syllabus_tools
from app.tools.match_verifier import quote_in_text, verify_matches, _norm

OLD = "Week 1: Propositional logic, logical operators.\nWeek 2: Sets and\nOperations; functions.\nWeek 3: Basic counting techniques."
NEW = "Topics: Fundamentals of logic and truth tables. Sets, functions. Graph theory: paths, Euler and Hamilton graphs."


def _analysis(matches):
    return {"old": {"title": "O", "credit_hours": 3, "topics": ["logic", "sets", "counting"], "learning_outcomes": ["o"], "assessment": {}},
            "new": {"title": "N", "credit_hours": 3, "topics": ["logic", "sets", "graphs"], "learning_outcomes": ["o"], "assessment": {}},
            "topic_matches": matches, "outcome_matches": []}


def test_quote_found_even_across_line_breaks_and_punctuation():
    assert quote_in_text("Sets and Operations; functions.", _norm(OLD))
    assert not quote_in_text("Graph theory and trees", _norm(OLD))


def test_short_or_empty_quotes_are_not_accepted():
    assert not quote_in_text("sets", _norm(OLD))
    assert not quote_in_text("", _norm(OLD))


def test_short_genuine_topic_names_are_accepted():
    assert quote_in_text("Functions", _norm("Week 7 Functions Chapter 2-3"))
    assert quote_in_text("Relations", _norm("Week 12 Relations Chapter 3-4"))


def test_slightly_reworded_quote_with_the_same_content_words_is_accepted():
    hay = _norm("Week 14 Induction, recursion; and relations, Chapter 4-1")
    assert quote_in_text("Induction and recursion", hay)
    assert not quote_in_text("Graph theory and trees", hay)


def test_dropped_matches_are_listed_with_the_quote_that_failed():
    fake = {"new_index": 2, "old_index": 2, "strength": "full", "old_quote": "Graph theory including Euler paths", "new_quote": "Graph theory: paths"}
    out = verify_matches(_analysis([fake]), OLD, NEW)
    d = out["verification"]["dropped_matches"]
    assert d[0]["requested_item"] == "graphs" and "Graph theory" in d[0]["quote_tried"]


def test_invented_match_is_dropped_and_real_one_kept():
    good = {"new_index": 0, "old_index": 0, "strength": "full", "old_quote": "Propositional logic, logical operators", "new_quote": "Fundamentals of logic and truth tables"}
    fake = {"new_index": 2, "old_index": 2, "strength": "full", "old_quote": "Graph theory including Euler and Hamilton paths", "new_quote": "Graph theory: paths, Euler and Hamilton graphs"}
    out = verify_matches(_analysis([good, fake]), OLD, NEW)
    assert [m["new_index"] for m in out["topic_matches"]] == [0]
    v = out["verification"]
    assert v["claimed_total"] == 2 and v["dropped_total"] == 1 and v["dropped_percent"] == 50.0
    assert v["verified_matches"][0]["quote_from_completed_syllabus"] == "Propositional logic, logical operators"


def test_invented_match_cannot_raise_the_score():
    fake = {"new_index": 2, "old_index": 2, "strength": "full", "old_quote": "Graph theory including Euler and Hamilton paths", "new_quote": "Graph theory: paths"}
    from app.tools.policy import load_equivalency_policy
    policy = load_equivalency_policy()
    sim = syllabus_tools.compute_similarity(verify_matches(_analysis([fake]), OLD, NEW), 3, 3, policy)
    assert sim["components_percent"]["topics"] == 0.0 and sim["verification_ok"] is False


def test_missing_quotes_drop_every_match():
    bare = {"new_index": 0, "old_index": 0, "strength": "full", "old_quote": "", "new_quote": ""}
    out = verify_matches(_analysis([bare]), OLD, NEW)
    assert out["topic_matches"] == [] and out["verification"]["dropped_percent"] == 100.0


def test_out_of_range_indexes_are_dropped():
    m = {"new_index": 9, "old_index": 0, "strength": "full", "old_quote": "Propositional logic, logical operators", "new_quote": "Fundamentals of logic and truth tables"}
    assert verify_matches(_analysis([m]), OLD, NEW)["topic_matches"] == []


def test_approval_is_withheld_when_many_matches_are_unverified():
    from app import agent
    ev = {"constraints": {"minimum_grade": {"meets": True, "grade": "A", "required": "C+"}, "study_plan": {"in_plan": True},
                          "credit_hours": {"old_covers_new": True, "old": 3, "new": 3},
                          "transfer_limit": {"within_limit": True}},
          "similarity": {"score_band": "EQUIVALENT_RANGE", "verification_ok": False}}
    g = {"status": "ok", "error": None, "recommendation": "APPROVE", "reasoning": "Looks equivalent."}
    out = agent._apply_guardrails(ev, g)
    assert out["recommendation"] == "MANUAL_REVIEW" and "could not be confirmed" in out["reasoning"]
    ev["similarity"]["verification_ok"] = True
    g = {"status": "ok", "error": None, "recommendation": "APPROVE", "reasoning": "Looks equivalent."}
    assert agent._apply_guardrails(ev, g)["recommendation"] == "APPROVE"