"""Recursive-descent parser for the ``.pat`` pattern format.

Grammar (one statement per line)::

    pattern  NAME;
    pins     CLK, RSTN, EN, Q[0:7];
    timing   tset_fast;
    levels   lv_nominal;
    include  other.pat;

    label start:
      repeat 3 [tset_fast] CLK=C RSTN=1 EN=1 Q=LLLLLLLH;
      loop 10 {
          [tset_fast] CLK=C RSTN=1 EN=1 Q=XXXXXXXX;
      }
      jump start;
      halt;

State characters: ``0``/``1`` drive, ``L``/``H`` compare, ``X`` don't care,
``Z`` high impedance, ``C`` clock pulse.  For a bus the leftmost character is
the lowest index (``Q=LLLLLLLH`` means ``Q7=1`` for an 8-bit bus).
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from .model import (
    HaltOp,
    Instruction,
    JumpOp,
    LabelOp,
    LoopEndOp,
    LoopStartOp,
    ParseError,
    Pattern,
    Vector,
    VectorOp,
)

__all__ = ["parse_pattern_text", "parse_pattern_file", "load_pattern"]

_ASSIGN_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)(?:\[\s*\d+\s*(?::\s*\d+\s*)?\])?\s*=\s*([0-9LHXZC]+)",
    re.IGNORECASE,
)
_PIN_DECL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+):(\d+)\])?$")
_TSET_RE = re.compile(r"^\[([A-Za-z_][A-Za-z0-9_]*)\]\s*(.*)$")
_VALID_STATES = set("01LHXZC")
_MAX_INCLUDE_DEPTH = 8


class _Statement:
    __slots__ = ("text", "line", "source")

    def __init__(self, text: str, line: int, source: str) -> None:
        self.text = text
        self.line = line
        self.source = source


def _strip_comment(line: str) -> str:
    """Remove ``#`` and ``//`` comments, honouring quotes."""
    out: list[str] = []
    in_quote: str | None = None
    index = 0
    while index < len(line):
        char = line[index]
        if in_quote:
            out.append(char)
            if char == in_quote:
                in_quote = None
            index += 1
            continue
        if char in "\"'":
            in_quote = char
            out.append(char)
            index += 1
            continue
        if char == "#":
            break
        if char == "/" and index + 1 < len(line) and line[index + 1] == "/":
            break
        out.append(char)
        index += 1
    return "".join(out)


class _Loader:
    """Turns files into a flat statement stream (expanding ``include``)."""

    def __init__(self) -> None:
        self.statements: list[_Statement] = []
        self.includes: list[str] = []

    def add_file(self, path: Path, depth: int = 0) -> None:
        if depth > _MAX_INCLUDE_DEPTH:
            raise ParseError("include nesting too deep", source=str(path))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ParseError(f"cannot read file: {exc}", source=str(path)) from exc
        self.includes.append(str(path))
        base = path.parent
        for number, raw in enumerate(text.splitlines(), start=1):
            line = _strip_comment(raw).strip()
            if not line:
                continue
            lower = line.lower()
            if lower.startswith("include"):
                match = re.match(r"include\s+[\"']?([^\"';]+)[\"']?\s*;?\s*$", line)
                if not match:
                    raise ParseError("malformed include statement", number, str(path))
                target = match.group(1).strip()
                candidate = Path(target)
                if not candidate.is_absolute():
                    candidate = base / candidate
                self.add_file(candidate.resolve(), depth + 1)
                continue
            # split braces so "loop 3 {" and "}" work regardless of formatting
            if line.endswith("{") and line != "{":
                self.statements.append(_Statement(line[:-1].strip(), number, str(path)))
                self.statements.append(_Statement("{", number, str(path)))
                continue
            if line.startswith("}") and line != "}":
                self.statements.append(_Statement("}", number, str(path)))
                rest = line[1:].strip()
                if rest:
                    self.statements.append(_Statement(rest, number, str(path)))
                continue
            self.statements.append(_Statement(line, number, str(path)))


class _Parser:
    def __init__(self, statements: list[_Statement], name: str | None,
                 source: str | None) -> None:
        self.statements = statements
        self.index = 0
        self.name = name or "pattern"
        self.source = source
        self.pin_widths: dict[str, int] = {}
        self.pin_order: list[str] = []
        self.ops: list[Instruction] = []
        self.labels: dict[str, int] = {}
        self.timing: str | None = None
        self.levels: str | None = None

    # ---------------------------------------------------------------- helpers
    def _peek(self) -> _Statement | None:
        return self.statements[self.index] if self.index < len(self.statements) else None

    def _next(self) -> _Statement:
        if self.index >= len(self.statements):
            raise ParseError("unexpected end of pattern", None, self.source)
        stmt = self.statements[self.index]
        self.index += 1
        return stmt

    def _parse_pins(self, text: str, stmt: _Statement) -> None:
        body = text[len("pins"):].strip().rstrip(";")
        for item in body.split(","):
            token = item.strip()
            if not token:
                continue
            match = _PIN_DECL_RE.match(token)
            if not match:
                raise ParseError(f"bad pin declaration '{token}'", stmt.line, stmt.source)
            base, lo_s, hi_s = match.groups()
            if lo_s is None:
                width = 1
            else:
                lo, hi = int(lo_s), int(hi_s)
                if hi < lo:
                    raise ParseError(f"pin '{token}': high index below low index", stmt.line, stmt.source)
                width = hi - lo + 1
            if base in self.pin_widths:
                raise ParseError(f"pin '{base}' declared twice", stmt.line, stmt.source)
            self.pin_widths[base] = width
            self.pin_order.extend(base if width == 1 else f"{base}{i}" for i in range(width))

    def _parse_vector(self, text: str, stmt: _Statement, label: str | None = None) -> Vector:
        tset = None
        body = text.strip()
        match = _TSET_RE.match(body)
        if match:
            tset = match.group(1)
            body = match.group(2).strip()
        if body.endswith(";"):
            body = body[:-1].strip()
        assignments = list(_ASSIGN_RE.finditer(body))
        if not assignments:
            raise ParseError(f"no pin assignments in vector '{text.strip()}'", stmt.line, stmt.source)
        cleaned = _ASSIGN_RE.sub("", body).strip()
        if cleaned:
            raise ParseError(f"unexpected text '{cleaned}' in vector", stmt.line, stmt.source)
        states: dict[str, str] = {}
        for match in assignments:
            base, raw = match.group(1), match.group(2).upper()
            names = self._resolve_pin_names(base, stmt)
            invalid = set(raw) - _VALID_STATES
            if invalid:
                raise ParseError(
                    f"pin '{base}': invalid state character(s) {sorted(invalid)}",
                    stmt.line, stmt.source,
                )
            if len(raw) != len(names):
                raise ParseError(
                    f"pin '{base}': expected {len(names)} state character(s), got {len(raw)}",
                    stmt.line, stmt.source,
                )
            for name, char in zip(names, raw):
                if name in states:
                    raise ParseError(f"pin '{name}' assigned twice", stmt.line, stmt.source)
                states[name] = char
        return Vector(states=states, tset=tset, line=stmt.line, label=label)

    def _resolve_pin_names(self, base: str, stmt: _Statement) -> list[str]:
        """Expand an assignment target: declared bus or ``Q0..Q7`` aggregate."""
        width = self.pin_widths.get(base)
        if width is not None:
            return [base] if width == 1 else [f"{base}{index}" for index in range(width)]
        count = 0
        while f"{base}{count}" in self.pin_widths:
            count += 1
        if count:
            return [f"{base}{index}" for index in range(count)]
        raise ParseError(f"pin '{base}' not declared in 'pins'", stmt.line, stmt.source)

    # ----------------------------------------------------------------- blocks
    def parse_block(self, *, top: bool = False) -> list[Instruction]:
        block: list[Instruction] = []
        while True:
            stmt = self._peek()
            if stmt is None:
                if top:
                    return block
                raise ParseError("missing '}'", None, self.source)
            text = stmt.text.strip()
            if text == "}":
                if top:
                    raise ParseError("unexpected '}'", stmt.line, stmt.source)
                self._next()
                return block
            if text == "{":
                raise ParseError("unexpected '{'", stmt.line, stmt.source)
            self._next()
            lower = text.lower()
            if lower.startswith("pattern "):
                self.name = text.split(None, 1)[1].strip().rstrip(";")
            elif lower.startswith("pins "):
                self._parse_pins(text, stmt)
            elif lower.startswith("timing "):
                self.timing = text.split(None, 1)[1].strip().rstrip(";")
            elif lower.startswith("levels "):
                self.levels = text.split(None, 1)[1].strip().rstrip(";")
            elif lower.startswith("label "):
                label = text[len("label "):].strip().rstrip(":").strip()
                if not label:
                    raise ParseError("empty label name", stmt.line, stmt.source)
                if label in self.labels:
                    raise ParseError(f"duplicate label '{label}'", stmt.line, stmt.source)
                self.labels[label] = -1
                block.append(LabelOp(label))
            elif lower.startswith("jump "):
                target = text[len("jump "):].strip().rstrip(";")
                if not target:
                    raise ParseError("jump needs a target label", stmt.line, stmt.source)
                block.append(JumpOp(target))
            elif lower.startswith("halt"):
                block.append(HaltOp())
            elif lower.startswith("repeat ") or lower.startswith("loop "):
                keyword, rest = text.split(None, 1)
                match = re.match(r"(\d+)\s*(.*)$", rest.strip())
                if not match:
                    raise ParseError(f"{keyword} needs a count", stmt.line, stmt.source)
                count = int(match.group(1))
                remainder = match.group(2).strip()
                if count <= 0:
                    raise ParseError(f"{keyword} count must be >= 1", stmt.line, stmt.source)
                block.append(LoopStartOp(count))
                if remainder.startswith("{"):
                    block.extend(self.parse_block())
                elif remainder:
                    block.append(VectorOp(self._parse_vector(remainder, stmt)))
                else:
                    following = self._peek()
                    if following is not None and following.text.strip() == "{":
                        self._next()
                        block.extend(self.parse_block())
                    else:
                        nested = self._next()
                        block.append(VectorOp(self._parse_vector(nested.text, nested)))
                block.append(LoopEndOp(-1))
            elif lower.startswith("capture"):
                raise ParseError("'capture' is not supported in this version", stmt.line, stmt.source)
            else:
                block.append(VectorOp(self._parse_vector(text, stmt)))
        return block


def _flatten(block: list[Instruction]) -> list[Instruction]:
    """Blocks are already flat; kept as an explicit hook for future nodes."""
    return list(block)


def _patch_loops(program: list[Instruction]) -> list[Instruction]:
    """Pair ``LoopStartOp``/``LoopEndOp`` markers (properly nested)."""
    out = list(program)
    stack: list[int] = []
    for index, op in enumerate(out):
        if isinstance(op, LoopStartOp):
            stack.append(index)
        elif isinstance(op, LoopEndOp):
            if not stack:
                raise ParseError("unmatched '}'")
            start = stack.pop()
            out[start] = replace(out[start], end_pc=index + 1)
            out[index] = LoopEndOp(start)
    if stack:
        raise ParseError("unterminated loop")
    return out


def _collect_labels(program: list[Instruction], parser: _Parser) -> dict[str, int]:
    """Map labels to their pc and verify every jump target exists."""
    labels: dict[str, int] = {}
    for index, op in enumerate(program):
        if isinstance(op, LabelOp):
            if op.name in labels:
                raise ParseError(f"duplicate label '{op.name}'", None, parser.source)
            labels[op.name] = index
    for op in program:
        if isinstance(op, JumpOp) and op.target not in labels:
            raise ParseError(f"unknown jump label '{op.target}'", None, parser.source)
    parser.labels = dict(labels)
    return labels


def parse_pattern_text(text: str, *, name: str | None = None,
                       source: str | None = "<text>") -> Pattern:
    """Parse pattern source text."""
    loader = _Loader()
    stmts: list[_Statement] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.endswith("{") and line != "{":
            stmts.append(_Statement(line[:-1].strip(), number, source or "<text>"))
            stmts.append(_Statement("{", number, source or "<text>"))
            continue
        if line.startswith("}") and line != "}":
            stmts.append(_Statement("}", number, source or "<text>"))
            rest = line[1:].strip()
            if rest:
                stmts.append(_Statement(rest, number, source or "<text>"))
            continue
        stmts.append(_Statement(line, number, source or "<text>"))
    parser = _Parser(stmts, name, source)
    program = parser.parse_block(top=True)
    program = _patch_loops(program)
    labels = _collect_labels(program, parser)
    pattern = Pattern(
        name=name or parser.name,
        pins=list(parser.pin_order),
        program=program,
        labels=dict(labels),
        timing=parser.timing,
        levels=parser.levels,
        source=source,
        includes=loader.includes,
    )
    if not pattern.pins:
        raise ParseError("pattern declares no pins", None, source)
    return pattern


def parse_pattern_file(path: str | Path) -> Pattern:
    """Parse a ``.pat`` file, expanding includes recursively."""
    file_path = Path(path).resolve()
    loader = _Loader()
    loader.add_file(file_path)
    parser = _Parser(loader.statements, None, str(file_path))
    program = parser.parse_block(top=True)
    program = _patch_loops(program)
    labels = _collect_labels(program, parser)
    pattern = Pattern(
        name=parser.name,
        pins=list(parser.pin_order),
        program=program,
        labels=dict(labels),
        timing=parser.timing,
        levels=parser.levels,
        source=str(file_path),
        includes=[item for item in loader.includes if item != str(file_path)],
    )
    if not pattern.pins:
        raise ParseError("pattern declares no pins", None, str(file_path))
    return pattern


def load_pattern(source: Pattern | str | Path | Iterable[str], *,
                 name: str | None = None) -> Pattern:
    """Accept a :class:`Pattern`, a path, pattern text or an iterable of lines."""
    if isinstance(source, Pattern):
        return source
    if isinstance(source, Path):
        return parse_pattern_file(source)
    if isinstance(source, str):
        candidate = Path(source)
        if "\n" not in source and candidate.suffix.lower() == ".pat":
            return parse_pattern_file(candidate)
        return parse_pattern_text(source, name=name)
    return parse_pattern_text("\n".join(source), name=name)
