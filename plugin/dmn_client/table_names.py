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


# 2026-11-16 (Brian「应该都是有task类型的吧」「对，四段」「都要落地啊」): every table name carries its task type first.
# Delivery's task type is "support"; its tables are stored under the names above and below (target. / triage. /
# solution. / handle.), which are the same names with the task type left out. Spelled in full, a delivery name is
# support.<that name>; the two are one table. The full spelling is taken off wherever a name comes in (a table parameter,
# a document's own name) before any other rule looks at it, so a full name behaves exactly as the name it spells: same
# storage, same versions, same owner and format rules, same signed and two-state rules, same old-name migration state.
TASK_TYPE_OF_DELIVERY = "support"
FULL_PREFIX = TASK_TYPE_OF_DELIVERY + "."
# handle. (2026-11-16, Brian「补进去」): delivery's handle workflow keeps its tables under handle.<object>.<judgment>, the
# name the other six task types write <task type>.handle.<object>.<judgment> with the task type left out, as target. and
# triage. are; so support.handle.<name> is the table handle.<name>
DELIVERY_WORKFLOW_PREFIXES = ("target.", "triage.", "solution.", "handle.")


def delivery_name(table):
    """The name a delivery table goes by here: support.<name> → <name> when <name> is under one of delivery's workflows;
    any other value itself."""
    if isinstance(table, str) and table.startswith(FULL_PREFIX):
        rest = table[len(FULL_PREFIX) :]
        # an old name is not spelled in full (two of the five old names begin target. / triage.: third review L1)
        if rest.startswith(DELIVERY_WORKFLOW_PREFIXES) and rest not in ALIASES:
            return rest
    return table


def full_spelled(table):
    """Whether a value is a delivery name written in full (support.<name>)."""
    return isinstance(table, str) and delivery_name(table) != table


REFERENCE_RULE = "表里引用别的表要写存表名，不写 support. 开头的全名：%s 写成 %s"

# 2026-11-18 (Brian 00:49「表名也改成四段吧」): the capability table is a delivery table under the locate workflow,
# support.target.capability.catalog in full, stored as target.capability.catalog. The name it had until then,
# platform.capability.catalog, is a read-only alias: a document or a pin that names it is taken as before (a published
# app still pins it), a read of it reads the table under the new name once that is stored (its own storage before:
# what 0.5.38 stored there, read as 0.5.38 read it), and nothing is saved under it any more. Not one of the five: no
# first-store migration, no drafts.
CAPABILITY_TABLE = "target.capability.catalog"
CAPABILITY_OLD_NAME = "platform.capability.catalog"
SCENE_ENTITY_TABLE = (
    "target.scene.entity"  # 场景关联实体表: its entity kinds have rows in the capability table
)


def full_name(table):
    """The full spelling of a delivery name (task type first), or None for a name that is not under delivery's
    workflows. An old name is spelled through its standard name."""
    if not isinstance(table, str):
        return None
    std = ALIASES.get(table, table)
    return FULL_PREFIX + std if std.startswith(DELIVERY_WORKFLOW_PREFIXES) else None


def storage_name(table):
    """The standard name of a table: an old name's standard name, any other name itself. Two names are one table when
    their storage names are equal. (2026-11-16: a full spelling counts as the name it spells.)"""
    table = delivery_name(table)
    return ALIASES.get(table, table) if isinstance(table, str) else table


def alias_of(table):
    """The standard name an old name is an alias of, or None."""
    table = delivery_name(table)
    return ALIASES.get(table) if isinstance(table, str) else None


def old_name(table):
    """The transitional old name of a standard name, or None."""
    table = delivery_name(table)
    return STANDARD.get(table) if isinstance(table, str) else None


def format_name(table):
    """The name whose format and owner rules a table name follows: a standard name its old name's, any other itself."""
    table = delivery_name(table)
    return STANDARD.get(table, table) if isinstance(table, str) else table


def read_as(requested, got):
    """0.5.20: an old name read after its standard name's first store gets the standard name's table; the receipt then
    names that table and says which name was read: {table: <standard>, read_as: <old>}. got: the table field of the
    document the read returned. Every other read: {}."""
    std = alias_of(requested)
    return {"table": std, "read_as": requested} if std is not None and got == std else {}


def read_names(requested, got):
    """The names a read receipt carries: before its first store a standard name reads its old name's table and says so
    as 0.5.19 did ({alias_of: <old>}); an old name read after it, read_as; else {}. 2026-11-18: the capability table's
    old name read when the new name holds the table says read_as too."""
    if requested == CAPABILITY_OLD_NAME:
        return {"table": CAPABILITY_TABLE, "read_as": requested} if got == CAPABILITY_TABLE else {}
    old = old_name(requested)
    if old is not None:
        return {} if got == requested else {"alias_of": old}
    return read_as(requested, got)
