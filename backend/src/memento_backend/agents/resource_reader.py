"""On-demand reading of a resource with exact, reverified citations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from memento_backend.agents.capture_understanding_agent import source_record_ref
from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_bytes, sha256_json, validate_datetime
from memento_backend.domain.refs import SourceSpan
from memento_backend.policies.resource_policy import RESOURCE_READER_POLICY_VERSION, RESOURCE_READER_PROMPT_VERSION
from memento_backend.providers.protocol import Provider, ProviderFailure, ProviderRequest, ProviderUsage

from .protocol import AgentRunContext, make_candidate


@dataclass(frozen=True)
class ResourceReadInput:
    resource_card: Mapping[str, Any]
    source_record: Mapping[str, Any]
    authorized_text: str
    question: str

    def validate(self) -> None:
        validate_contract("resource-card-v1.schema.json", self.resource_card)
        validate_contract("source-record-v2.schema.json", self.source_record)
        if self.resource_card["source_record_ref"] != source_record_ref(self.source_record):
            raise ContractError("resource reader source binding is stale", kind="evidence")
        if not self.question.strip() or len(self.question) > 2_000:
            raise ContractError("resource reader question is invalid")
        if not self.authorized_text or len(self.authorized_text) > 1_000_000:
            raise ContractError("resource reader authorized text is invalid", kind="size")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "resource_ref": resource_card_ref(self.resource_card),
            "source_record_ref": source_record_ref(self.source_record),
            "authorized_text_sha256": sha256_bytes(self.authorized_text.encode("utf-8")),
            "question": self.question.strip(),
        }


def resource_card_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "resource_card", "id": value["resource_id"], "revision": value["revision"],
        "revision_sha256": sha256_json(value),
    }


class ResourceReader:
    def __init__(self, provider: Optional[Provider] = None) -> None:
        self.provider = provider

    def evaluate(
        self,
        value: ResourceReadInput,
        *,
        user_action_watermark_sha256: str,
        created_at: str,
    ) -> Mapping[str, Any]:
        value.validate()
        validate_datetime(created_at, "created_at")
        payload = value.canonical_payload()
        input_sha = sha256_json(payload)
        run_id = make_id(
            "agent_run",
            RESOURCE_READER_PROMPT_VERSION,
            {"input_sha256": input_sha, "watermark": user_action_watermark_sha256, "created_at": created_at},
        )
        usage = ProviderUsage.deterministic()
        output: Optional[Mapping[str, Any]] = None
        failure_reason = "provider_required"
        if self.provider is not None:
            request = ProviderRequest(
                run_id=run_id,
                agent_role="resource_reader",
                prompt_version=RESOURCE_READER_PROMPT_VERSION,
                policy_version=RESOURCE_READER_POLICY_VERSION,
                input_payload={**payload, "authorized_text": value.authorized_text},
            )
            request.validate()
            try:
                response = self.provider.complete(request)
                usage = response.usage
                output = self._validate_output(response.output)
            except ProviderFailure as exc:
                usage = exc.usage
                failure_reason = "provider_failed"
            except (ContractError, KeyError, TypeError, ValueError):
                failure_reason = "provider_invalid_output"
        context = AgentRunContext(
            run_id=run_id,
            agent_role="resource_reader",
            prompt_version=RESOURCE_READER_PROMPT_VERSION,
            policy_version=RESOURCE_READER_POLICY_VERSION,
            input_sha256=input_sha,
            user_action_watermark_sha256=user_action_watermark_sha256,
            created_at=created_at,
            usage=usage,
        )
        refs = [resource_card_ref(value.resource_card), source_record_ref(value.source_record)]
        if output is None:
            return make_candidate(
                context, action="stop", proposed_kind="none", proposed_object=None,
                source_refs=refs, source_spans=[], reason_code=failure_reason, confidence="low",
            )
        try:
            citations = [self._citation(value, quote) for quote in output["citation_quotes"]]
        except ContractError:
            return make_candidate(
                context, action="stop", proposed_kind="none", proposed_object=None,
                source_refs=refs, source_spans=[], reason_code="provider_invalid_output", confidence="low",
            )
        result_base = {
            "resource_ref": refs[0], "source_record_ref": refs[1], "question": value.question.strip(),
            "answer": output["answer"], "citations": citations, "unknowns": list(output["unknowns"]),
            "prompt_version": RESOURCE_READER_PROMPT_VERSION, "policy_version": RESOURCE_READER_POLICY_VERSION,
            "user_action_watermark_sha256": user_action_watermark_sha256, "usage": usage.to_dict(), "created_at": created_at,
        }
        result = {
            "schema_version": "1.0", "kind": "memento_resource_read_result",
            "result_id": make_id("resource_read_result", "resource-read-result-v1", result_base), **result_base,
        }
        validate_contract("resource-read-result-v1.schema.json", result)
        return make_candidate(
            context, action="propose_create", proposed_kind="resource_read_result", proposed_object=result,
            source_refs=refs, source_spans=citations, reason_code="resource_answer_with_citations", confidence="medium",
        )

    @staticmethod
    def _validate_output(value: Mapping[str, Any]) -> Mapping[str, Any]:
        if set(value) != {"answer", "citation_quotes", "unknowns"}:
            raise ContractError("resource reader provider output fields are invalid")
        if not isinstance(value["answer"], str) or not value["answer"].strip() or len(value["answer"]) > 12_000:
            raise ContractError("resource reader answer is invalid")
        for name, limit in (("citation_quotes", 32), ("unknowns", 24)):
            raw = value[name]
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) > limit:
                raise ContractError(f"resource reader {name} is invalid")
            if any(not isinstance(item, str) or not item.strip() for item in raw):
                raise ContractError(f"resource reader {name} is invalid")
        if not value["citation_quotes"]:
            raise ContractError("resource reader answer requires citations", kind="evidence")
        return value

    @staticmethod
    def _citation(value: ResourceReadInput, quote: str) -> dict[str, Any]:
        if quote not in value.authorized_text:
            raise ContractError("resource reader citation cannot be reverified", kind="evidence")
        record = value.source_record
        span = {
            "record_id": record["record_id"], "record_revision": record["revision"],
            "record_revision_sha256": sha256_json(record), "source_file": record["source_file"],
            "line_start": record["line_start"], "line_end": record["line_end"],
            "quote": quote, "quote_sha256": sha256_bytes(quote.encode("utf-8")),
        }
        SourceSpan.from_dict(span)
        return span
