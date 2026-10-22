"""A small HL7 v2 parser and writer: segments, fields, repetitions, components, sub-components and escapes.

Pure Python, standard library only. It reads what the sender wrote and gives it back unchanged; it validates nothing
about the message type (that is `handler`'s job). Every failure is a `Hl7ParseError` whose text is a fixed phrase: a
message's content never appears in an error, so an error can be logged and put in an ACK without leaking PHI.

Numbering follows the standard. For `MSH`, field 1 is the field separator and field 2 the encoding characters, so
`MSH-9` is the message type; for every other segment field 1 is the first field after the name.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

MAX_MESSAGE_BYTES = 1_048_576
MAX_SEGMENTS = 2_000
MLLP_START = b"\x0b"
MLLP_END = b"\x1c\x0d"

_SEGMENT_NAME = re.compile(r"^[A-Z][A-Z0-9]{2}$")
_ESCAPE_RUN = re.compile(r"\\([^\\]*)\\")


class Hl7ParseError(ValueError):
    """The message cannot be read. `code` is an HL70357 error code; `reason` a fixed phrase (never message content)."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class Delimiters:
    field: str = "|"
    component: str = "^"
    repetition: str = "~"
    escape: str = "\\"
    subcomponent: str = "&"

    @classmethod
    def from_msh(cls, line: str) -> Delimiters:
        if len(line) < 8 or not line.startswith("MSH"):
            raise Hl7ParseError("100", "message does not start with an MSH segment")
        d = cls(field=line[3], component=line[4], repetition=line[5], escape=line[6], subcomponent=line[7])
        chars = [d.field, d.component, d.repetition, d.escape, d.subcomponent]
        if len(set(chars)) != 5 or any(c in "\r\n" or c.isalnum() for c in chars):
            raise Hl7ParseError("102", "invalid delimiter characters in MSH")
        return d


def unescape(value: str, d: Delimiters) -> str:
    """Resolve the escape sequences of one leaf value: \\F\\ \\S\\ \\T\\ \\R\\ \\E\\, \\.br\\ (a line break) and \\Xhh..\\
    (hex bytes, read as UTF-8). Formatting sequences (\\H\\ \\N\\ ...) and unknown ones are dropped, as the standard allows,
    and so is an unterminated escape (a lone escape character)."""
    if d.escape not in value:
        return value
    table = {"F": d.field, "S": d.component, "T": d.subcomponent, "R": d.repetition, "E": d.escape}
    parts = value.split(d.escape)
    out = [parts[0]]
    # parts alternate text, sequence, text, sequence ... ; the final part is text only if the count of escapes is even
    i = 1
    while i < len(parts):
        if i + 1 >= len(parts):
            break  # a lone, unterminated escape: dropped with whatever follows it
        body, text = parts[i], parts[i + 1]
        if body in table:
            out.append(table[body])
        elif body == ".br":
            out.append("\n")
        elif re.fullmatch(r"X(?:[0-9A-Fa-f]{2})+", body):
            try:
                out.append(bytes.fromhex(body[1:]).decode("utf-8"))
            except UnicodeDecodeError:
                pass
        out.append(text)
        i += 2
    return "".join(out)


def escape(value: str, d: Delimiters) -> str:
    """The inverse of `unescape` for one leaf value (used when writing a message)."""
    value = value.replace(d.escape, d.escape + "E" + d.escape)
    value = value.replace(d.field, d.escape + "F" + d.escape)
    value = value.replace(d.component, d.escape + "S" + d.escape)
    value = value.replace(d.subcomponent, d.escape + "T" + d.escape)
    value = value.replace(d.repetition, d.escape + "R" + d.escape)
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", d.escape + ".br" + d.escape)


@dataclass(frozen=True)
class Segment:
    name: str
    fields: tuple[str, ...]  # raw (still escaped) text; fields[n-1] is field n
    d: Delimiters = field(default_factory=Delimiters, compare=False, repr=False)

    def reps(self, n: int) -> int:
        """How many repetitions field `n` has (0 when it is absent or empty)."""
        raw = self._raw(n)
        return len(raw.split(self.d.repetition)) if raw else 0

    def _raw(self, n: int) -> str:
        return self.fields[n - 1] if 0 < n <= len(self.fields) else ""

    def get(self, n: int, comp: int | None = None, sub: int | None = None, rep: int = 0) -> str:
        """Field `n`, repetition `rep` (0-based), optionally component `comp` and sub-component `sub` (both 1-based),
        with escapes resolved. An absent field, repetition, component or sub-component is the empty string. MSH-1 and
        MSH-2 are returned verbatim (they hold the delimiters themselves)."""
        if self.name == "MSH" and n in (1, 2):
            return self._raw(n)
        raw = self._raw(n)
        if not raw:
            return ""
        parts = raw.split(self.d.repetition)
        if rep >= len(parts):
            return ""
        value = parts[rep]
        if comp is None and sub is not None:
            comp = 1
        if comp is not None:
            comps = value.split(self.d.component)
            if comp > len(comps):
                return ""
            value = comps[comp - 1]
            if sub is not None:
                subs = value.split(self.d.subcomponent)
                if sub > len(subs):
                    return ""
                value = subs[sub - 1]
        return unescape(value, self.d)

    def all(self, n: int, comp: int | None = None, sub: int | None = None) -> list[str]:
        """The value of field `n` (component `comp`) in every repetition, in order."""
        return [self.get(n, comp, sub, rep=i) for i in range(self.reps(n))]


@dataclass(frozen=True)
class Message:
    segments: tuple[Segment, ...]
    d: Delimiters

    @property
    def msh(self) -> Segment:
        return self.segments[0]

    def find(self, name: str) -> list[Segment]:
        return [s for s in self.segments if s.name == name]

    def first(self, name: str) -> Segment | None:
        return next((s for s in self.segments if s.name == name), None)

    @property
    def control_id(self) -> str:
        return self.msh.get(10)

    @property
    def message_type(self) -> tuple[str, str]:
        """(message code, trigger event) from MSH-9, e.g. ("ORU", "R01")."""
        return self.msh.get(9, 1), self.msh.get(9, 2)

    @property
    def sender(self) -> str:
        """Sending application and facility, `app|facility` (MSH-3.1, MSH-4.1): part of the idempotency key."""
        return f"{self.msh.get(3, 1)}|{self.msh.get(4, 1)}"


def parse(text: str) -> Message:
    """Parse one HL7 v2 message. Accepts `\\r`, `\\n` or `\\r\\n` between segments and an MLLP frame around it."""
    if not isinstance(text, str) or not text.strip():
        raise Hl7ParseError("100", "empty message")
    if len(text.encode("utf-8", "ignore")) > MAX_MESSAGE_BYTES:
        raise Hl7ParseError("102", "message too large")
    text = text.replace("\x0b", "").replace("\x1c", "").lstrip("﻿ \t\r\n")
    lines = [ln for ln in re.split(r"\r\n|\r|\n", text) if ln.strip("\x00 ") != ""]
    if not lines:
        raise Hl7ParseError("100", "empty message")
    if len(lines) > MAX_SEGMENTS:
        raise Hl7ParseError("102", "too many segments")
    d = Delimiters.from_msh(lines[0])
    segments: list[Segment] = []
    for i, line in enumerate(lines):
        name = line[:3]
        if not _SEGMENT_NAME.match(name) or (len(line) > 3 and line[3] != d.field):
            raise Hl7ParseError("100", "segment is not well formed")
        if name == "MSH" and i > 0:
            raise Hl7ParseError("100", "a second MSH segment (batches are not supported)")
        parts = line.split(d.field)
        fields = (d.field, *parts[1:]) if name == "MSH" else tuple(parts[1:])
        segments.append(Segment(name, fields, d))
    msg = Message(tuple(segments), d)
    if not msg.msh.get(9, 1) or not msg.msh.get(10):
        raise Hl7ParseError("101", "MSH-9 or MSH-10 is missing")
    return msg


# ---- timestamps -------------------------------------------------------------------------------------------------

_TS = re.compile(r"^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:\.\d{1,4})?([+-]\d{4})?$")


def parse_ts(value: str) -> tuple[datetime | None, str | None]:
    """An HL7 DTM/TS value as `(datetime, precision)`; precision is `date` or `datetime`. The datetime is naive
    (wall-clock) unless the value carries an offset. `(None, None)` for an empty or invalid value."""
    value = (value or "").strip()
    m = _TS.match(value)
    if not m:
        return None, None
    year, month, day, hour, minute, second, offset = m.groups()
    try:
        from datetime import timedelta, timezone

        tz = None
        if offset:
            sign = 1 if offset[0] == "+" else -1
            tz = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
        when = datetime(int(year), int(month or 1), int(day or 1), int(hour or 0), int(minute or 0), int(second or 0), tzinfo=tz)
    except ValueError:
        return None, None
    return when, ("datetime" if hour else "date")


# ---- writing ----------------------------------------------------------------------------------------------------

CR = "\r"
ERROR_TEXT = {  # HL70357, the codes this adapter uses, with fixed texts
    "100": "Segment sequence error",
    "101": "Required field missing",
    "102": "Data type error",
    "103": "Table value not found",
    "200": "Unsupported message type",
    "201": "Unsupported event code",
    "203": "Unsupported version id",
    "204": "Unknown key identifier",
    "207": "Application internal error",
}


def build_ack(msg: Message | None, code: str, error_code: str | None = None, reason: str | None = None,
              now: datetime | None = None) -> str:
    """An ACK for `msg` (or for nothing readable: `msg=None`). `code` is AA, AE or AR. `reason` is a fixed phrase chosen by
    the caller (never message content); it is written to MSA-3, and with AE or AR to an ERR segment too. `now` is MSH-7: the
    engine's simulation clock from the handler and the router (the API reads no wall clock); empty when the caller has none."""
    if code not in ("AA", "AE", "AR"):
        raise ValueError("ACK code must be AA, AE or AR")
    d = msg.d if msg is not None else Delimiters()
    stamp = now.astimezone(UTC).strftime("%Y%m%d%H%M%S") if now is not None else ""  # no wall clock in here: the caller supplies it
    f, c = d.field, d.component

    def esc(v: str) -> str:
        return escape(v, d)

    orig_app = msg.msh.get(3, 1) if msg else ""  # the ACK goes back to whoever sent the message
    orig_fac = msg.msh.get(4, 1) if msg else ""
    trigger = msg.message_type[1] if msg else ""
    msh = f.join(["MSH", d.component + d.repetition + d.escape + d.subcomponent, esc("screening-closure"), esc("hl7-adapter"),
                  esc(orig_app), esc(orig_fac), stamp, "", f"ACK{c}{esc(trigger)}{c}ACK", uuid.uuid4().hex[:20], "P", "2.5.1"])
    control = esc(msg.control_id) if msg else ""
    msa = f.join(["MSA", code, control] + ([esc(reason)] if reason else []))
    segments = [msh, msa]
    if code != "AA":
        ec = error_code or "207"
        text = reason or ERROR_TEXT.get(ec, "Error")
        segments.append(f.join(["ERR", "", "", f"{ec}{c}{esc(ERROR_TEXT.get(ec, 'Error'))}{c}HL70357", "E", "", "", "", esc(text)]))
    return CR.join(segments) + CR


def mllp_frame(text: str) -> bytes:
    return MLLP_START + text.encode("utf-8") + MLLP_END


def mllp_unframe(data: bytes) -> str:
    if data.startswith(MLLP_START):
        data = data[1:]
    if data.endswith(MLLP_END):
        data = data[:-2]
    return data.decode("utf-8")
