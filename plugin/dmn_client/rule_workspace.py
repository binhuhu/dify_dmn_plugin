"""Content-only editor contracts. No query/action executor or persistence dependency."""

import hashlib
import re

import rfc8785

from .decision_executor import evaluate_decision
from .node_contract import (
    ContractError,
    decode_object,
    definition_digest,
    resolve_node,
    validate_definition,
)

SCHEMA = "service-decision-dsl.rule-workspace.v1"
PROFILE = "phase-step-node.v1"


def inspect(document):
    document = decode_object(document, "document", 1572864)
    count = 0

    def bounded(value, depth=0):
        nonlocal count
        count += 1
        if count > 60000 or depth > 40:
            raise ContractError("WORKSPACE_BUDGET_EXCEEDED")
        if isinstance(value, dict):
            for child in value.values():
                bounded(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                bounded(child, depth + 1)

    bounded(document)
    if document.get("schema_version") == "service-decision-dsl.rule-freeze.v1":
        if (
            set(document)
            != {"schema_version", "document", "definition_sha256", "content_sha256", "authority"}
            or document["authority"] != "CONTENT_ONLY_NOT_AUTHORIZATION"
        ):
            raise ContractError("FREEZE_CONTRACT_INVALID")
        bundle, digest = inspect(document["document"])
        if (
            document["definition_sha256"] != digest
            or document["content_sha256"]
            != hashlib.sha256(rfc8785.dumps(document["document"])).hexdigest()
        ):
            raise ContractError("FREEZE_DIGEST_MISMATCH")
        return bundle, digest
    required = {
        "schema_version",
        "container_profile",
        "document_id",
        "revision",
        "parent_definition_sha256",
        "definition_bundle",
    }
    if (
        set(document) != required
        or document["schema_version"] != SCHEMA
        or document["container_profile"] != PROFILE
    ):
        raise ContractError("WORKSPACE_CONTRACT_INVALID")
    if not isinstance(document["document_id"], str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,80}", document["document_id"]
    ):
        raise ContractError("WORKSPACE_ID_INVALID")
    if type(document["revision"]) is not int or not 0 <= document["revision"] <= 2147483647:
        raise ContractError("WORKSPACE_REVISION_INVALID")
    parent = document["parent_definition_sha256"]
    if parent is not None and (
        not isinstance(parent, str) or not re.fullmatch(r"[0-9a-f]{64}", parent)
    ):
        raise ContractError("WORKSPACE_PARENT_INVALID")
    bundle = document["definition_bundle"]
    validate_definition(bundle)
    return bundle, definition_digest(bundle)


def handle(request):
    if not isinstance(request, dict) or request.get("operation") not in {
        "validate",
        "evaluate",
        "freeze",
    }:
        raise ContractError("EDITOR_OPERATION_FORBIDDEN")
    operation = request["operation"]
    allowed = {"operation", "document"}
    if operation != "validate":
        allowed.add("expected_definition_sha256")
    if operation == "evaluate":
        allowed |= {"node_ref", "parameters", "as_of"}
    if set(request) != allowed:
        raise ContractError("EDITOR_REQUEST_INVALID")
    document = request["document"]
    bundle, digest = inspect(document)
    if document.get("schema_version") == "service-decision-dsl.rule-freeze.v1":
        document = document["document"]
    if operation != "validate" and request["expected_definition_sha256"] != digest:
        raise ContractError("DEFINITION_DIGEST_MISMATCH")
    result = {"definition_sha256": digest, "authority": "CONTENT_ONLY_NOT_AUTHORIZATION"}
    if operation == "freeze":
        result["frozen"] = {
            "schema_version": "service-decision-dsl.rule-freeze.v1",
            "document": document,
            "definition_sha256": digest,
            "content_sha256": hashlib.sha256(rfc8785.dumps(document)).hexdigest(),
            "authority": "CONTENT_ONLY_NOT_AUTHORIZATION",
        }
    if operation == "evaluate":
        ref = request["node_ref"]
        _, _, _, node = resolve_node(bundle, ref)
        if node["category"] != "EXECUTION" or node["kind"] != "DECISION":
            raise ContractError("EDITOR_NODE_FORBIDDEN")
        context = {
            "workflow_run_id": "EDITOR-WORKFLOW",
            "step_run_id": "EDITOR-STEP",
            "attempt_id": "EDITOR-ATTEMPT",
            "node_run_id": "EDITOR-NODE",
            "activation_ref": "synthetic:editor-not-activated",
            "subject_scope_ref": "synthetic:editor-manual-input",
            "authorization_context_ref": "synthetic:content-only-no-authority",
            "as_of": request["as_of"],
        }
        inputs = {
            "parameter_snapshot": {
                "schema_version": "service-decision-dsl.parameter-snapshot.v2",
                "parameters": request["parameters"],
                "as_of": request["as_of"],
                "prepared_for": ref,
                "definition_digest": digest,
                "subject_scope_ref": context["subject_scope_ref"],
                "provenance": {"environment": "SYNTHETIC", "fixture_only": True},
            },
            "runtime_snapshot": {"step_status": "RUNNING", "receipt_refs": []},
        }
        invocation = {
            "definition_bundle_json": bundle,
            "node_ref": ref,
            "expected_definition_sha256": digest,
            "inputs_json": inputs,
            "execution_context_json": context,
        }
        result["result"] = evaluate_decision(**invocation)
        # Exact Tool replay input, not an activation or execution token.
        result["tool_replay"] = invocation
    return result
