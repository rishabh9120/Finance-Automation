"""
Unit tests for logic.clean_bank_narration -- the merchant-name extractor
that turns raw UPI/IMPS/ACH/card-mandate narrations into something both
readable and matchable against category keyword rules.
"""
import pytest
from logic import clean_bank_narration


class TestCleanBankNarration:

    def test_upi_debit_extracts_payee(self):
        raw = "WDL TFR   UPI/DR/613858185975/VIKKY BA/YESB/paytmqr71o/UPI   0097690162095 AT 30525 IIM CAMPUS INDORE"
        result = clean_bank_narration(raw)
        assert "VIKKY BA" in result
        assert "613858185975" not in result
        assert "0097690162095" not in result

    def test_upi_credit_extracts_payee(self):
        raw = "DEP TFR   UPI/CR/655739544767/ANURAG P/HDFC/anurag chau/UPI   0097736162097 AT 30525 IIM CAMPUS INDORE"
        result = clean_bank_narration(raw)
        assert "ANURAG P" in result
        assert "655739544767" not in result

    def test_upi_merchant_name_preserved(self):
        raw = "WDL TFR    UPI/DR/657804158042/Swiggy Ltd/UTIB/swiggyupi@/Pa   0097694162092 AT 30525 IIM CAMPUS INDORE"
        result = clean_bank_narration(raw)
        assert "Swiggy" in result

    def test_card_mandate_extracts_merchant(self):
        raw = "ME DC SI 416021XXXXXX6366 SPOTIFY SI  0000613304364849"
        result = clean_bank_narration(raw)
        assert "SPOTIFY" in result.upper()
        assert "416021XXXXXX6366" not in result

    def test_ach_credit_extracts_payer(self):
        raw = "ACH C- HINDUSTAN ZINC LTD-5686250"
        result = clean_bank_narration(raw)
        assert "HINDUSTAN ZINC LTD" in result
        assert "5686250" not in result

    def test_imps_extracts_payee_and_remark(self):
        raw = "IMPS-614360970840-ABHI RAM N K-UBIN-XXXXXXXXXXX0278-MAY RENT REMAINING"
        result = clean_bank_narration(raw)
        assert "ABHI RAM N K" in result
        assert "614360970840" not in result

    def test_embedded_newlines_are_flattened(self):
        raw = "UPI/DR/613858185975/VIKKY BA/YESB/paytmq\n r71o/UPI"
        result = clean_bank_narration(raw)
        assert "\n" not in result

    def test_masked_card_numbers_stripped(self):
        raw = "POS PURCHASE 4160211234567890 SOME MERCHANT"
        result = clean_bank_narration(raw)
        assert "4160211234567890" not in result

    def test_empty_string_returns_empty(self):
        assert clean_bank_narration("") == ""

    def test_none_returns_empty(self):
        assert clean_bank_narration(None) == ""

    def test_nan_like_string_handled_gracefully(self):
        # pandas often gives literal 'nan' strings after .astype(str) on NaN
        result = clean_bank_narration("nan")
        assert isinstance(result, str)

    def test_already_clean_text_is_not_mangled(self):
        raw = "Auto rickshaw fare"
        result = clean_bank_narration(raw)
        assert "Auto" in result and "rickshaw" in result.lower() or "fare" in result.lower()

    def test_never_raises_on_garbage_input(self):
        for garbage in ["///---", "12345678901234", "", "   ", "-----"]:
            # Should never throw, always return *some* string
            assert isinstance(clean_bank_narration(garbage), str)

    @pytest.mark.parametrize("prefix", ["WDL TFR", "DEP TFR"])
    def test_transfer_prefixes_are_stripped(self, prefix):
        raw = f"{prefix}   UPI/DR/123456789012/MERCHANT NAME/HDFC/vpa@/Pa   9876543210 AT 12345 SOME BRANCH"
        result = clean_bank_narration(raw)
        assert not result.upper().startswith(prefix)
