"""Tests of the calibration fits on tiny fixtures (ticket B18). No external file is needed: every fixture is built here."""
from __future__ import annotations

import math
import struct
import zipfile

import pytest

import fit_bands as B
import fit_bcsp as C
import fit_dm01 as D
import fit_noshow as N
import stats as S
import xlsread as L
import xlsxread as X


# ---- helpers: a tiny .xlsx writer ---------------------------------------------------------------------------
def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path, sheets: dict[str, list[list]]) -> None:
    strings: list[str] = []

    def sidx(s: str) -> int:
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    sheet_xml = []
    for rows in sheets.values():
        out = []
        for i, row in enumerate(rows, 1):
            cells = []
            for j, v in enumerate(row, 1):
                if v is None:
                    continue
                ref = f"{col_letter(j)}{i}"
                if isinstance(v, str):
                    cells.append(f'<c r="{ref}" t="s"><v>{sidx(v)}</v></c>')
                else:
                    cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            out.append(f'<row r="{i}">{"".join(cells)}</row>')
        sheet_xml.append(f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(out)}</sheetData></worksheet>')
    with zipfile.ZipFile(path, "w") as z:
        wb = "".join(f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(sheets, 1))
        z.writestr("xl/workbook.xml", f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{wb}</sheets></workbook>')
        rels = "".join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
        z.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        for i, x in enumerate(sheet_xml, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", x)


def test_xlsx_reader_reads_strings_and_numbers(tmp_path):
    p = tmp_path / "t.xlsx"
    write_xlsx(p, {"One": [["a", 1.5], [None, "b"]], "Two": [[None, None, 7]]})
    book = X.read_xlsx(p)
    assert book["One"] == {(1, 1): "a", (1, 2): 1.5, (2, 2): "b"}
    assert book["Two"] == {(1, 3): 7.0}
    assert X.rows(book["One"]) == [(1, ["a", 1.5]), (2, [None, "b"])]


def test_xlsx_reader_rejects_a_member_over_the_limit(tmp_path, monkeypatch):
    p = tmp_path / "t.xlsx"
    write_xlsx(p, {"One": [["a"]]})
    monkeypatch.setattr(X, "MAX_MEMBER", 10)
    with pytest.raises(ValueError):
        X.read_xlsx(p)


# ---- legacy xls pieces ---------------------------------------------------------------------------------------
def test_rk_encoding():
    assert L._rk((5 << 2) | 2) == 5.0                      # integer 5
    assert L._rk(((-3) << 2 & 0xFFFFFFFF) | 2) == -3.0    # negative integer
    assert L._rk(((250) << 2) | 3) == 2.5                  # integer 250 / 100
    d = struct.unpack("<Q", struct.pack("<d", 1.5))[0] >> 32
    assert L._rk(d & 0xFFFFFFFC) == 1.5                    # IEEE double, top 30 bits


def test_sst_string_split_across_continue_records():
    def hdr(total, uniq):
        return struct.pack("<II", total, uniq)

    def s8(txt):  # compressed (latin-1) string
        return struct.pack("<HB", len(txt), 0) + txt.encode()

    first = hdr(3, 3) + s8("alpha") + struct.pack("<HB", 6, 0) + b"bet"      # "beta12" split after 3 chars
    cont1 = b"\x00" + b"a12" + struct.pack("<HB", 2, 1) + "é".encode("utf-16le")[:2] + b"\x00\x00"[:0]
    # second record continues with a flag byte (0 = still compressed); the third string is UTF-16, "é" + "x"
    cont1 = b"\x00" + b"a12" + struct.pack("<HB", 2, 1) + "éx".encode("utf-16le")
    assert L._read_sst([first, cont1]) == ["alpha", "beta12", "éx"]


# ---- numerics ------------------------------------------------------------------------------------------------
def test_normal_quantile_round_trip_and_hermite_moments():
    for p in (0.001, 0.288, 0.5, 0.704, 0.999):
        assert S.norm_cdf(S.norm_ppf(p)) == pytest.approx(p, abs=1e-12)
    x, w = S.gauss_hermite(12)
    assert sum(w) == pytest.approx(1.0)
    assert sum(wi * xi ** 2 for xi, wi in zip(x, w)) == pytest.approx(1.0)
    assert sum(wi * xi ** 4 for xi, wi in zip(x, w)) == pytest.approx(3.0)


def lcg(seed: int):
    state = seed
    while True:
        state = (1103515245 * state + 12345) % (2 ** 31)
        yield state / 2 ** 31


def test_logistic_newton_recovers_coefficients():
    g = lcg(7)
    X_, y = [], []
    for _ in range(6000):
        x1 = next(g) * 2 - 1
        x2 = 1.0 if next(g) < 0.5 else 0.0
        p = S.sigmoid(-0.5 + 1.0 * x1 - 0.8 * x2)
        X_.append([1.0, x1, x2])
        y.append(1.0 if next(g) < p else 0.0)
    beta, cov, ll = S.logistic_newton(X_, y)
    assert beta == pytest.approx([-0.5, 1.0, -0.8], abs=0.15)
    assert all(cov[i][i] > 0 for i in range(3))


# ---- lognormal bands (Public Health Scotland style) ------------------------------------------------------------
def test_two_quantile_lognormal_recovers_parameters():
    mu, sg = math.log(40.0), 0.63
    cdf = lambda t: B.lognormal_cdf(t, mu, sg)
    mu2, sg2 = B.lognormal_from_two_quantiles(28.0, cdf(28.0), 56.0, cdf(56.0))
    assert (mu2, sg2) == pytest.approx((mu, sg), abs=1e-9)
    summ = B.lognormal_summary(mu2, sg2)
    assert summ["median"] == pytest.approx(40.0)
    assert summ["p75"] / summ["p25"] == pytest.approx(math.exp(2 * 0.6744897 * sg), rel=1e-5)


def phs_fixture(path, mu=math.log(40.0), sg=0.63):
    boards = ["Alpha", "Beta", "Scotland"]
    cdf = lambda t: B.lognormal_cdf(t, mu, sg) * 100
    def sheet(c1, c2, labels, med):
        rows = [[None] for _ in range(11)]
        rows.append([None, None] + boards)
        rows.append(["Males"])
        rows.append(["All persons"])
        rows.append([None, labels[0]] + [cdf(c1)] * 3)
        rows.append([None, labels[1]] + [cdf(c2) - cdf(c1)] * 3)
        rows.append([None, labels[2]] + [100 - cdf(c2)] * 3)
        rows.append([])
        rows.append([None, med] + [math.exp(mu) / (7.0 if "weeks" in med else 1.0)] * 3)
        return rows
    a4 = [[None]] * 3 + [[None, "NHS Board", "Invites", "Uptake", "Positive tests", "Colonoscopy performed"],
                        [None, "Alpha", 1000, 600, 100, 80], [None, "Beta", 2000, 1200, 200, 150], [None, "Scotland", 3000, 1800, 300, 230]]
    ts = [[None]] * 5 + [["KPI", "Sex", "2020/22", "2021/23"], [3.0, "Persons", 2.7, 2.8]]
    write_xlsx(path, {"KPI_4": sheet(28, 56, ["0 to 4 weeks", ">4 to 8 weeks", ">8 weeks"], "median time (weeks)"),
                      "A6-Time from screening to colon": sheet(31, 62, ["0 to 31 days", ">31 to 62 days", ">62 days"], "median time (days)"),
                      "A4-Numerical counts by HB": a4, "TSData": ts})


def test_phs_extract_and_fit_on_a_fixture(tmp_path):
    p = tmp_path / "phs.xlsx"
    phs_fixture(p)
    f = B.fit_phs(B.extract_phs(p))
    sc = f["scotland"]
    assert sc["fit_weeks_bands"]["median"] == pytest.approx(40.0, rel=1e-6)
    assert sc["fit_weeks_bands"]["sigma"] == pytest.approx(0.63, rel=1e-6)
    assert sc["fit_days_bands"]["median"] == pytest.approx(40.0, rel=1e-6)
    assert sc["pi_never_in_window"] == pytest.approx(1 - 230 / 300)
    assert sc["completion_from_referral"]["90"] == pytest.approx((230 / 300) * B.lognormal_cdf(90, math.log(40), 0.63), rel=1e-6)
    assert f["kpi3_persons"] == {"2020/22": 2.7, "2021/23": 2.8}


# ---- BCSP ---------------------------------------------------------------------------------------------------------
def test_bcsp_fit_from_extracted_values():
    ex = {"table5": {"Screened": 1000.0, "Invited": 1600.0, "Further Tests Needed": 20.0},
          "table11": {"Offer within 14 days": 19.0, "Referred": 20.0, "Offer within 14 days#2": 15.0, "Referred#2": 18.0, "Attendance within 14 days": 8.0, "Participants": 16.0},
          "table12": {"Attendance": 15.0, "Definitive FOBt+ Patients": 20.0}}
    f = C.fit_bcsp(ex)
    assert f["positivity"] == pytest.approx(0.02)
    assert f["diagnostic_uptake_after_positive"] == pytest.approx(0.75)
    assert f["attended_diagnostic_within_14d_of_ssp"] == pytest.approx(0.5)
    lo, hi = f["positivity_ci95"]
    assert lo < 0.02 < hi


def test_bcsp_england_row_is_found_under_the_totals_block(tmp_path):
    p = tmp_path / "b.xlsx"
    hdr = ["Region Code", "Region", "Hub", "Screening Provider Code", "Screening Provider", "Screened", "Invited", "Uptake (%)", "Further Tests Needed", "Positivity (%)"]
    prov = ["Y61", "East", "Eastern", "BCS1", "Bedfordshire", 100.0, 150.0, 0.66, 2.0, 0.02]
    totals_hdr = [None, None, None, "Region Code", "Region", "Screened", "Invited", "Uptake (%)", "Further Tests Needed", "Positivity (%)"]
    eng = [None, None, None, None, "England", 5000.0, 8000.0, 0.62, 90.0, 0.018]
    sheet = [["title"], [None], hdr, prov, [None], totals_hdr, [None, None, None, "Y61", "East", 100.0, 150.0, 0.66, 2.0, 0.02], eng]
    write_xlsx(p, {"Table 5": sheet, "Table 11": sheet, "Table 12": sheet})
    t5 = C.extract_bcsp(p)["table5"]
    assert t5["Screened"] == 5000.0 and t5["Further Tests Needed"] == 90.0


# ---- DM01 length-biased waiting stock ----------------------------------------------------------------------------------
def test_stock_age_cdf_is_a_cdf_and_band_probs_sum_to_one():
    p = D.band_probs(2.9, 1.0)
    assert sum(p) == pytest.approx(1.0)
    assert all(x > 0 for x in p)
    assert D.age_cdf(1e-9, 2.9, 1.0) == pytest.approx(0.0, abs=1e-6)
    assert D.age_cdf(1e7, 2.9, 1.0) == pytest.approx(1.0, abs=1e-6)


def test_stock_age_cdf_matches_numerical_integral_of_the_survival_function():
    mu, sg = 3.0, 0.8
    mean = math.exp(mu + sg * sg / 2)
    x = 30.0
    n = 20000
    h = x / n
    integral = sum((1 - B.lognormal_cdf((i + 0.5) * h, mu, sg)) for i in range(n)) * h
    assert D.age_cdf(x, mu, sg) == pytest.approx(integral / mean, rel=1e-4)


def test_stock_fit_recovers_parameters_from_exact_band_counts():
    mu, sg = 2.9, 1.0
    counts = [p * 1e6 for p in D.band_probs(mu, sg)]
    fit = D.fit_stock(counts)
    assert fit["mu"] == pytest.approx(mu, abs=1e-3)
    assert fit["sigma"] == pytest.approx(sg, abs=1e-3)
    assert fit["max_abs_band_error"] < 1e-6


# ---- no-show ------------------------------------------------------------------------------------------------------------
def test_arff_parser_handles_quoted_neighbourhoods_with_commas_and_filters(tmp_path):
    p = tmp_path / "x.arff"
    p.write_text("% c\n@RELATION x\n@ATTRIBUTE a REAL\n@DATA\n"
                 "1.0,10,F,2016-04-20T10:00:00Z,2016-04-29T00:00:00Z,40,'SANTA, MARTHA',0,0,0,0,0,1,Yes\n"
                 "2.0,11,M,2016-04-28T10:00:00Z,2016-04-29T00:00:00Z,-1,'X',0,0,0,0,0,0,No\n"
                 "3.0,12,M,2016-04-27T10:00:00Z,2016-04-29T00:00:00Z,30,'Y',0,0,0,0,0,0,No\n"
                 "4.0,13,M,2016-04-10T10:00:00Z,2016-04-29T00:00:00Z,200,'Z',0,0,0,0,0,0,No\n")
    rows = N.parse_arff(p)
    assert [r["lead"] for r in rows] == [9, 1, 2, 19]
    assert rows[0] == {"patient": "1.0", "lead": 9, "age": 40, "sms": 1, "noshow": 1}
    kept = N.keep(rows)
    assert [r["patient"] for r in kept] == ["1.0"]          # lead < 3, age -1 and age 200 are dropped


def synthetic_noshow(n_patients=900, per=3, tau=0.9, sms_logit=-0.4, seed=11):
    g = lcg(seed)
    def normal():
        u1, u2 = max(next(g), 1e-12), next(g)
        return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
    rows = []
    for i in range(n_patients):
        u = tau * normal()
        age = int(20 + 50 * next(g))
        for _ in range(per):
            lead = 3 + int(30 * next(g))
            sms = 1 if next(g) < 0.5 else 0
            eta = -1.0 + 0.2 * math.log(lead) - 0.1 * (age - N.AGE_CENTRE) / 10 + sms_logit * sms + u
            rows.append({"patient": str(i), "lead": lead, "age": age, "sms": sms, "noshow": 1 if next(g) < S.sigmoid(eta) else 0})
    return rows


def test_pooled_fit_sees_the_sms_effect_and_standardised_effect_is_a_relative_reduction():
    rows = synthetic_noshow(n_patients=2500, per=2, tau=0.0, sms_logit=-0.5)
    f = N.fit_pooled(rows)
    assert f["coef"]["sms_received"] == pytest.approx(-0.5, abs=0.15)
    assert 0.05 < f["sms_relative_reduction"] < 0.35
    lo, hi = f["sms_relative_reduction_ci95"]
    assert lo < f["sms_relative_reduction"] < hi


def test_mixed_fit_finds_between_patient_spread():
    rows = synthetic_noshow(n_patients=700, per=4, tau=1.0)
    pooled = N.fit_pooled(rows)
    m = N.fit_mixed(rows, [pooled["coef"][k] for k in N.NAMES])
    assert 0.6 < m["tau"] < 1.5
    flat = synthetic_noshow(n_patients=700, per=4, tau=0.0, seed=5)
    p2 = N.fit_pooled(flat)
    m2 = N.fit_mixed(flat, [p2["coef"][k] for k in N.NAMES])
    assert m2["tau"] < 0.4


# ---- runner -------------------------------------------------------------------------------------------------------------
def test_diff_is_tolerant_to_rounding_and_flags_changes():
    import calibrate as K
    assert K.diff({"a": 1.0000000001, "b": [1, 2]}, {"a": 1.0, "b": [1, 2]}) == []
    assert K.diff({"a": 1.0}, {"a": 1.1})
    assert K.diff({"a": 1}, {"b": 1})
    assert K.r8({"x": 0.123456789012}) == {"x": 0.12345679}
