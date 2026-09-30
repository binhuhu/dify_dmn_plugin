import hashlib

XML = '<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/"/>'
DIGEST = hashlib.sha256(XML.encode()).hexdigest()
CREDENTIALS = {"engine_url": "https://engine.example.test", "api_key": "test-only-token"}
ENGINE = {
    "name": "dmn-elements",
    "version": "0.3.0",
    "feel": "feelin@8.2.0",
    "profile": "dmn13-safe-v1",
}
