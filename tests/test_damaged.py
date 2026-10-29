from simpleaprs.damaged import broken_at, metres, printable


def test_well_formed_weather_and_positions_are_not_faulted():
    assert broken_at("@282204z2923.02NI09838.89W&WX3in1+ / P.S.=13.4 v / Cabinet Temp=91.4") is None  # free text after a position
    assert broken_at("!2935.50N/09815.97W_269/021g023t081r000p001P001h65b10164tRSW") is None  # weather, then a software tag
    assert broken_at("_01010832c276s000g000t...r000p000P000h..b09880tRSW") is None  # positionless; dots are missing values
    assert broken_at("!2911.04N/09858.16W_PHG5487/Devine,Tx/Medina Co./3") is None  # coverage data on a weather symbol
    assert broken_at("!b?Qj]5Rt/#  ! W5REO WIDE1 DigiPi") is None  # compressed position
    assert broken_at("!!000000860352025827710361--------00B301870|") is None  # a logger's own format
    assert broken_at("`~0YlqU>/`\"8@}441.725MHz T123") is None  # not something this checks


def test_damage_it_can_see_is_reported_where_it_starts():
    assert broken_at("@282111z2935.50N/09815.97W_268/022g023t082r(0p;kL") == 42  # rain value is not a number
    assert broken_at("!2919.38NS10023.77[#PHG6634/BK") == 0  # longitude lost its hemisphere
    assert broken_at("@2821z2935.50N/09815.97W_269") == 0  # not even a timestamp


def test_printable_and_metres():
    assert printable(b"abc\x01\xfe\r") == "abc"
    assert printable(b"a\x01b") == "a.b"
    assert 100 < metres((30.0, -97.0), (30.001, -97.0)) < 120
