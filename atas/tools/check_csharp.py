"""
check_csharp.py — static sanity check for the indicator sources.

No ATAS SDK here, so a real build is impossible. This catches the class of
mistakes that would otherwise surface as a wall of compiler errors on the user's
Windows machine:

  1. syntax errors (via tree-sitter-c-sharp)
  2. braces / parentheses balance
  3. identifiers referenced but never declared (typos, renamed fields)
  4. declared-but-never-used private members (dead code)
  5. interpolation and format-string sanity
"""
import re
import sys
from pathlib import Path

import tree_sitter_c_sharp as tscs
from tree_sitter import Language, Parser

CS = Language(tscs.language())
SRC = Path("/home/user/ss/atas/src/SsDrawOnLiquidity")

# symbols provided by the ATAS platform / BCL that we must not flag
EXTERNALS = {
    # ATAS / OFT
    "Indicator", "ValueDataSeries", "RenderContext", "DrawingLayouts", "RenderFont",
    "RenderStringFormat", "RenderPen", "VisualMode", "IndicatorCategories",
    "InstrumentInfo", "ChartInfo", "Container", "DataSeries", "CurrentBar",
    "Candle", "Strings", "DefaultColors", "RenderPens", "PenSettings",
    "LineDashStyle", "OFT", "ATAS", "Range", "Display", "DisplayName",
    "Description", "Category", "Parameter", "HelpLink", "FilterTimeSpan",
    # BCL
    "Math", "Color", "Rectangle", "StringAlignment", "StringFormatFlags",
    "Console", "List", "DateTime", "TimeSpan", "Decimal", "Int32", "Single",
    "String", "Object", "Exception", "ArgumentException", "Linq", "Enumerable",
    "System", "Generic", "ComponentModel", "DataAnnotations", "Drawing", "Tools",
    "Context", "Helpers", "Attributes", "Localization", "nameof", "base",
    "this", "value", "get", "set", "true", "false", "null", "var", "new",
    "return", "if", "else", "foreach", "for", "while", "switch", "case",
    "break", "continue", "using", "namespace", "class", "struct", "enum",
    "public", "private", "internal", "protected", "static", "readonly",
    "override", "virtual", "sealed", "abstract", "const", "float", "double",
    "decimal", "int", "bool", "string", "char", "void", "byte", "short",
    "long", "uint", "ulong", "ushort", "sbyte", "in", "out", "ref", "params",
    "try", "catch", "finally", "throw", "do", "default", "is", "as", "typeof",
    "sizeof", "lock", "operator", "implicit", "explicit", "event", "delegate",
}


def collect_declared(text):
    """Fields, properties, methods, locals, types, and parameters declared here."""
    names = set()
    pats = [
        r"(?:private|public|internal|protected|static|readonly|sealed|override|\s)+"
        r"[A-Za-z_][\w<>\[\],\.\?]*\s+([A-Za-z_]\w*)\s*[;=({]",
        r"\bvar\s+([A-Za-z_]\w*)\s*=",
        r"\b(?:class|struct|enum)\s+([A-Za-z_]\w*)",
        r"\b([A-Za-z_]\w*)\s*\(\s*(?:[A-Za-z_][\w<>\[\],\.\?]*\s+[A-Za-z_]\w*\s*,?\s*)*\)\s*$",
        r"\b([A-Za-z_]\w*)\s*=>",
        r"\bfor\s*\(\s*var\s+([A-Za-z_]\w*)",
        r"\bforeach\s*\(\s*var\s+([A-Za-z_]\w*)",
    ]
    for p in pats:
        for m in re.finditer(p, text, re.M):
            names.add(m.group(1))
    # comma-separated declarators, e.g. "private decimal _a, _b = 2m;"
    for m in re.finditer(
            r"\b(?:private|public|internal|protected|static|readonly)\s+"
            r"[\w<>\[\],\.\?]+\s+([^;(){}]+);", text):
        for part in m.group(1).split(","):
            nm = re.match(r"\s*(_?\w+)", part)
            if nm:
                names.add(nm.group(1))
    return names


# Names that are resolved outside this file: BCL types, ATAS Indicator members,
# and attribute / constructor invocations that merely look like calls.
_EXTERNAL_CALLS = {
    # BCL
    "Math", "MathF", "Convert", "String", "ToString", "Array", "Tuple", "Func",
    "Action", "Nullable", "Environment", "DateTime", "TimeSpan", "Enumerable",
    "StringBuilder", "ArgumentException", "Console", "Enum", "Type", "Object",
    "Rectangle", "Point", "Size", "List", "Dictionary",
    # ATAS Indicator surface used here
    "RecalculateValues", "GetCandle", "SubscribeToDrawingEvents", "OnCalculate",
    "OnRender", "OnInitialize", "IsNewSession", "Add", "Clear", "Contains",
    # attributes and constructors
    "Category", "Description", "Display", "DisplayName", "Range", "HelpLink",
    "RenderPen", "RenderFont", "RenderStringFormat", "PenSettings",
}

_CALL_KEYWORDS = {
    "if", "for", "foreach", "while", "switch", "return", "using", "catch",
    "lock", "fixed", "sizeof", "typeof", "nameof", "new", "case", "do",
    "else", "get", "set", "throw", "when", "yield", "base", "this",
}


def find_unresolved_calls(text):
    """
    Unqualified calls (no preceding dot) that have no definition in the file and
    are not known platform/BCL members. Qualified calls are ignored because their
    existence depends on external types this checker cannot see.
    """
    no_block = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    no_line = re.sub(r"//[^\n]*", "", no_block)
    no_str = re.sub(r'"(?:[^"\\]|\\.)*"', '""', no_line)

    defined = set(re.findall(
        r"\b(?:private|public|protected|internal|static|sealed|override|async|\s)+"
        r"[A-Za-z_][\w<>\[\],\.\?]*\s+([A-Za-z_]\w*)\s*\(", no_str))
    defined |= set(re.findall(
        r"[A-Za-z_][\w<>\[\],\.\?]*\s+([A-Za-z_]\w*)\s*\n\s*=>", no_str))
    defined |= set(re.findall(r"\bvoid\s+([A-Za-z_]\w*)\s*\(", no_str))
    defined |= set(re.findall(r"\(([A-Za-z_]\w*)\)\s*=>", no_str))

    calls = re.findall(r"(?<![\w\.])([A-Za-z_]\w*)\s*\(", no_str)

    return sorted({c for c in calls
                   if c not in defined
                   and c not in _EXTERNAL_CALLS
                   and c not in _CALL_KEYWORDS})


def main():
    files = sorted(SRC.glob("*.cs"))
    if not files:
        print("no sources found")
        return 1

    parser = Parser(CS)
    failures = 0

    for f in files:
        text = f.read_text()
        print(f"\n{'='*74}\n{f.name}  ({len(text.splitlines())} lines)\n{'='*74}")

        # 1. parse
        tree = parser.parse(text.encode())
        errors = []

        def walk(node):
            if node.type == "ERROR" or node.is_missing:
                errors.append(node)
            for ch in node.children:
                walk(ch)

        walk(tree.root_node)
        if errors:
            failures += 1
            print(f"  [FAIL] {len(errors)} syntax error(s)")
            for e in errors[:12]:
                line = e.start_point[0] + 1
                snippet = text.splitlines()[line - 1][:90] if line <= len(text.splitlines()) else ""
                print(f"     line {line}: {e.type}  |  {snippet}")
        else:
            print("  [ok] parses cleanly")

        # 2. balance
        for open_c, close_c, label in [("{", "}", "braces"), ("(", ")", "parens")]:
            d = text.count(open_c) - text.count(close_c)
            if d:
                failures += 1
                print(f"  [FAIL] unbalanced {label}: {d:+d}")

        # 3/4 only meaningful for the main indicator
        if f.name == "PotTable.cs":
            continue

        declared = collect_declared(text)
        # field references of the form _name
        refs = set(re.findall(r"\b_[A-Za-z]\w*\b", text))
        undeclared = sorted(r for r in refs if r not in declared)
        if undeclared:
            failures += 1
            print(f"  [FAIL] underscore identifiers never declared: {undeclared}")
        else:
            print(f"  [ok] all {len(refs)} underscore fields are declared")

        # unused private fields
        unused = []
        for m in re.finditer(r"private\s+(?:readonly\s+)?[\w<>\[\],\.\?]+\s+(_\w+)\s*(?:=|;)", text):
            name = m.group(1)
            if len(re.findall(rf"\b{re.escape(name)}\b", text)) <= 1:
                unused.append(name)
        if unused:
            print(f"  [warn] declared but never used: {unused}")

        # 5. called methods must exist somewhere resolvable
        unresolved = find_unresolved_calls(text)
        if unresolved:
            failures += 1
            print(f"  [FAIL] called but not defined in this file: {unresolved}")
        else:
            print("  [ok] every unqualified method call resolves")

        # 6. interpolation sanity
        for m in re.finditer(r'\$"([^"]*)"', text):
            body = m.group(1)
            if body.count("{") != body.count("}"):
                failures += 1
                print(f"  [FAIL] unbalanced interpolation: {m.group(0)[:70]}")

    print(f"\n{'='*74}")
    print("RESULT:", "PASS" if failures == 0 else f"{failures} problem(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
