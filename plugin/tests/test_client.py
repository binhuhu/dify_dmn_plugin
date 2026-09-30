import copy
import hashlib
import json

import gevent
import httpx
import pytest

import dmn_client.client as client_module
from dmn_client.client import (
    MAX_INPUT_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_XML_BYTES,
    EngineClient,
    EngineError,
    evaluate_parameters,
    prepare_request,
)

XML = '<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/"/>'
DIGEST = hashlib.sha256(XML.encode()).hexdigest()
PARAMETERS = {"dmn_xml": XML, "inputs_json": '{"age": 21}', "decision_id": "decision_1"}
CREDENTIALS = {"engine_url": "https://engine.example.test", "api_key": "test-only-token"}
ENGINE = {
    "name": "dmn-elements",
    "version": "0.3.0",
    "feel": "feelin@8.2.0",
    "profile": "dmn13-safe-v1",
}


def envelope(**overrides):
    result = {
        "schema_version": "1.0",
        "status": "SUCCEEDED",
        "decision_id": "decision_1",
        "result": {"eligible": True},
        "outcome": "MATCHED",
        "result_state": "VALUE",
        "decisions": [],
        "trace": [],
        "diagnostics": [],
        "error": None,
        "engine": ENGINE,
        "model_sha256": DIGEST,
    }
    result.update(overrides)
    return result


def invoke(handler, parameters=None, credentials=None):
    return evaluate_parameters(
        PARAMETERS if parameters is None else parameters,
        CREDENTIALS if credentials is None else credentials,
        transport=httpx.MockTransport(handler),
    )


def json_response(value, status=200):
    return httpx.Response(status, json=value)


def assert_failed(result, code):
    assert result["status"] == "FAILED"
    assert result["result"] is None
    assert result["outcome"] is None
    assert result["error"]["code"] == code
    assert result["schema_version"] == "1.0"


def test_success_posts_exact_request_and_returns_envelope():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://engine.example.test/evaluate"
        assert request.headers["authorization"] == "Bearer test-only-token"
        assert request.headers["accept-encoding"] == "identity"
        assert json.loads(request.content) == {
            "dmn_xml": XML,
            "inputs": {"age": 21},
            "decision_id": "decision_1",
            "include_trace": False,
        }
        return json_response(envelope())

    assert invoke(handler) == envelope()
    assert len(calls) == 1


def test_explicit_trace_false_and_service_prefix():
    def handler(request):
        assert str(request.url) == "https://engine.example.test/api/evaluate"
        assert json.loads(request.content)["include_trace"] is False
        return json_response(envelope())

    result = invoke(
        handler,
        {**PARAMETERS, "include_trace": False},
        {**CREDENTIALS, "engine_url": "https://engine.example.test/api/"},
    )
    assert result["status"] == "SUCCEEDED"


@pytest.mark.parametrize(
    "raw",
    [
        "{bad",
        '{"x":1,"x":2}',
        '{"x":{"a":1,"a":2}}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":-Infinity}',
        '{"x":1e999}',
        '{"x":"\\ud800"}',
        "[" * 100 + "0" + "]" * 100,
        '{"x":' + "[" * 66 + "0" + "]" * 66 + "}",
    ],
)
def test_rejects_non_strict_json_without_network(raw):
    def forbidden(_):
        pytest.fail("invalid input must not reach network")

    assert_failed(invoke(forbidden, {**PARAMETERS, "inputs_json": raw}), "INVALID_JSON")


@pytest.mark.parametrize("raw", ["[]", "null", '"hello"', "123", "true"])
def test_inputs_must_be_json_object(raw):
    assert_failed(
        invoke(lambda _: pytest.fail("network"), {**PARAMETERS, "inputs_json": raw}),
        "INVALID_INPUT",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"inputs_json": []},
        {"decision_id": None},
        {"decision_id": " "},
        {"decision_id": "x\n"},
        {"decision_id": "x" * 1025},
        {"dmn_xml": ""},
        {"dmn_xml": None},
        {"dmn_xml": "\ud800"},
        {"include_trace": "false"},
        {"include_trace": 0},
        {"include_trace": None},
    ],
)
def test_invalid_parameters(overrides):
    assert_failed(
        invoke(lambda _: pytest.fail("network"), {**PARAMETERS, **overrides}), "INVALID_INPUT"
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("dmn_xml", "x" * (MAX_XML_BYTES + 1)),
        ("dmn_xml", "好" * (MAX_XML_BYTES // 3 + 1)),
        ("inputs_json", '{"x":"' + "x" * MAX_INPUT_BYTES + '"}'),
    ],
)
def test_input_limits_are_utf8_bytes(key, value):
    assert_failed(
        invoke(lambda _: pytest.fail("network"), {**PARAMETERS, key: value}), "INPUT_TOO_LARGE"
    )


@pytest.mark.parametrize("xml", ["<!DOCTYPE x><x/>", "<!doctype x><x/>", '<!ENTITY x "a"><x/>'])
def test_xml_declarations_rejected_before_network(xml):
    assert_failed(
        invoke(lambda _: pytest.fail("network"), {**PARAMETERS, "dmn_xml": xml}), "UNSAFE_XML"
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/model.dmn",
        "ftp://host.test",
        "https://user:secret@host.test",
        "https://host.test?q=a",
        "https://host.test#frag",
        "https://host.test:0",
        "https://host.test:65536",
        "https://[broken",
        "https://host.test\n",
        "https://host.test\\path",
        "https://",
        "",
        None,
    ],
)
def test_invalid_service_urls(url):
    assert_failed(
        invoke(lambda _: pytest.fail("network"), credentials={**CREDENTIALS, "engine_url": url}),
        "INVALID_CONFIGURATION",
    )


def test_http_requires_explicit_boolean_opt_in():
    credentials = {**CREDENTIALS, "engine_url": "http://dmn-engine:8080"}
    assert_failed(
        invoke(lambda _: pytest.fail("network"), credentials=credentials), "INSECURE_TRANSPORT"
    )
    assert_failed(
        invoke(
            lambda _: pytest.fail("network"),
            credentials={**credentials, "allow_insecure_http": "true"},
        ),
        "INVALID_CONFIGURATION",
    )
    assert (
        invoke(
            lambda _: json_response(envelope()),
            credentials={**credentials, "allow_insecure_http": True},
        )["status"]
        == "SUCCEEDED"
    )


@pytest.mark.parametrize("key", ["", None, "has space", "has\r\nnewline", "密钥", "x" * 4097])
def test_invalid_bearer_token_never_sent(key):
    assert_failed(
        invoke(lambda _: pytest.fail("network"), credentials={**CREDENTIALS, "api_key": key}),
        "INVALID_CONFIGURATION",
    )


@pytest.mark.parametrize(
    "status,code",
    [
        (301, "ENGINE_HTTP_ERROR"),
        (302, "ENGINE_HTTP_ERROR"),
        (307, "ENGINE_HTTP_ERROR"),
        (400, "ENGINE_HTTP_ERROR"),
        (401, "ENGINE_AUTHENTICATION_FAILED"),
        (403, "ENGINE_AUTHENTICATION_FAILED"),
        (500, "ENGINE_HTTP_ERROR"),
    ],
)
def test_http_errors_are_sanitized_without_redirects_or_retries(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"location": "https://untrusted.example.test/"},
            text="secret token and model content",
        )

    result = invoke(handler)
    assert_failed(result, code)
    assert len(calls) == 1
    assert "secret" not in json.dumps(result)


def test_engine_failure_is_not_promoted_to_success():
    response = envelope(
        status="FAILED",
        result=None,
        outcome=None,
        result_state="UNAVAILABLE",
        error={"code": "FEEL_ERROR", "message": "Invalid expression"},
    )
    assert invoke(lambda _: json_response(response)) == response


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "2"},
        {"status": "OK"},
        {"decision_id": "wrong"},
        {"engine": {}},
        {"decisions": {}},
        {"trace": {}},
        {"diagnostics": "oops"},
        {"model_sha256": None},
        {"model_sha256": "a" * 64},
        {"error": {"code": "ERROR", "message": "error"}},
        {"outcome": "UNKNOWN"},
        {"status": "FAILED"},
        {"status": "FAILED", "result": None, "outcome": None, "error": {}},
    ],
)
def test_inconsistent_envelope_rejected(changes):
    assert_failed(invoke(lambda _: json_response(envelope(**changes))), "INVALID_RESPONSE")


def test_incomplete_response_rejected():
    assert_failed(invoke(lambda _: json_response({"status": "SUCCEEDED"})), "INVALID_RESPONSE")


@pytest.mark.parametrize(
    "raw",
    [b'{"x":NaN}', b'{"x":1,"x":2}', b"\xff", b"{", '"hello"'.encode("utf-16"), b'{"x":"\\ud800"}'],
)
def test_invalid_response_json(raw):
    result = invoke(
        lambda _: httpx.Response(200, content=raw, headers={"content-type": "application/json"})
    )
    assert_failed(result, "INVALID_RESPONSE")


def test_response_size_header_and_stream_limits():
    assert_failed(
        invoke(
            lambda _: httpx.Response(
                200,
                content=b"{}",
                headers={
                    "content-type": "application/json",
                    "content-length": str(MAX_RESPONSE_BYTES + 1),
                },
            )
        ),
        "RESPONSE_TOO_LARGE",
    )

    class LargeStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b" " * MAX_RESPONSE_BYTES
            yield b"{}"

    result = invoke(
        lambda _: httpx.Response(
            200, stream=LargeStream(), headers={"content-type": "application/json"}
        )
    )
    assert_failed(result, "RESPONSE_TOO_LARGE")


@pytest.mark.parametrize(
    "headers",
    [
        {"content-type": "text/html"},
        {"content-type": "application/json", "content-length": "-1"},
        {"content-type": "application/json", "content-encoding": "br"},
    ],
)
def test_response_headers_fail_closed(headers):
    assert_failed(
        invoke(lambda _: httpx.Response(200, content=b"{}", headers=headers)), "INVALID_RESPONSE"
    )


@pytest.mark.parametrize(
    "exception,code",
    [
        (httpx.ReadTimeout("secret"), "ENGINE_TIMEOUT"),
        (httpx.ConnectTimeout("secret"), "ENGINE_TIMEOUT"),
        (httpx.ConnectError("secret URL"), "ENGINE_CONNECTION_ERROR"),
    ],
)
def test_transport_errors_sanitized(exception, code):
    def handler(_):
        raise exception

    result = invoke(handler)
    assert_failed(result, code)
    assert "secret" not in json.dumps(result)


def test_total_deadline_interrupts_cooperative_io(monkeypatch):
    monkeypatch.setattr(client_module, "REQUEST_TIMEOUT_SECONDS", 0.01)

    def handler(_):
        gevent.sleep(0.1)
        return json_response(envelope())

    assert_failed(invoke(handler), "ENGINE_TIMEOUT")


def test_client_disables_proxy_environment_redirects_and_verifies_tls(monkeypatch):
    actual_client = httpx.Client
    options = []

    def spy(*args, **kwargs):
        options.append(kwargs)
        return actual_client(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "Client", spy)
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.example.test:9999")
    invoke(lambda _: json_response(envelope()))
    assert options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False
    assert options[0]["verify"] is True
    assert options[0]["timeout"].read <= 20


def test_health_uses_authentication_and_validates_version():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/health"
        assert request.headers["authorization"] == "Bearer test-only-token"
        return json_response({"status": "ok", "protocol_version": "1.0", "engine": ENGINE})

    assert (
        EngineClient(CREDENTIALS, transport=httpx.MockTransport(handler)).health()["engine"]
        == ENGINE
    )
    with pytest.raises(EngineError, match="protocol 1.0"):
        EngineClient(
            CREDENTIALS,
            transport=httpx.MockTransport(
                lambda _: json_response(
                    {"status": "ok", "protocol_version": "2.0", "engine": ENGINE}
                )
            ),
        ).health()


def test_no_model_url_or_dynamic_engine_url_parameter():
    params = {
        **PARAMETERS,
        "engine_url": "https://untrusted.example.test",
        "dmn_url": "https://untrusted.example.test/model",
    }
    paths = []
    result = invoke(
        lambda request: paths.append(str(request.url)) or json_response(envelope()), params
    )
    assert result["status"] == "SUCCEEDED"
    assert paths == ["https://engine.example.test/evaluate"]


def test_preparation_does_not_mutate_parameters():
    original = copy.deepcopy(PARAMETERS)
    prepare_request(PARAMETERS)
    assert PARAMETERS == original


def test_huge_integer_engine_response_is_structured():
    client = EngineClient(
        CREDENTIALS,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, content='{"x":' + "9" * 400 + "}", headers={"content-type": "application/json"}
            )
        ),
    )
    with pytest.raises(EngineError) as error:
        client.post_json("/evaluate", {})
    assert error.value.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("native", [False, True])
def test_huge_integer_input_is_structured(native):
    parameters = {
        **PARAMETERS,
        "inputs_json": {"x": 10**400} if native else '{"x":' + "9" * 400 + "}",
    }
    result = invoke(lambda _: pytest.fail("invalid input reached network"), parameters)
    assert result["error"]["code"] == "INVALID_JSON"
