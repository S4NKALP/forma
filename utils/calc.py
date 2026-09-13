"""Safe arithmetic evaluator for the launcher calc row — port of ukishima
lib/calc.js. Recursive descent over a tiny grammar (+ - * / ^ parens decimals,
postfix % = /100). Never evals, so a typed query cannot run code. evaluate()
returns (ok, value, display) with ok False unless the query is a real
calculation (at least one operation).
"""

import math
import re

_NUM_RE = re.compile(r"^[0-9]*\.?[0-9]+")


def _tokenize(src: str) -> list | None:
    tokens = []
    i = 0
    while i < len(src):
        c = src[i]
        if c in " \t":
            i += 1
            continue
        if c.isdigit() or c == ".":
            j = i
            dots = 0
            while j < len(src) and (src[j].isdigit() or src[j] == "."):
                if src[j] == ".":
                    dots += 1
                j += 1
            if dots > 1:
                return None
            tokens.append({"t": "num", "v": float(src[i:j])})
            i = j
            continue
        if c in "+-*/^%()":
            tokens.append({"t": c})
            i += 1
            continue
        return None
    return tokens


class _Parser:
    __slots__ = ("ops", "pos", "tokens")

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0
        self.ops = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _expr(self):
        v = self._term()
        while True:
            t = self.peek()
            if t and t["t"] == "+":
                self.pos += 1
                v = v + self._term()
                self.ops += 1
            elif t and t["t"] == "-":
                self.pos += 1
                v = v - self._term()
                self.ops += 1
            else:
                break
        return v

    def _term(self):
        v = self._power()
        while True:
            t = self.peek()
            if t and t["t"] == "*":
                self.pos += 1
                v = v * self._power()
                self.ops += 1
            elif t and t["t"] == "/":
                self.pos += 1
                v = v / self._power()
                self.ops += 1
            else:
                break
        return v

    def _power(self):
        base = self._unary()
        t = self.peek()
        if t and t["t"] == "^":
            self.pos += 1
            self.ops += 1
            return math.pow(base, self._power())
        return base

    def _unary(self):
        t = self.peek()
        if t and t["t"] == "-":
            self.pos += 1
            return -self._unary()
        if t and t["t"] == "+":
            self.pos += 1
            return self._unary()
        return self._postfix()

    def _postfix(self):
        v = self._primary()
        t = self.peek()
        if t and t["t"] == "%":
            self.pos += 1
            self.ops += 1
            v = v / 100
        return v

    def _primary(self):
        t = self.peek()
        if not t:
            raise ValueError("eof")
        if t["t"] == "num":
            self.pos += 1
            return t["v"]
        if t["t"] == "(":
            self.pos += 1
            v = self._expr()
            if not (self.peek() and self.peek()["t"] == ")"):
                raise ValueError("paren")
            self.pos += 1
            return v
        raise ValueError("unexpected")


def evaluate(src: str):
    fail = {"ok": False, "value": float("nan"), "display": ""}
    if not src or not src.strip():
        return fail
    tokens = _tokenize(src)
    if not tokens:
        return fail

    p = _Parser(tokens)
    try:
        value = p._expr()
    except ValueError, ZeroDivisionError, OverflowError:
        return fail
    if p.pos != len(tokens):
        return fail
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return fail
    if p.ops < 1:
        return fail

    rounded = float(f"{value:.12g}")
    if rounded == 0:
        rounded = 0
    display = str(int(rounded)) if rounded.is_integer() else str(rounded)
    return {"ok": True, "value": rounded, "display": display}
