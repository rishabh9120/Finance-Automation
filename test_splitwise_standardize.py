"""
Unit tests for logic.standardize_splitwise_data -- covers bug #1 (the
"Total balance" summary row), bug #2 (settlements treated as expenses),
and a wide range of "various splits" scenarios (full pay, partial share,
someone-else-paid, perfectly-even split, multi-person groups).
"""
import pandas as pd
import pytest
from logic import standardize_splitwise_data


USER = "Rishabh Agrawal"


class TestTotalBalanceRowFiltering:
    """bug #1: Splitwise's own running-balance summary row must never be
    imported as a transaction, regardless of which way the sign happens to
    fall (the original bug only "worked" by sign coincidence)."""

    def test_total_balance_row_dropped_when_balance_positive(self, splitwise_df_factory, splitwise_users):
        rows = [
            dict(Date="2026-07-01", Description="Groceries", Category="Groceries", Cost=1000, Currency="INR",
                 **{"Rishabh Agrawal": -500, "Geetesh Mokhare": 500, "AC": 0}),
            # Positive running balance -- this is the case that used to slip through as a phantom expense
            dict(Date="2026-08-03", Description="Total balance", Category="", Cost="", Currency="",
                 **{"Rishabh Agrawal": 13958.06, "Geetesh Mokhare": -9878.42, "AC": -4079.64}),
        ]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        assert not (result["Description"].str.lower() == "total balance").any()

    def test_total_balance_row_dropped_when_balance_negative(self, splitwise_df_factory, splitwise_users):
        rows = [
            dict(Date="2026-07-01", Description="Groceries", Category="Groceries", Cost=1000, Currency="INR",
                 **{"Rishabh Agrawal": -500, "Geetesh Mokhare": 500, "AC": 0}),
            dict(Date="2026-08-03", Description="Total balance", Category="", Cost="", Currency="",
                 **{"Rishabh Agrawal": -13958.06, "Geetesh Mokhare": 9878.42, "AC": 4079.64}),
        ]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        assert not (result["Description"].str.lower() == "total balance").any()
        # Would previously have been a phantom expense of ~13958
        assert result["Amount"].max() < 13958

    def test_blank_category_footer_rows_dropped(self, splitwise_df_factory, splitwise_users):
        rows = [
            dict(Date="2026-07-01", Description="Groceries", Category="Groceries", Cost=1000, Currency="INR",
                 **{"Rishabh Agrawal": -500, "Geetesh Mokhare": 500, "AC": 0}),
            dict(Date="2026-07-02", Description="Some footer artifact", Category="", Cost="", Currency="",
                 **{"Rishabh Agrawal": 0, "Geetesh Mokhare": 0, "AC": 0}),
        ]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        assert len(result) == 1


class TestSettlementHandling:
    """bug #2: settlement ('Payment') rows move cash between people -- they
    must never inflate expense categories."""

    def test_settlement_received_is_not_an_expense(self, splitwise_df_factory, splitwise_users):
        # A roommate paid Rishabh back 3000 -> Rishabh's net entry is negative
        rows = [dict(Date="2026-07-10", Description="AC paid Rishabh A.", Category="Payment", Cost=3000, Currency="INR",
                      **{"Rishabh Agrawal": -3000, "Geetesh Mokhare": 0, "AC": 3000})]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        row = result.iloc[0]
        assert row["Amount"] == 0
        assert row["you_received"] == 3000
        assert row["you_paid"] == 0
        assert row["Type"] == "Settlement"

    def test_settlement_paid_is_not_an_expense(self, splitwise_df_factory, splitwise_users):
        # Rishabh paid a roommate back 2000 -> Rishabh's net entry is positive
        rows = [dict(Date="2026-07-22", Description="Rishabh A. paid AC", Category="Payment", Cost=2000, Currency="INR",
                      **{"Rishabh Agrawal": 2000, "Geetesh Mokhare": 0, "AC": -2000})]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        row = result.iloc[0]
        assert row["Amount"] == 0
        assert row["you_paid"] == 2000
        assert row["you_received"] == 0
        assert row["Type"] == "Settlement"

    def test_settlement_rows_carry_settlement_category(self, splitwise_df_factory, splitwise_users):
        rows = [dict(Date="2026-07-22", Description="Rishabh A. paid AC", Category="Payment", Cost=2000, Currency="INR",
                      **{"Rishabh Agrawal": 2000, "Geetesh Mokhare": 0, "AC": -2000})]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        assert result.iloc[0]["_settlement_category"] == "Settlement"


class TestVariousSplits:
    """A range of real-world group-expense split scenarios."""

    def test_you_fronted_the_full_bill_two_way_split(self, splitwise_df_factory, splitwise_users):
        # You paid 1000 for a 2-way split (500/500) -> you're owed 500
        rows = [dict(Date="2026-07-01", Description="Dinner", Category="Dining out", Cost=1000, Currency="INR",
                      **{"Rishabh Agrawal": 500, "Geetesh Mokhare": -500, "AC": 0})]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        row = result.iloc[0]
        assert row["you_paid"] == 1000
        assert row["your_share"] == 500  # cost - net = 1000 - 500

    def test_someone_else_paid_you_owe_your_share(self, splitwise_df_factory, splitwise_users):
        # Someone else paid 900 for a 3-way split, your share is 300
        rows = [dict(Date="2026-07-02", Description="Groceries", Category="Groceries", Cost=900, Currency="INR",
                      **{"Rishabh Agrawal": -300, "Geetesh Mokhare": 600, "AC": -300})]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        row = result.iloc[0]
        assert row["you_paid"] == 0
        assert row["your_share"] == 300
        assert row["Amount"] == 300

    def test_perfectly_even_zero_net_row_is_dropped(self, splitwise_df_factory, splitwise_users):
        # You paid exactly your own share and nothing more -- net is 0,
        # there's genuinely no expense or transfer to track.
        rows = [dict(Date="2026-07-03", Description="Solo lunch logged to group", Category="Dining out", Cost=200, Currency="INR",
                      **{"Rishabh Agrawal": 0, "Geetesh Mokhare": 0, "AC": 0})]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        assert len(result) == 0  # correctly filtered out -- Amount=0, you_paid=0, you_received=0

    def test_three_way_uneven_split(self, splitwise_df_factory, splitwise_users):
        # Cost 1200, you fronted it, split 600/400/200 across three people.
        # Your net = +600 (owed 600 back: 1200 - 600 = the other two owe you 600 combined)
        rows = [dict(Date="2026-07-04", Description="Road trip fuel", Category="Car", Cost=1200, Currency="INR",
                      **{"Rishabh Agrawal": 600, "Geetesh Mokhare": -400, "AC": -200})]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        row = result.iloc[0]
        assert row["you_paid"] == 1200
        assert row["your_share"] == 600  # 1200 - 600

    def test_you_owe_a_small_uneven_share(self, splitwise_df_factory, splitwise_users):
        rows = [dict(Date="2026-07-05", Description="Cab", Category="Taxi", Cost=250, Currency="INR",
                      **{"Rishabh Agrawal": -83.33, "Geetesh Mokhare": 166.66, "AC": -83.33})]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        row = result.iloc[0]
        assert abs(row["your_share"] - 83.33) < 0.01
        assert row["you_paid"] == 0

    def test_multiple_expenses_mixed_directions(self, splitwise_df_factory, splitwise_users):
        rows = [
            dict(Date="2026-07-01", Description="Dinner", Category="Dining out", Cost=1000, Currency="INR",
                 **{"Rishabh Agrawal": 500, "Geetesh Mokhare": -500, "AC": 0}),
            dict(Date="2026-07-02", Description="Groceries", Category="Groceries", Cost=900, Currency="INR",
                 **{"Rishabh Agrawal": -300, "Geetesh Mokhare": 600, "AC": -300}),
            dict(Date="2026-07-03", Description="Rent", Category="Rent", Cost=30000, Currency="INR",
                 **{"Rishabh Agrawal": -10000, "Geetesh Mokhare": 20000, "AC": -10000}),
        ]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        assert len(result) == 3
        assert set(result["Description"]) == {"Dinner", "Groceries", "Rent"}


class TestSplitwiseCategoryMapping:
    """item #4: Splitwise's own category should map onto our taxonomy."""

    def test_groceries_maps_to_groceries(self, splitwise_df_factory, splitwise_users):
        rows = [dict(Date="2026-07-01", Description="Weekly shop", Category="Groceries", Cost=500, Currency="INR",
                      **{"Rishabh Agrawal": 250, "Geetesh Mokhare": -250, "AC": 0})]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        assert result.iloc[0]["_category_hint"] == "Groceries"

    def test_dining_out_maps_to_eating_out(self, splitwise_df_factory, splitwise_users):
        rows = [dict(Date="2026-07-01", Description="Dinner", Category="Dining out", Cost=1000, Currency="INR",
                      **{"Rishabh Agrawal": 500, "Geetesh Mokhare": -500, "AC": 0})]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        assert result.iloc[0]["_category_hint"] == "Eating Out"

    def test_car_maps_to_transport(self, splitwise_df_factory, splitwise_users):
        rows = [dict(Date="2026-07-01", Description="Fuel", Category="Car", Cost=1200, Currency="INR",
                      **{"Rishabh Agrawal": 600, "Geetesh Mokhare": -400, "AC": -200})]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        assert result.iloc[0]["_category_hint"] == "Transport"

    def test_unknown_category_hint_is_none(self, splitwise_df_factory, splitwise_users):
        rows = [dict(Date="2026-07-01", Description="Mystery item", Category="Some Weird Unmapped Category", Cost=100, Currency="INR",
                      **{"Rishabh Agrawal": 50, "Geetesh Mokhare": -50, "AC": 0})]
        df = splitwise_df_factory(rows, splitwise_users)
        result = standardize_splitwise_data(df, user_name=USER)
        assert result.iloc[0]["_category_hint"] is None


class TestErrorHandling:

    def test_missing_username_raises(self, splitwise_df_factory, splitwise_users):
        rows = [dict(Date="2026-07-01", Description="Dinner", Category="Dining out", Cost=1000, Currency="INR",
                      **{"Rishabh Agrawal": 500, "Geetesh Mokhare": -500, "AC": 0})]
        df = splitwise_df_factory(rows, splitwise_users)
        with pytest.raises(ValueError, match="not found in Splitwise columns"):
            standardize_splitwise_data(df, user_name="Someone Else Entirely")
