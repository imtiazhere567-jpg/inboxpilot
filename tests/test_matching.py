from agent.matching import Party, match_any, match_party

ACME = Party(1, "Acme Supplies Ltd", ("acmesupplies.co.uk", "ACME-"))
CASTLE = Party(2, "Castle Equipment Hire", ("castlehire.co.uk", "CEH-", "tradepay.co.uk"))
GRANITE = Party(3, "Granite Facilities Supplies", ("granitefs.co.uk", "GFS-", "tradepay.co.uk"))
SUPPLIERS = [ACME, CASTLE, GRANITE]
REDWOOD = Party(9, "Redwood Care Homes", ("redwoodcare.co.uk", "RCH-"))


def test_domain_match():
    r = match_party("supplier", "From: accounts@acmesupplies.co.uk\nInvoice", SUPPLIERS)
    assert r.status == "matched" and r.party_name == "Acme Supplies Ltd" and "acmesupplies.co.uk" in r.matched_on


def test_prefix_needs_digit_and_is_case_insensitive():
    assert match_party("supplier", "ref acme-2031", SUPPLIERS).status == "matched"
    assert match_party("supplier", "the acme- brand", SUPPLIERS).status == "none"
    assert match_party("supplier", "PO NF-ACME-1", SUPPLIERS).status == "none"  # embedded in another token


def test_ambiguous_when_two_parties_share_an_identifier():
    r = match_party("supplier", "billing via tradepay.co.uk", SUPPLIERS)
    assert r.status == "ambiguous" and r.candidates == ["Castle Equipment Hire", "Granite Facilities Supplies"]


def test_name_only_is_never_trusted():
    r = match_party("customer", "Signed, Jas Patel, Redwood Care Homes", [REDWOOD])
    assert r.status == "name_only" and r.party_id == 9


def test_none():
    assert match_party("supplier", "hello world", SUPPLIERS).status == "none"


def test_match_any_prefers_kind_but_falls_through():
    r = match_any("From: ops@redwoodcare.co.uk", SUPPLIERS, [REDWOOD], prefer="supplier")
    assert r.status == "matched" and r.party_kind == "customer"
