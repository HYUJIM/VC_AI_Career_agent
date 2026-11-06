"""Grounded curriculum generation (moved from BE app/services/curriculum_worker.py).

BE keeps the durable queue: claiming, leases, retries, evidence currency checks and
signed storage. This module receives one frozen job and returns either a validated
result or a failure code, together with the LLM audit that BE encrypts and stores.
Model output is an untrusted draft: it is parsed strictly, validated against the
frozen options, repaired at most once and otherwise replaced by the baseline.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from src.career.canonical import digest_json
from src.career.contracts.api import GenerationInput, GenerationOutcome
from src.career.contracts.curriculum import CurriculumDraft, CurriculumResult
from src.career.errors import ConfigurationError, EvidenceError
from src.career.learning_retrieval import RESOURCE_FRESHNESS_DAYS
from src.career.llm_client import ProviderFailure, generator_from_env, selector_payload
from src.career.output_validator import render_steps, validate_draft

logger = logging.getLogger(__name__)


def parse_draft(text):
    def unique(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate model key")
            output[key] = value
        return output

    return CurriculumDraft.model_validate(json.loads(text, object_pairs_hook=unique))


def failed(code, retryable=False, audit=None):
    return GenerationOutcome(
        state="failed", error_code=code, retryable=retryable, audit=audit or []
    )


async def generate(job: GenerationInput, client_factory=None, config_loader=None):
    audit = []
    try:
        draft, method, model, errors = job.context.baseline, "template", None, []
        if job.request.mode == "llm" and job.context.options:
            config = (config_loader or generator_from_env)()
            if config.public_metadata() != job.generator:
                raise EvidenceError("GENERATOR_CONFIGURATION_CHANGED")
            client = (client_factory or (lambda current: current.create_client()))(config)
            model = config.model
            for _ in range(2):
                prompt_hash = digest_json(selector_payload(job.context, job.request, errors))
                try:
                    reply = await client.generate(job.context, job.request, errors)
                except ProviderFailure as exc:
                    audit.append(
                        {"prompt_hash": prompt_hash, "provider_error": exc.code, "usage": {}}
                    )
                    raise
                try:
                    if not reply.complete:
                        raise ValueError("incomplete")
                    proposed = parse_draft(reply.text)
                    errors = validate_draft(proposed, job.context, job.request)
                except (ValueError, ValidationError, TypeError, RecursionError):
                    errors = ["INVALID_MODEL_SCHEMA_OR_INCOMPLETE_JSON"]
                audit.append(
                    {
                        "prompt_hash": prompt_hash,
                        "raw_output": reply.text.encode("utf-8", errors="backslashreplace").decode(
                            "utf-8"
                        ),
                        "output_encoding": "utf8-backslashreplace",
                        "usage": reply.usage,
                        "validation_errors": errors,
                    }
                )
                if not errors:
                    draft, method = proposed, "llm"
                    break
            else:
                method = "llm_fallback"
        elif job.request.mode == "llm":
            method = "template"  # No actionable steps; never bill an empty request.
        if validate_draft(draft, job.context, job.request):
            raise EvidenceError("INTERNAL_BASELINE_VALIDATION_FAILED")
        steps, known_cost, unknown_costs = render_steps(draft, job.context, job.request)
        warnings = list(job.context.warnings)
        if method == "llm_fallback":
            warnings.append("UNTRUSTED_MODEL_OUTPUT_DISCARDED")
        if unknown_costs:
            warnings.append("RESOURCE_COSTS_PARTLY_UNKNOWN")
        warnings.extend(
            [
                "STUDY_HOURS_ARE_PLANNING_ESTIMATES",
                "SOURCE_PRICES_AND_AVAILABILITY_REQUIRE_RECHECK_BEFORE_ENROLLMENT",
            ]
        )
        limits = [job.analysis_valid_until]
        limits.extend(
            step.resource.resource.captured_at + timedelta(days=RESOURCE_FRESHNESS_DAYS)
            for step in steps
            if step.resource
        )
        partial = bool(
            job.context.questions
            or errors
            or unknown_costs
            or "GENERAL_EXERCISE_NO_REVIEWED_COURSE" in warnings
            or "CONFIRM_EXPERIENCE_BEFORE_CONDITIONAL_LEARNING" in warnings
        )
        result = CurriculumResult(
            job_id=job.job_id,
            analysis_id=job.analysis_id,
            as_of=datetime.now(timezone.utc),
            valid_until=min(limits),
            generation_method=method,
            model=model,
            status="partial" if partial else "ready",
            steps=steps,
            total_hours=sum(step.hours for step in steps),
            known_cost_krw=known_cost,
            has_unknown_costs=unknown_costs,
            questions=job.context.questions,
            warnings=sorted(set(warnings)),
            validation_errors=errors,
        )
        return GenerationOutcome(state="completed", result=result, audit=audit)
    except ProviderFailure as exc:
        return failed(exc.code, exc.retryable, audit)
    except ConfigurationError:
        return failed("GENERATOR_CONFIGURATION_REQUIRED", False, audit)
    except (ValueError, TypeError, KeyError):
        return failed("UNTRUSTED_OR_UNSUPPORTED_JOB_INPUT", False, audit)
    except Exception as exc:
        logger.error("Curriculum processing error type: %s", type(exc).__name__)
        return failed("INTERNAL_GENERATION_ERROR", False, audit)
