"""0.5.46: the save check of what object a scene-entity conclusion asks for (DMN 2.0.95 §五, Brian 11-25 00:30「都照推荐定」).

A conclusion's data may write issue_type_objects {issue type: object} and topic_objects {topic: object}, both literals; the object
asked for is topic_objects[the issue's topic] -> issue_type_objects[its issue type] -> object_type, the first one found. A save of
trigger.scene.entity now checks, before anything is written:

  - the shape: {"literal": {key: value}} with non-empty string keys and values, issue_type_objects keyed by an issue type
    (COMPLAINT / CONSULT / FEEDBACK), topic_objects at most TOPIC_OPTIONS_MAX keys;
  - each value is a result entity kind of the capability table head, or a policy word (KNOWLEDGE / NONE);
  - each topic_objects key is a topic, in the scene-topic table head, of a scene the rules using this conclusion list (when /
    when_any on scene_id, either spelling).
A missing capability table or scene-topic table leaves those two checks out (the shape is still checked). A read never checks.
"""

from dmn_client import chain_limits
from dmn_client.route_table import RouteTableError
from dmn_client.scene_id import canonical
from dmn_client.stored_tables import ISSUE_TYPES

CODE = "ENTITY_TOPIC_OBJECTS_INVALID"
CELLS = ("issue_type_objects", "topic_objects")
POLICY_WORDS = ("KNOWLEDGE", "NONE")
SCENE_PATH = ["parameters", "scene_id", "value"]


def _need(ok, message, path):
    if not ok:
        raise RouteTableError(CODE, message, path)


def topic_keys_max():
    return chain_limits.limit("TOPIC_OPTIONS_MAX")


def kinds_of(capability):
    """The result entity kinds of a capability table (None: no table)."""
    if capability is None:
        return None
    return {
        r.get("entity")
        for r in capability.get("capabilities") or []
        if isinstance(r.get("entity"), str)
    }


def topics_of(scene_topic):
    """{topic: canonical scene id} of a scene-topic table (None: no table)."""
    if scene_topic is None:
        return None
    return {
        t["topic"]: canonical(r["scene_id"])
        for r in scene_topic.get("rows") or []
        for t in r.get("topics") or []
    }


def _models(clean):
    if "models" in clean:
        for key in sorted(clean["models"]):
            yield "models.%s.model" % key, clean["models"][key]["model"]
    else:
        yield "model", clean.get("model") or {}


def _scenes_by_template(model):
    """{template ref: canonical scene ids its rules list}."""
    out = {}
    for rule in model.get("rules") or []:
        cases = rule["when_any"] if type(rule.get("when_any")) is list else [rule.get("when") or []]
        ids = out.setdefault(rule.get("output_template_ref"), set())
        for case in cases:
            for c in case:
                if c.get("path") != SCENE_PATH:
                    continue
                vals = c.get("value") if c.get("op") == "in" else [c.get("value")]
                ids.update(canonical(v) for v in vals if isinstance(v, str))
    return out


def check_table(clean, capability, scene_topic):
    kinds, topics = kinds_of(capability), topics_of(scene_topic)
    for mpath, model in _models(clean):
        scenes = _scenes_by_template(model)
        for ref in sorted(model.get("result_templates") or {}):
            data = model["result_templates"][ref].get("data") or {}
            for cell in CELLS:
                if cell not in data:
                    continue
                p = "%s.result_templates.%s.data.%s" % (mpath, ref, cell)
                raw = data[cell]
                _need(
                    type(raw) is dict and set(raw) == {"literal"},
                    '%s 写成 {"literal": {...}}' % cell,
                    p,
                )
                m = raw["literal"]
                _need(
                    type(m) is dict
                    and len(m) > 0
                    and all(type(k) is str and k and type(v) is str and v for k, v in m.items()),
                    "%s = {键: 对象}，键和值都是非空串，至少一个" % cell,
                    p + ".literal",
                )
                if cell == "issue_type_objects":
                    bad = sorted(k for k in m if k not in ISSUE_TYPES)
                    _need(
                        not bad,
                        "issue_type_objects 的键只能是问题类型 "
                        + " / ".join(ISSUE_TYPES)
                        + "："
                        + ", ".join(bad),
                        p + ".literal",
                    )
                else:
                    _need(
                        len(m) <= topic_keys_max(),
                        "topic_objects 至多 %d 个话题" % topic_keys_max(),
                        p + ".literal",
                    )
                for k in sorted(m):
                    q = "%s.literal.%s" % (p, k)
                    v = m[k]
                    _need(
                        v in POLICY_WORDS or kinds is None or v in kinds,
                        "%s 的对象 %s 没有着落：写能力表头版里的结果实体种类，或 %s"
                        % (cell, v, " / ".join(POLICY_WORDS)),
                        q,
                    )
                    if cell == "topic_objects" and topics is not None:
                        _need(
                            topics.get(k) in scenes.get(ref, set()),
                            "topic_objects 的键 %s 不是这条结论所列场景的话题（场景与话题表头版里它属于 %s）"
                            % (k, topics.get(k) or "没有这个话题"),
                            q,
                        )
