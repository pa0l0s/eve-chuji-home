from esi import parse_fitting_link


def test_parse_fitting_basic():
    href = "fitting:621:31360;1:1877;5:27361;1000::"
    ship_id, items = parse_fitting_link(href)
    assert ship_id == 621
    assert items == [(31360, 1), (1877, 5), (27361, 1000)]


def test_parse_fitting_defaults_qty_when_missing():
    ship_id, items = parse_fitting_link("fitting:621:31360::")
    assert ship_id == 621
    assert items == [(31360, 1)]


def test_parse_fitting_rejects_non_fitting():
    assert parse_fitting_link("showinfo:1377//94409715") == (0, [])
    assert parse_fitting_link("fitting:abc:1;1::") == (0, [])
