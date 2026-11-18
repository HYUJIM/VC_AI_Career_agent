"""Make ONE real Anthropic Claude API call with the AI service's settings and report the result.

Run from the AI `ai/` folder after putting ANTHROPIC_API_KEY into ai/.env:

    ../BE/.venv/bin/python scripts/claude_check.py
    ../BE/.venv/bin/python scripts/claude_check.py --model claude-haiku-5-5   # try another model once
    ../BE/.venv/bin/python scripts/claude_check.py --effort medium            # try another effort once

- Reads ai/.env (variables already set in the shell win) unless --no-dotenv is given or
  CAREER_AI_SKIP_DOTENV=true. CURRICULUM_LLM_ENABLED does not have to be true yet: the check turns
  it on for its own process only, so the key can be tried before LLM mode is switched on.
- Sends one small synthetic selector request (two made-up steps, no personal data, no VC data)
  through src.career.claude_client: the same SDK call, schema, effort, limits and error mapping as
  the service.
- Prints the model, effort, HTTP status, latency, token usage and whether the reply passed the
  JSON and draft checks. Never prints the key, the request or the model's text.
- Exit codes: 0 connected and the reply is valid CurriculumDraft JSON (draft rule problems, if any,
  are listed on the draft_valid line), 1 the call failed or the JSON did not validate,
  2 configuration error.
- Each run is one billable request (a few thousand tokens at most).
- Not part of the Docker image (ai/.dockerignore excludes scripts/).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

HINTS = {
    400: "request refused: model ID, effort, schema, a beta flag or the account's billing (type shows which)",
    401: "key rejected: check ANTHROPIC_API_KEY (no spaces, the whole key, not revoked)",
    403: "permission denied: the key's workspace or organization cannot use this model or feature",
    404: "model not found: check CURRICULUM_LLM_MODEL",
    408: "request timed out at Anthropic: try again",
    413: "request too large",
    429: "rate limit reached: wait and try again, or check the rate limits in the Claude Console",
    529: "Anthropic API overloaded: try again later",
}


def synthetic_payload():
    """Same shape as llm_client.selector_payload, with made-up IDs and no user or VC data."""
    from src.career.contracts.curriculum import CurriculumDraft

    first, second, resource = str(uuid4()), str(uuid4()), str(uuid4())
    options = [
        {
            "step_id": first,
            "skill": "sql",
            "kind": "practice",
            "depends_on": [],
            "minimum_hours": 2,
            "resources": [
                {
                    "version_id": resource,
                    "minimum_hours": 3,
                    "cost_krw": 0,
                    "level": "beginner",
                    "untrusted_quotes": ["SQL"],
                }
            ],
        },
        {
            "step_id": second,
            "skill": "spark",
            "kind": "practice",
            "depends_on": [first],
            "minimum_hours": 3,
            "resources": [],
        },
    ]
    baseline = {
        "steps": [
            {"step_id": first, "resource_version_id": resource, "hours": 3},
            {"step_id": second, "resource_version_id": None, "hours": 3},
        ]
    }
    return {
        "options": options,
        "total_hours_limit": 20,
        "budget_krw": 0,
        "baseline": baseline,
        "schema": CurriculumDraft.model_json_schema(),
        "correction_codes": [],
    }


def check_draft(text, payload):
    """(json_valid, problems). The service's own validator needs a full planning context; this
    applies the same rules to the synthetic options: every step once, prerequisite order, listed
    resources only, minimum hours and the hour budget."""
    from src.career.curriculum_generator import parse_draft

    try:
        draft = parse_draft(text)
    except Exception:  # noqa: BLE001 - any parse/schema failure means "did not validate"
        return False, ["INVALID_MODEL_SCHEMA_OR_INCOMPLETE_JSON"]
    options = {item["step_id"]: item for item in payload["options"]}
    order = [str(step.step_id) for step in draft.steps]
    problems = []
    if sorted(order) != sorted(options):
        problems.append("STEPS_NOT_SELECTED_EXACTLY_ONCE")
    seen = set()
    for step in draft.steps:
        option = options.get(str(step.step_id))
        if option is None:
            continue
        if any(dependency not in seen for dependency in option["depends_on"]):
            problems.append("PREREQUISITE_ORDER")
        seen.add(str(step.step_id))
        minimum = option["minimum_hours"]
        if step.resource_version_id is not None:
            resources = {item["version_id"]: item for item in option["resources"]}
            chosen = resources.get(str(step.resource_version_id))
            if chosen is None:
                problems.append("UNKNOWN_RESOURCE")
            else:
                minimum = max(minimum, chosen["minimum_hours"])
        if step.hours < minimum:
            problems.append("BELOW_MINIMUM_HOURS")
    if sum(step.hours for step in draft.steps) > payload["total_hours_limit"]:
        problems.append("OVER_HOUR_BUDGET")
    return True, sorted(set(problems))


def main(argv=None, transport=None, out=None):
    out = out or sys.stdout

    def say(line):
        print(line, file=out)

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", help="override CURRICULUM_LLM_MODEL for this check")
    parser.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="override CURRICULUM_LLM_EFFORT",
    )
    parser.add_argument("--no-dotenv", action="store_true", help="do not read ai/.env")
    args = parser.parse_args(argv)

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if not args.no_dotenv and os.getenv("CAREER_AI_SKIP_DOTENV", "false").lower() != "true":
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")  # never overrides variables already set

    from src.career.claude_client import ANTHROPIC_API_BASE, ClaudeClient, ClaudeConfig
    from src.career.errors import ConfigurationError
    from src.career.llm_client import ProviderFailure

    os.environ["CURRICULUM_LLM_ENABLED"] = "true"  # this process only
    for value, name in ((args.model, "CURRICULUM_LLM_MODEL"), (args.effort, "CURRICULUM_LLM_EFFORT")):
        if value:
            os.environ[name] = value
    provider = os.getenv("CURRICULUM_LLM_PROVIDER", "").strip() or "claude"
    if provider != "claude":
        say(f"note: CURRICULUM_LLM_PROVIDER={provider}, so the service itself will not use Claude")
    try:
        config = ClaudeConfig.from_env()
    except ConfigurationError as exc:
        say(f"configuration error: {exc}")
        return 2

    say(f"endpoint: {ANTHROPIC_API_BASE}/v1/messages (Messages API, anthropic SDK)")
    say(f"model: {config.model}")
    say(f"effort: {config.effort}")
    say(f"refusal_fallback: {'on' if config.refusal_fallback else 'off'}")
    say("ANTHROPIC_API_KEY: set (not shown)")

    payload = synthetic_payload()
    client = ClaudeClient(config, transport=transport)
    try:
        reply = asyncio.run(client.complete(payload))
    except ProviderFailure as exc:
        say(f"HTTP status: {exc.http_status or client.last_status or '-'}")
        say(f"latency_ms: {client.last_latency_ms}")
        say(
            f"error: {exc.code} (retryable={'yes' if exc.retryable else 'no'}, "
            f"provider code={exc.provider_code or '-'})"
        )
        hint = HINTS.get(exc.http_status) or (
            "Anthropic-side error: try again later" if (exc.http_status or 0) >= 500 else None
        )
        if exc.code == "LLM_PROVIDER_UNAVAILABLE":
            hint = "no answer within 25 s or no connection: network, proxy-free egress to 443, DNS"
        if (exc.provider_code or "").startswith("refusal"):
            hint = "the model declined the request (stop_reason refusal)"
        if hint:
            say(f"hint: {hint}")
        say("result: FAILED")
        return 1

    say(f"HTTP status: {client.last_status}")
    say(f"served_by: {client.last_model or '-'}")
    say(f"latency_ms: {client.last_latency_ms}")
    say(f"usage: {reply.usage or '-'}")
    say(f"complete: {'yes' if reply.complete else 'no (incomplete: max_tokens reached)'}")
    json_valid, problems = check_draft(reply.text, payload) if reply.complete else (False, [])
    say(f"json_valid: {'yes' if json_valid else 'no'}")
    say(
        f"draft_valid: {'yes' if json_valid and not problems else 'no'}"
        + (f" ({', '.join(problems)})" if problems else "")
    )
    if not (reply.complete and json_valid):
        say("result: FAILED (the service would retry once, then use the template plan)")
        return 1
    if problems:
        # Connection and structured output work; the model broke a selector rule this time.
        say("result: OK (connected, JSON valid; the service would retry once on these draft errors)")
    else:
        say("result: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
