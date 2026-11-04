"""Policy table names. 0.5.20 (存表名即标准名): the five standard names are the storage names; the old names are
transitional read-only aliases of them. One mapping; both directions are derived from it."""

# 0.5.20 (存表名即标准名): standard name → transitional old name (0.5.13–0.5.19 stored the tables under the old names)
STANDARD = {
    "target.scene.entity": "target.scene_entity",
    "triage.scene.acceptance": "triage.acceptance",
    "triage.supplement.requirements": "supplement.requirements",
    "triage.dispatch.route": "dispatch.route",
    "triage.dispatch.resource": "dispatch.resource",
}
ALIASES = {
    old: new for new, old in STANDARD.items()
}  # old name → the standard name it is an alias of


def storage_name(table):
    """The standard name of a table: an old name's standard name, any other name itself. Two names are one table when
    their storage names are equal."""
    return ALIASES.get(table, table) if isinstance(table, str) else table


def alias_of(table):
    """The standard name an old name is an alias of, or None."""
    return ALIASES.get(table) if isinstance(table, str) else None


def old_name(table):
    """The transitional old name of a standard name, or None."""
    return STANDARD.get(table) if isinstance(table, str) else None


def format_name(table):
    """The name whose format and owner rules a table name follows: a standard name its old name's, any other itself."""
    return STANDARD.get(table, table) if isinstance(table, str) else table


def read_as(requested, got):
    """0.5.20: an old name read after its standard name's first store gets the standard name's table; the receipt then
    names that table and says which name was read: {table: <standard>, read_as: <old>}. got: the table field of the
    document the read returned. Every other read: {}."""
    std = alias_of(requested)
    return {"table": std, "read_as": requested} if std is not None and got == std else {}


def read_names(requested, got):
    """The names a read receipt carries: before its first store a standard name reads its old name's table and says so
    as 0.5.19 did ({alias_of: <old>}); an old name read after it, read_as; else {}."""
    old = old_name(requested)
    if old is not None:
        return {} if got == requested else {"alias_of": old}
    return read_as(requested, got)
