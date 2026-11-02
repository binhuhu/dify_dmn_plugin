"""Policy table names: the five new names are read-only aliases of the current names."""

ALIASES = {
    "target.scene.entity": "target.scene_entity",
    "triage.scene.acceptance": "triage.acceptance",
    "triage.supplement.requirements": "supplement.requirements",
    "triage.dispatch.route": "dispatch.route",
    "triage.dispatch.resource": "dispatch.resource",
}


def storage_name(table):
    """The name the table is stored under: an alias's current name, any other name itself."""
    return ALIASES.get(table, table) if isinstance(table, str) else table


def alias_of(table):
    """The current name a new name reads, or None."""
    return ALIASES.get(table) if isinstance(table, str) else None
