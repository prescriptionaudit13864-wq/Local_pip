"""Check AUDIT_PROVIDER=vllm without a GPU: a stand-in vLLM server answers with fixed text.

Checks that layer 1 goes to the LoRA name and layer 2 to the base name, that prompts are sent as the same token ids
the in-process model reads (no added BOS on training-format prompts), that the fast-concern rounds and the stop
string work, and that a whole section check runs through the app's AuditService.

    python -m benchmark.vllm_provider_selftest
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("HF_HUB_OFFLINE", "1")
BASE = "unsloth/gemma-4-12B-it-qat-q4_0-unquantized"
LORA = "prescribex"
SEEN: list[dict] = []


def _fake_answer(body: dict, text: str) -> str:
    from audit_pipeline.finetune_prompt import load_taxonomy

    if body["model"] == BASE:  # layer 2
        if body.get("max_tokens") == 60:  # second look
            return "1]}"
        return (
            '{"n": 1, "entry": "fever", "problems": [{"problem": "x", "severity": "review", "type": "implausible"}]}]}'
        )
    answer = text.split("### Output:", 1)[-1]
    if "Chief_Complaint" not in text:
        return '{"has_error": false, "section": "?", "detected_errors": []}'
    if '"detected_errors": [{' not in answer:  # first fast round: stop where the concern starts
        return (
            '\n{"has_error": true, "section": "CC", "detected_errors": [{"rule_id": "CC.3", '
            '"rule_name": "duration_missing", "concern": "'
        )
    concern = next(t for t in load_taxonomy()["CC"]["allowed_error_types"] if t["rule_id"] == "CC.3")["concern"]
    known = json.dumps(concern, ensure_ascii=False)[1:-1]
    return known[known.rfind(" ") :] + '", "why": "Duration is not mentioned for \'fever\'."}]}'


def _server() -> ThreadingHTTPServer:
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(BASE)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: ANN002
            pass

        def _send(self, obj: dict) -> None:
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._send({"data": [{"id": BASE}, {"id": LORA}]})

        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            choices = []
            for i, ids in enumerate(body["prompt"]):
                text = tok.decode(ids)
                SEEN.append({"model": body["model"], "first_id": ids[0], "text": text, "stop": body.get("stop")})
                choices.append({"index": i, "text": _fake_answer(body, text)})
            self._send({"choices": choices})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> None:
    httpd = _server()
    os.environ.update(
        {
            "AUDIT_PROVIDER": "vllm",
            "VLLM_BASE_URL": f"http://127.0.0.1:{httpd.server_address[1]}/v1",
            "HF_MODEL": BASE,
            "VLLM_LORA_NAME": LORA,
            "AUDIT_PROMPT_MODE": "finetune",
            "AUDIT_MODEL_ONLY": "1",
            "FINETUNE_UNSUPPORTED": "omit",
            "FINETUNE_QUESTION_RULES": "none",
        }
    )
    from audit_pipeline.app_server import AuditService

    service = AuditService()
    patient = {
        "id": 1,
        "serial": "T-1",
        "name": "Test",
        "gender": "Male",
        "dob": "1980-01-01",
        "ageValue": 46,
        "ageUnit": "years",
    }
    payload = {
        "patient": patient,
        "visit": {"id": 1, "chiefComplaints": [{"id": 1, "text": "fever"}]},
        "section": "chiefComplaints",
        "revision": 1,
    }
    live = service.audit_section(payload)["live_audit"]
    rules = [f.get("rule_id") for f in live.get("findings", [])]
    review = service.review_section({**payload, "layer1": [f.get("why", "") for f in live.get("findings", [])]})
    layer1 = [s for s in SEEN if s["model"] == LORA]
    layer2 = [s for s in SEEN if s["model"] == BASE]
    checks = {
        "layer 1 raised CC.3 from the model's answer": "CC.3" in rules,
        "layer 1 why is the model's own text": any("fever" in f.get("why", "") for f in live.get("findings", [])),
        "layer 1 requests went to the LoRA": bool(layer1),
        "layer 1 prompts have no added BOS": all(not s["text"].startswith("<bos>") for s in layer1),
        "fast-concern rounds stop at the concern": any(s["stop"] == ['"concern": "'] for s in layer1),
        "layer 2 requests went to the base model": len(layer2) == 2,
        "layer 2 uses the chat template": all(s["text"].startswith("<bos>") for s in layer2),
        "layer 2 stops at ]}]}": layer2 and layer2[0]["stop"] == ["]}]}"],
        "review shown after second look": review["review"]["status"] == "done"
        and len(review["review"]["problems"]) == 1,
    }
    for name, ok in checks.items():
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    print(f"requests: layer 1 {len(layer1)} prompts, layer 2 {len(layer2)}; findings {rules}")
    httpd.shutdown()
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
