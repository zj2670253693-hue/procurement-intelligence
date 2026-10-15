"""No default paid authorization, no hidden retry, no unverified token counting."""
from dataclasses import replace
from decimal import Decimal
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from openai import OpenAI

from models.inference import InferenceHalt, InferenceResponse
from .contracts import ModelBudget, PlatformError, strict_json_loads


class InferencePolicy:
    def __init__(self, *, enabled=lambda: False, authorized_owner_ids=(), maximum_budget=None,
                 allowed_models=(), counter_profiles=()):
        self.enabled = enabled
        self.authorized_owner_ids = frozenset(authorized_owner_ids)
        self.maximum_budget = maximum_budget or ModelBudget(0, 0, 0)
        self.allowed_models = frozenset(allowed_models)
        self.counter_profiles = frozenset(counter_profiles)

    def authorize(self, job, request):
        if not self.enabled() or job["owner_id"] not in self.authorized_owner_ids:
            raise InferenceHalt("paid_inference_disabled")
        if job["mode"] != "extract" or (request.provider, request.model) not in self.allowed_models:
            raise InferenceHalt("inference_model_forbidden")
        budget = strict_json_loads(job["spec_json"]).get("budget") or {}
        for key in ("max_calls", "max_input_tokens", "max_output_tokens"):
            value = budget.get(key)
            if type(value) is not int or not 0 < value <= getattr(self.maximum_budget, key):
                raise InferenceHalt("inference_budget_not_authorized")


class UnverifiedCounter:
    """Safe default until an exact provider/model counting policy has been verified."""
    verified = False
    profile_sha256 = None

    def supports(self, provider, model):
        return False

    def upper_bound(self, request):
        return None


class LedgerTransport:
    def __init__(self, store, policy, backend, counter, *, pulse=None):
        self.store = store
        self.policy = policy
        self.backend = backend
        self.counter = counter
        self.task_id = store.lease.task_id
        self._lock = threading.Lock()
        self._halted = None
        self.pulse = pulse

    def _send(self, request):
        timeout = self.store.jobs.limits.model_seconds
        if self.pulse is None:
            return self.backend.send(request, timeout=timeout)
        future = Future()
        def network_only():
            try:
                future.set_result(self.backend.send(request, timeout=timeout))
            except BaseException as error:
                future.set_exception(error)
        threading.Thread(target=network_only, daemon=True).start()
        started = time.monotonic()
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("model_deadline")
            try:
                response = future.result(timeout=min(self.store.jobs.limits.heartbeat_seconds, remaining))
                self.pulse()
                return response
            except FutureTimeout:
                if future.done():
                    return future.result()
                self.pulse()

    def __call__(self, request):
        with self._lock:
            if self._halted:
                raise InferenceHalt(self._halted.code, details=self._halted.details)
            attempt, sent = None, False
            try:
                self.policy.authorize(self.store.job_row(), request)
                if (not self.counter.verified or self.counter.profile_sha256 not in self.policy.counter_profiles
                        or not self.counter.supports(request.provider, request.model)):
                    raise InferenceHalt("unverified_token_counter")
                request = replace(request, parameters={**request.parameters, "counter_profile_sha256": self.counter.profile_sha256})
                bound = self.counter.upper_bound(request)
                output = request.parameters.get("max_tokens")
                if type(bound) is not int or bound <= 0 or type(output) is not int or output <= 0:
                    raise InferenceHalt("unverified_token_bound")
                self.store.require_clear(self.task_id)
                attempt = self.store.reserve(self.store.lease, request, bound, output)
                if attempt.state == "received":
                    return self.store.response(attempt.attempt_id)
                # Recheck server authorization immediately before the committed send marker.
                self.policy.authorize(self.store.job_row(), request)
                if self.pulse:
                    self.pulse()
                self.store.mark_sent(attempt.attempt_id)
                sent = True
                response = self._send(request)
                self.store.record_response(attempt.attempt_id, response)
                return response
            except BaseException as exc:
                details = None
                if sent and attempt:
                    try:
                        self.store.mark_uncertain(attempt.attempt_id, "transport_or_persistence_uncertain")
                    except BaseException:
                        pass  # Persisted sent marker itself prevents automatic replay after DB failure.
                    code = "inference_needs_confirmation"
                elif isinstance(exc, (InferenceHalt, PlatformError)):
                    code = exc.code
                    if isinstance(exc, InferenceHalt) and code == "inference_budget_exhausted":
                        details = exc.details
                else:
                    code = "inference_ledger_unavailable"
                self._halted = InferenceHalt(code, details=details)
                raise self._halted from None


class OpenAIBackend:
    def __init__(self, *, api_key, base_url, timeout=120):
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)

    def send(self, request, *, timeout):
        params = request.parameters
        temperature = Decimal(str(params.get("temperature", "0.1")))
        if not temperature.is_finite():
            raise ValueError("invalid_generation_parameters")
        response = self.client.chat.completions.create(
            model=request.model, messages=list(request.messages),
            temperature=float(temperature), max_tokens=params["max_tokens"], timeout=timeout)
        usage = getattr(response, "usage", None)
        tokens = None
        if usage is not None:
            input_tokens = getattr(usage, "prompt_tokens", None)
            output_tokens = getattr(usage, "completion_tokens", None)
            if all(type(value) is int and value >= 0 for value in (input_tokens, output_tokens)):
                tokens = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        return InferenceResponse(response.choices[0].message.content or "", tokens,
                                 getattr(response, "id", None), getattr(response, "model", None))
