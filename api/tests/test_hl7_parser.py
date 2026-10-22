"""A28 (ADR-039): the HL7 v2 parser and ACK writer. Segments, fields, repetitions, components, sub-components, escapes,
timestamps, MLLP framing, and what a malformed message does (a fixed-phrase error, never message content)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.hl7 import parser as p

MSH = "MSH|^~\\&|LAB|FAC|SC|SCF|20260105080000||ORU^R01^ORU_R01|CTRL1|P|2.5.1"


def msg(*segments: str, sep: str = "\r") -> str:
    return sep.join([MSH, *segments]) + sep


def test_fields_components_repetitions_and_subcomponents_are_addressed_by_standard_numbers():
    m = p.parse(msg("PID|1||AAA^^^SYS1&1.2.3&ISO~BBB^^^SYS2||Doe^Jane^Q||19700101|F"))
    pid = m.first("PID")
    assert m.message_type == ("ORU", "R01") and m.control_id == "CTRL1" and m.sender == "LAB|FAC"
    assert pid.reps(3) == 2 and pid.get(3, 1, rep=0) == "AAA" and pid.get(3, 1, rep=1) == "BBB"
    assert pid.get(3, 4, 1) == "SYS1" and pid.get(3, 4, 2) == "1.2.3" and pid.get(3, 4, 3) == "ISO"
    assert pid.all(3, 1) == ["AAA", "BBB"]
    assert pid.get(5, 2) == "Jane" and pid.get(5, 4) == "" and pid.get(99) == "" and pid.get(3, rep=7) == ""
    assert pid.get(1) == "1" and pid.reps(4) == 0


def test_msh_numbering_counts_the_separator_as_field_one():
    m = p.parse(msg())
    assert m.msh.get(1) == "|" and m.msh.get(2) == "^~\\&" and m.msh.get(3) == "LAB" and m.msh.get(9, 2) == "R01"
    assert m.msh.get(12) == "2.5.1"


@pytest.mark.parametrize("sep", ["\r", "\n", "\r\n"])
def test_any_segment_separator_and_an_mllp_frame_are_accepted(sep):
    text = msg("PID|1||X1^^^S", sep=sep)
    assert p.parse(text).first("PID").get(3, 1) == "X1"
    framed = p.mllp_unframe(p.mllp_frame(text))
    assert p.parse(framed).control_id == "CTRL1"
    assert p.parse("\x0b" + text + "\x1c\r").control_id == "CTRL1"


def test_escape_sequences_are_resolved_and_written_back():
    d = p.Delimiters()
    assert p.unescape("a\\F\\b\\S\\c\\T\\d\\R\\e\\E\\f", d) == "a|b^c&d~e\\f"
    assert p.unescape("line1\\.br\\line2", d) == "line1\nline2"
    assert p.unescape("check \\Xe29c93\\ ok", d) == "check ✓ ok"
    assert p.unescape("bold \\H\\here\\N\\ plain \\Zlocal\\", d) == "bold here plain "  # formatting and unknown dropped
    assert p.unescape("lone \\ escape", d) == "lone "  # an unterminated escape is dropped, not an error
    assert p.unescape("\\Xzz\\", d) == "" and p.unescape("\\XFF\\", d) == ""  # bad hex / invalid UTF-8 dropped
    for raw in ["plain", "a|b^c&d~e\\f", "two\nlines", "end\\"]:
        assert p.unescape(p.escape(raw, d), d) == raw


def test_an_escaped_delimiter_does_not_split_a_field():
    m = p.parse(msg("NTE|1||a\\F\\b\\S\\c"))
    nte = m.first("NTE")
    assert nte.reps(3) == 1 and nte.get(3) == "a|b^c" and nte.get(3, 2) == ""


def test_custom_delimiters_in_msh_are_honoured():
    text = "MSH#*~\\&#LAB#FAC#SC#SCF#20260105#*#ORU*R01#C9#P#2.5.1\rPID#1##A*B~C*D"
    m = p.parse(text)
    assert m.control_id == "C9" and m.message_type == ("ORU", "R01")
    assert m.first("PID").all(3, 1) == ["A", "C"] and m.first("PID").get(3, 2, rep=1) == "D"


@pytest.mark.parametrize("text,code", [
    ("", "100"), ("   \r\n", "100"), ("PID|1||X", "100"), ("MS", "100"),
    ("MSH|^~\\&|A|B|C|D|2026||ORU^R01|", "101"),            # no control id
    ("MSH|^~\\&|A|B|C|D|2026|||CTRL|P|2.5", "101"),          # no message type
    ("MSH|^|A\r", "100"),                                    # short encoding characters
    ("MSH||^~\\&|", "102"), ("MSH|||~\\&|", "102"),         # delimiters repeated / empty
    (MSH + "\rXX|1", "100"), (MSH + "\r1PID|1", "100"), (MSH + "\rPIDX|1", "100"),  # bad segment names
    (MSH + "\r" + MSH, "100"),                               # a second MSH (batch)
])
def test_malformed_input_is_a_parse_error_with_a_code_and_a_fixed_reason(text, code):
    with pytest.raises(p.Hl7ParseError) as exc:
        p.parse(text)
    assert exc.value.code == code
    assert exc.value.reason in {"empty message", "message does not start with an MSH segment", "invalid delimiter characters in MSH",
                                "MSH-9 or MSH-10 is missing", "segment is not well formed",
                                "a second MSH segment (batches are not supported)"}


def test_size_limits_are_enforced_without_echoing_the_input():
    with pytest.raises(p.Hl7ParseError, match="too large"):
        p.parse(MSH + "\r" + "NTE|1||" + "x" * (p.MAX_MESSAGE_BYTES + 1))
    with pytest.raises(p.Hl7ParseError, match="too many segments"):
        p.parse(MSH + "\r" + "\r".join(["NTE|1"] * (p.MAX_SEGMENTS + 1)))
    with pytest.raises(p.Hl7ParseError) as exc:
        p.parse("SECRET-NAME-DOE\r")
    assert "DOE" not in str(exc.value) and "DOE" not in exc.value.reason


def test_empty_trailing_fields_and_a_trailing_blank_line_are_harmless():
    m = p.parse(msg("PID|1||||||||||||", "NTE|1") + "\r\r")
    assert [s.name for s in m.segments] == ["MSH", "PID", "NTE"]


@pytest.mark.parametrize("value,expected", [
    ("20260105", (datetime(2026, 1, 5), "date")),
    ("202601", (datetime(2026, 1, 1), "date")),
    ("2026010509", (datetime(2026, 1, 5, 9), "datetime")),
    ("20260105093015", (datetime(2026, 1, 5, 9, 30, 15), "datetime")),
    ("20260105093015.1234", (datetime(2026, 1, 5, 9, 30, 15), "datetime")),
])
def test_timestamps_parse_with_their_precision(value, expected):
    assert p.parse_ts(value) == expected


def test_timestamp_offsets_and_invalid_values():
    when, precision = p.parse_ts("20260105093000+0400")
    assert when.astimezone(UTC) == datetime(2026, 1, 5, 5, 30, tzinfo=UTC) and precision == "datetime"
    for bad in ["", "2026-01-05", "20261301", "20260230", "abc", "20260105250000"]:
        assert p.parse_ts(bad) == (None, None), bad


def test_an_ack_echoes_the_control_id_swaps_the_parties_and_carries_only_fixed_text():
    m = p.parse(msg("PID|1||X"))
    ack = p.parse(p.build_ack(m, "AE", "201", "trigger event not supported"))
    assert ack.message_type == ("ACK", "R01") and ack.msh.get(3) == "screening-closure" and ack.msh.get(5) == "LAB"
    msa = ack.first("MSA")
    assert (msa.get(1), msa.get(2), msa.get(3)) == ("AE", "CTRL1", "trigger event not supported")
    err = ack.first("ERR")
    assert err.get(3, 1) == "201" and err.get(3, 3) == "HL70357" and err.get(4) == "E"
    aa = p.parse(p.build_ack(m, "AA"))
    assert aa.first("MSA").get(1) == "AA" and aa.first("ERR") is None
    assert aa.control_id != m.control_id and len(aa.control_id) == 20


def test_an_ack_without_a_readable_message_is_still_a_valid_message():
    ack = p.parse(p.build_ack(None, "AR", "100", "empty message"))
    assert ack.first("MSA").get(1) == "AR" and ack.first("MSA").get(2) == "" and ack.first("ERR").get(3, 1) == "100"
    with pytest.raises(ValueError):
        p.build_ack(None, "XX")


def test_reasons_are_escaped_in_the_ack():
    ack = p.build_ack(p.parse(msg()), "AE", "207", "a|b^c")
    assert p.parse(ack).first("MSA").get(3) == "a|b^c"
