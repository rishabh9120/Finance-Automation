"""
Unit tests for logic.load_category_rules / suggest_category /
extract_rule_keyword / apply_category_rules / map_splitwise_category
(item #1: wiring up the rules engine for real).
"""
import pandas as pd
import pytest
from logic import (
    load_category_rules,
    suggest_category,
    extract_rule_keyword,
    apply_category_rules,
    map_splitwise_category,
)


class TestLoadCategoryRules:

    def test_loads_seeded_starter_rules(self, isolated_db):
        rules = load_category_rules()
        assert "SWIGGY" in rules
        assert rules["SWIGGY"] == "Eating Out"

    def test_empty_rules_sheet_returns_empty_dict(self, isolated_db):
        import database
        empty = pd.DataFrame(columns=["keyword", "category"])
        with pd.ExcelWriter(database.DB_FILE, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            empty.to_excel(writer, sheet_name="category_rules", index=False)
        assert load_category_rules() == {}


class TestSuggestCategory:

    def test_exact_substring_match(self):
        rules = {"SWIGGY": "Eating Out"}
        assert suggest_category("SWIGGY BANGALORE ORDER", rules) == "Eating Out"

    def test_case_insensitive(self):
        rules = {"SWIGGY": "Eating Out"}
        assert suggest_category("swiggy bangalore order", rules) == "Eating Out"

    def test_no_match_returns_none(self):
        rules = {"SWIGGY": "Eating Out"}
        assert suggest_category("SOME RANDOM MERCHANT", rules) is None

    def test_empty_rules_returns_none(self):
        assert suggest_category("SWIGGY ORDER", {}) is None

    def test_empty_description_returns_none(self):
        assert suggest_category("", {"SWIGGY": "Eating Out"}) is None

    def test_longest_keyword_wins(self):
        # A more specific rule should beat a shorter, looser one
        rules = {"JIO": "Utilities", "JIO FIBER": "Utilities", "JIOHOTSTAR": "Party"}
        assert suggest_category("JIOHOTSTAR SUBSCRIPTION", rules) == "Party"


class TestExtractRuleKeyword:
    """
    extract_rule_keyword expects an already-cleaned description (i.e. the
    output of clean_bank_narration, which is what apply_category_rules and
    the Triage 'Save' learning step actually feed it) -- it does NOT itself
    strip embedded digits. These tests reflect that real usage pattern.
    """

    def test_drops_short_tokens(self):
        key = extract_rule_keyword("A BC MERCHANT XY NAME")
        assert "A" not in key.split()
        assert "BC" not in key.split()

    def test_limits_to_three_tokens(self):
        key = extract_rule_keyword("ALPHA BETA GAMMA DELTA EPSILON")
        assert len(key.split()) <= 3

    def test_consistent_when_fed_already_cleaned_text(self):
        # This is the realistic path: clean_bank_narration runs FIRST and
        # strips reference numbers, so by the time extract_rule_keyword sees
        # the text, two transactions at the same merchant look identical.
        from logic import clean_bank_narration
        raw1 = "WDL TFR   UPI/DR/111111111111/SWIGGY LTD/UTIB/x/Pa   9999999999 AT 12345 SOME BRANCH"
        raw2 = "WDL TFR   UPI/DR/222222222222/SWIGGY LTD/UTIB/x/Pa   8888888888 AT 12345 SOME BRANCH"
        k1 = extract_rule_keyword(clean_bank_narration(raw1))
        k2 = extract_rule_keyword(clean_bank_narration(raw2))
        assert k1 == k2

    def test_raw_uncleaned_input_is_not_guaranteed_stable(self):
        # Documents the precondition explicitly: feeding raw (uncleaned) text
        # with embedded reference numbers directly to extract_rule_keyword is
        # NOT safe -- different reference numbers produce different keys.
        # This is why apply_category_rules always cleans first.
        k1 = extract_rule_keyword("SWIGGY BANGALORE 111111")
        k2 = extract_rule_keyword("SWIGGY BANGALORE 222222")
        assert k1 != k2  # documents the limitation rather than hiding it


class TestApplyCategoryRules:

    def test_fills_uncategorized_rows_from_seeded_rules(self, isolated_db):
        df = pd.DataFrame({
            "description": ["Swiggy order", "Random unmapped merchant"],
            "category": ["Uncategorized", "Uncategorized"],
            "is_reviewed": [False, False],
        })
        result = apply_category_rules(df)
        assert result.loc[0, "category"] == "Eating Out"
        assert result.loc[0, "is_reviewed"] == True  # noqa: E712
        assert result.loc[1, "category"] == "Uncategorized"
        assert result.loc[1, "is_reviewed"] == False  # noqa: E712

    def test_does_not_touch_already_categorized_rows(self, isolated_db):
        df = pd.DataFrame({
            "description": ["Swiggy order"],
            "category": ["Settlement"],
            "is_reviewed": [False],
        })
        result = apply_category_rules(df)
        assert result.loc[0, "category"] == "Settlement"
        assert result.loc[0, "is_reviewed"] == False  # noqa: E712

    def test_learned_rule_applies_on_next_upload(self, isolated_db):
        import database
        # Simulate the user having taught the app "COFFEE HOUSE -> Eating Out"
        rules_df = pd.read_excel(database.DB_FILE, sheet_name="category_rules")
        rules_df = pd.concat([rules_df, pd.DataFrame([{"keyword": "COFFEE HOUSE", "category": "Eating Out"}])], ignore_index=True)
        with pd.ExcelWriter(database.DB_FILE, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            rules_df.to_excel(writer, sheet_name="category_rules", index=False)

        df = pd.DataFrame({
            "description": ["Coffee House Indiranagar"],
            "category": ["Uncategorized"],
            "is_reviewed": [False],
        })
        result = apply_category_rules(df)
        assert result.loc[0, "category"] == "Eating Out"


class TestMapSplitwiseCategory:

    def test_known_category(self):
        assert map_splitwise_category("Groceries") == "Groceries"

    def test_case_insensitive(self):
        assert map_splitwise_category("groceries") == "Groceries"
        assert map_splitwise_category("GROCERIES") == "Groceries"

    def test_dining_out_maps_to_eating_out(self):
        assert map_splitwise_category("Dining out") == "Eating Out"

    def test_unknown_category_returns_none(self):
        assert map_splitwise_category("Some Completely Unmapped Thing") is None

    def test_none_input_returns_none(self):
        assert map_splitwise_category(None) is None

    def test_payment_is_handled_separately_not_via_this_map(self):
        # 'Payment' rows are forced to 'Settlement' via the dedicated
        # is_settlement branch in standardize_splitwise_data, not through
        # this category map -- so map_splitwise_category correctly returns
        # None for it (see finalize_splitwise_category for the full picture).
        assert map_splitwise_category("Payment") is None
