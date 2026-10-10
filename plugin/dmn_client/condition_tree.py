"""Explicit v2 condition groups; legacy v1 remains a flat conjunction."""


def leaves(conditions, profile):
    if profile == "service-decision-table-v1":
        return list(conditions)
    if profile != "service-decision-table-v2":
        raise ValueError("Unsupported condition profile")
    result = []
    count = 0

    def visit(node, depth):
        nonlocal count
        count += 1
        if depth > 8 or count > 64:
            raise ValueError("Condition tree exceeds depth/node budget")
        if "path" in node:
            result.append(node)
            if len(result) > 32:
                raise ValueError("Condition tree exceeds 32 leaves")
        else:
            key = "all" if "all" in node else "any"
            for child in node[key]:
                visit(child, depth + 1)

    for node in conditions:
        visit(node, 0)
    return result


def combine(states, mode):
    if mode == "any":
        return "TRUE" if "TRUE" in states else "UNKNOWN" if "UNKNOWN" in states else "FALSE"
    return "FALSE" if "FALSE" in states else "UNKNOWN" if "UNKNOWN" in states else "TRUE"


def evaluate(conditions, predicate):
    def visit(node):
        if "path" in node:
            return predicate(node)
        key = "all" if "all" in node else "any"
        return combine([visit(child) for child in node[key]], key)

    return combine([visit(node) for node in conditions], "all")
