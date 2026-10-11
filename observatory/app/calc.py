# -*- coding: utf-8 -*-
"""calculate: arithmetic for the writing assistant, done by code and shown
with its formula.

A model doing sums in its head slips: a growth rate a tenth out, a share of
the wrong total. Every number a run puts in a draft that no tool returned as
is (a change, a ratio, a share, a difference) is meant to come from here, so
the figure is computed exactly and the writer can see how it was got.

The expression is parsed, never run: numbers, the named values the model
passes in, + - * / ^ and brackets, and a short list of functions. Nothing
else is accepted. All arithmetic is in floating point, powers are bounded,
and a result that is not a finite number is refused rather than shown.
"""
import ast
import json
import math

EXPR_MAX = 400
NODES_MAX = 200
VALUES_MAX = 40
LIST_MAX = 1000
POW_MAX = 1000.0


class CalcError(ValueError):
    pass


def _mean(xs):
    return sum(xs) / len(xs)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _round(x, n=0.0):
    return round(x, int(n))


# name: (function, takes a list, smallest and largest argument count)
FUNCS = {
    "abs": (abs, False, 1, 1),
    "round": (_round, False, 1, 2),
    "sqrt": (math.sqrt, False, 1, 1),
    "ln": (math.log, False, 1, 1),
    "log10": (math.log10, False, 1, 1),
    "exp": (math.exp, False, 1, 1),
    "min": (min, True, 1, LIST_MAX),
    "max": (max, True, 1, LIST_MAX),
    "sum": (sum, True, 1, LIST_MAX),
    "mean": (_mean, True, 1, LIST_MAX),
    "median": (_median, True, 1, LIST_MAX),
    "count": (len, True, 1, LIST_MAX),
}

_OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}


def _number(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise CalcError("%s is not a number." % name)
    v = float(v)
    if not math.isfinite(v):
        raise CalcError("%s is not a finite number." % name)
    return v


def _values(values):
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise CalcError("values must be an object of name: number.")
    if len(values) > VALUES_MAX:
        raise CalcError("At most %d named values." % VALUES_MAX)
    out = {}
    for k, v in values.items():
        k = str(k)
        if not k.isidentifier() or k in FUNCS:
            raise CalcError("'%s' cannot be a value's name: use letters, digits and _, "
                            "and not a function's name." % k)
        if isinstance(v, list):
            if not v or len(v) > LIST_MAX:
                raise CalcError("The list %s needs 1 to %d numbers." % (k, LIST_MAX))
            out[k] = [_number(x, "An item of %s" % k) for x in v]
        else:
            out[k] = _number(v, k)
    return out


def _eval(node, env):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant):
        return _number(node.value, repr(node.value))
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise CalcError("'%s' is not given in values." % node.id)
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval(node.operand, env)
        if isinstance(v, list):
            raise CalcError("A list can only go inside sum, mean, median, min, max or count.")
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and (type(node.op) in _OPS or isinstance(node.op, ast.Pow)):
        a, b = _eval(node.left, env), _eval(node.right, env)
        if isinstance(a, list) or isinstance(b, list):
            raise CalcError("A list can only go inside sum, mean, median, min, max or count.")
        if isinstance(node.op, ast.Pow):
            if abs(b) > POW_MAX:
                raise CalcError("That power is too large.")
            if a < 0 and b != int(b):
                raise CalcError("A negative number cannot be raised to a fractional power.")
            try:
                return math.pow(a, b)
            except (OverflowError, ValueError):
                raise CalcError("That power is too large.")
        if isinstance(node.op, ast.Div) and b == 0:
            raise CalcError("Division by zero.")
        return _OPS[type(node.op)](a, b)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        name = node.func.id
        if name not in FUNCS:
            raise CalcError("No function called %s. The functions are: %s."
                            % (name, ", ".join(sorted(FUNCS))))
        fn, takes_list, lo, hi = FUNCS[name]
        args = [_eval(a, env) for a in node.args]
        if takes_list:
            flat = []
            for a in args:
                flat.extend(a if isinstance(a, list) else [a])
            args = flat
        elif any(isinstance(a, list) for a in args):
            raise CalcError("%s takes numbers, not a list." % name)
        if not lo <= len(args) <= hi:
            raise CalcError("%s needs %s." % (name, "one number" if hi == 1 else
                                                "%d to %d numbers" % (lo, hi)))
        try:
            return float(fn(args) if takes_list else fn(*args))
        except (ValueError, OverflowError):
            raise CalcError("%s cannot take that number." % name)
    raise CalcError("Only numbers, named values, + - * / ^, brackets and the functions %s "
                    "are allowed." % ", ".join(sorted(FUNCS)))


class _Fill(ast.NodeTransformer):
    """The expression with each name replaced by its value, for display."""

    def __init__(self, env):
        self.env = env

    def visit_Name(self, node):
        v = self.env.get(node.id)
        if isinstance(v, float):
            return ast.copy_location(ast.Constant(_plain(v)), node)
        return node


def _plain(v):
    """A float as people write it: 105.2, not 105.2000000001; 3, not 3.0."""
    if v == int(v) and abs(v) < 1e15:
        return int(v)
    return float("%.12g" % v)


def calculate(expression, values=None, decimals=None):
    expr = str(expression or "").strip()
    if not expr:
        raise CalcError("Give an expression, e.g. (a / b - 1) * 100.")
    if len(expr) > EXPR_MAX:
        raise CalcError("Keep the expression under %d characters." % EXPR_MAX)
    env = _values(values)
    try:
        tree = ast.parse(expr.replace("^", "**").replace("×", "*").replace("÷", "/")
                         .replace("−", "-"), mode="eval")
    except SyntaxError:
        raise CalcError("That expression could not be read. Write it like (a / b - 1) * 100.")
    if sum(1 for _ in ast.walk(tree)) > NODES_MAX:
        raise CalcError("That expression is too long; split it into steps.")
    result = _eval(tree, env)
    if isinstance(result, list):
        raise CalcError("The answer is a list; wrap it in sum, mean, median, min, max or count.")
    if not math.isfinite(result):
        raise CalcError("The answer is not a finite number.")
    out = {"expression": expr, "result": _plain(result)}
    filled = ast.unparse(_Fill(env).visit(tree)).replace("**", "^")
    if env:
        out["with_values"] = filled + " = " + _fmt(result, decimals)
    if decimals is not None:
        try:
            d = max(0, min(10, int(decimals)))
        except (TypeError, ValueError):
            raise CalcError("decimals must be a whole number.")
        out["rounded"] = _fmt(result, d)
    return out


def _fmt(v, decimals=None):
    if decimals is None:
        return str(_plain(v))
    return "%.*f" % (max(0, min(10, int(decimals))), v)


TOOL = {
    "name": "calculate",
    "description": "Do arithmetic exactly, with code, and get the formula back. Use it for every "
                   "number you put in the draft that no tool returned as is (a change, a growth "
                   "rate, a ratio, a share, a difference, an average), never mental arithmetic. "
                   "Name the inputs in values and refer to them in the expression, e.g. "
                   "expression '(now / year_ago - 1) * 100', values {\"now\": 112.4, "
                   "\"year_ago\": 108.9}. Allowed: + - * / ^, brackets and the functions "
                   + ", ".join(sorted(FUNCS)) + " (the list ones take a list value). "
                   "A difference between two percentages is in pp, not %.",
    "parameters": {"type": "object", "properties": {
        "expression": {"type": "string"},
        "values": {"type": "object", "description": "name: number, or name: list of numbers.",
                   "additionalProperties": {}},
        "decimals": {"type": "integer", "description": "Optional: also give the answer rounded "
                                                       "to this many decimal places."}},
        "required": ["expression"]},
}


def run_tool(args):
    """(text, is_error) for one calculate call."""
    args = args if isinstance(args, dict) else {}
    try:
        out = calculate(args.get("expression"), args.get("values"), args.get("decimals"))
    except CalcError as exc:
        return json.dumps({"error": str(exc)}), True
    return json.dumps(out, ensure_ascii=False), False
