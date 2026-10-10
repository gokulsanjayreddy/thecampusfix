"""The tools the model is allowed to ask for.

A tool is a plain Python function. Put @tool above it and the model can use it.
The tool's description comes from its docstring. Its inputs come from its type hints.
Type hints can be str, int, float or bool.

SAFETY: there is no shell tool, no network tool, and no file access outside notes/.
"""

import ast
import inspect
import operator
import typing
from datetime import datetime
from pathlib import Path

TOOLS = {}  # tool name -> {"func": the function, "declaration": what we tell the model}

_TYPE_NAMES = {str: "string", int: "integer", float: "number", bool: "boolean"}
NOTES_FILE = Path(__file__).resolve().parent.parent / "notes" / "notes.txt"


def tool(func):
    """Register a function as a tool the model can ask for."""
    hints = typing.get_type_hints(func)
    docstring = inspect.cleandoc(func.__doc__ or "")
    properties, required = {}, []
    for name, param in inspect.signature(func).parameters.items():
        py_type = hints.get(name, str)
        if py_type not in _TYPE_NAMES:
            raise TypeError(f"Tool '{func.__name__}': use str, int, float or bool for '{name}'.")
        # A docstring line like "expression: the sum to work out" describes that input.
        lines = [l.strip() for l in docstring.splitlines() if l.strip().startswith(name + ":")]
        description = lines[0].split(":", 1)[1].strip() if lines else name
        properties[name] = {"type": _TYPE_NAMES[py_type], "description": description}
        if param.default is inspect.Parameter.empty:
            required.append(name)

    declaration = {"name": func.__name__, "description": docstring.split("\n\n")[0].replace("\n", " ")}
    if properties:
        declaration["parameters"] = {"type": "object", "properties": properties, "required": required}
    TOOLS[func.__name__] = {"func": func, "declaration": declaration}
    return func


def get_declarations():
    return [t["declaration"] for t in TOOLS.values()]


def _convert(py_type, value):
    """The model sends numbers and text loosely. Turn each value into the type we expect."""
    if py_type is bool and isinstance(value, str):
        return value.strip().lower() == "true"
    return py_type(value)


def run_tool(name, args):
    """Run a tool by name. Always returns text. Problems come back as text starting with 'Error'."""
    if name not in TOOLS:
        return f"Error: there is no tool called '{name}'."
    func = TOOLS[name]["func"]
    hints = typing.get_type_hints(func)
    try:
        clean = {key: _convert(hints.get(key, str), value) for key, value in (args or {}).items()}
        return str(func(**clean))
    except Exception as error:
        return f"Error: {error}"


# ---------- calculator: safe maths, never eval() ----------

_OPERATORS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
}


def _calc(node):
    """Work out a maths expression. Allows numbers, + - * / ** % and brackets. Nothing else."""
    if isinstance(node, ast.Expression):
        return _calc(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _calc(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left, right = _calc(node.left), _calc(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("That power is too big.")
        result = _OPERATORS[type(node.op)](left, right)
        if isinstance(result, int) and result.bit_length() > 4000:
            raise ValueError("That number is too big.")
        return result
    raise ValueError("Only numbers and + - * / ** % ( ) are allowed.")


@tool
def calculator(expression: str):
    """Work out a maths sum such as (12 + 8) * 3. Allows numbers, + - * / ** % and brackets.

    expression: the sum to work out, for example 2 * (3 + 4)
    """
    if len(expression) > 200:
        raise ValueError("That sum is too long.")
    return _calc(ast.parse(expression.strip(), mode="eval"))


@tool
def get_time():
    """Get the current date and time on this computer."""
    return datetime.now().strftime("%A %d %B %Y, %H:%M")


@tool
def save_note(text: str):
    """Save a short note. The note is added to notes/notes.txt.

    text: the note to save
    """
    NOTES_FILE.parent.mkdir(exist_ok=True)
    with NOTES_FILE.open("a", encoding="utf-8") as file:
        file.write(text[:500].replace("\n", " ") + "\n")
    return "Saved the note."


# ---------- CampusFix read-only harness tools ----------

ISSUES_FILE = Path(__file__).resolve().parent.parent / "notes" / "issues.json"


def _read_campus_issues():
    """Read issues safely from notes/issues.json without writing or modifying."""
    if not ISSUES_FILE.exists():
        return []
    try:
        import json
        data = json.loads(ISSUES_FILE.read_text(encoding="utf-8"))
        return data.get("issues", []) if isinstance(data, dict) else []
    except Exception:
        return []


@tool
def list_top_issues(n: int):
    """List the highest priority open campus issues.

    n: number of top open issues to return (between 1 and 20)
    """
    issues = [i for i in _read_campus_issues() if i.get("status") == "open" and not i.get("needs_review")]
    if not issues:
        return "No open issues on the campus board."

    limit = max(1, min(int(n), 20))
    # Rank by severity base + confirmations
    sev_weights = {"critical": 100, "high": 60, "medium": 30, "low": 10}
    issues.sort(
        key=lambda x: -(sev_weights.get(x.get("severity", "medium"), 30) + x.get("confirmations", 0) * 2)
    )
    lines = []
    for itm in issues[:limit]:
        lines.append(
            f"#{itm.get('id')} [{itm.get('severity', '').upper()}] {itm.get('title')} ({itm.get('category')}) - {itm.get('location_hint')}"
        )
    result = "\n".join(lines)
    return result[:2000]


@tool
def count_by_category():
    """Count open campus issues grouped by category."""
    issues = [i for i in _read_campus_issues() if i.get("status") == "open"]
    if not issues:
        return "No open issues on the campus board."
    counts = {}
    for i in issues:
        cat = i.get("category", "Other")
        counts[cat] = counts.get(cat, 0) + 1
    lines = [f"{cat}: {cnt}" for cat, cnt in sorted(counts.items(), key=lambda x: -x[1])]
    result = "\n".join(lines)
    return result[:2000]


@tool
def find_issues(keyword: str):
    """Find campus issues matching a search keyword in title, summary, or location.

    keyword: search word or phrase to look for
    """
    key = str(keyword).strip().lower()
    if not key:
        return "Please provide a keyword to search."
    issues = _read_campus_issues()
    matches = []
    for i in issues:
        blob = f"{i.get('title', '')} {i.get('summary', '')} {i.get('text', '')} {i.get('location_hint', '')}".lower()
        if key in blob:
            status_tag = "OPEN" if i.get("status") == "open" else "RESOLVED"
            matches.append(
                f"#{i.get('id')} [{status_tag} | {i.get('severity', '').upper()}] {i.get('title')} at {i.get('location_hint')}"
            )
    if not matches:
        return f"No campus issues found matching '{keyword}'."
    result = "\n".join(matches[:15])
    return result[:2000]
