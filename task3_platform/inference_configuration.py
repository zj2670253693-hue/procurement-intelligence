"""Explicit operator profile; no model client/tokenizer is built by API admission.

An operator approval is an attestation, not proof of a mathematical token bound.
Real counter evidence and finite fee authorization remain separate release gates.
"""
from dataclasses import replace
from decimal import Decimal
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from .contracts import ModelBudget, PlatformError, content_sha256, strict_json_loads
from .paths import reject_link_ancestors
from .model_transport import InferencePolicy, LedgerTransport, OpenAIBackend
from .ledger import LedgerStore
from models.inference import InferenceHalt


def _trusted_file(value,maximum):
    if not isinstance(value,str) or not Path(value).is_absolute(): raise ValueError()
    path=reject_link_ancestors(Path(value))
    info=path.stat()
    if not path.is_file() or info.st_size>maximum: raise ValueError()
    if os.name=='posix' and (info.st_mode & 0o022 or info.st_uid not in (0,os.geteuid())):
        raise ValueError()
    return path


def _digest(value):
    return isinstance(value,str) and re.fullmatch(r'[a-f0-9]{64}',value)


def _read_profile(environ):
    try:
        path=_trusted_file(environ.get('TASK3_INFERENCE_PROFILE'),64*1024)
        profile=strict_json_loads(path.read_bytes())
        if not isinstance(profile,dict) or set(profile)!={'version','provider','model','base_url','authorized_owner_ids','maximum_budget','generation','counter','verification'}:
            raise ValueError()
        if profile['version']!=1 or profile['provider'] not in {'deepseek','qwen'}: raise ValueError()
        if not isinstance(profile['model'],str) or not 1<=len(profile['model'])<=128: raise ValueError()
        url=urlsplit(profile['base_url'])
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment: raise ValueError()
        url.port
        owners=profile['authorized_owner_ids']
        if not isinstance(owners,list) or not 1<=len(owners)<=100 or any(not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{32}',v) for v in owners): raise ValueError()
        budget=ModelBudget(**profile['maximum_budget'])
        if not (0<budget.max_calls<=10000 and 0<budget.max_input_tokens<=10**9 and 0<budget.max_output_tokens<=10**9): raise ValueError()
        generation=profile['generation']
        if set(generation)!={'temperature','max_tokens'} or not isinstance(generation['temperature'],str): raise ValueError()
        temperature=Decimal(generation['temperature'])
        if not temperature.is_finite() or not 0<=temperature<=2 or type(generation['max_tokens']) is not int or not 1<=generation['max_tokens']<=32768: raise ValueError()
        counter=profile['counter'];verification=profile['verification']
        if set(counter)!={'path','source_sha256','profile_sha256'} or not all(_digest(counter[k]) for k in ('source_sha256','profile_sha256')): raise ValueError()
        source=_trusted_file(counter['path'],1024**2)
        if source.parent!=path.parent/'counters' or source.suffix!='.py' or hashlib.sha256(source.read_bytes()).hexdigest()!=counter['source_sha256']: raise ValueError()
        if set(verification)!={'approved','record_sha256'} or verification['approved'] is not True or not _digest(verification['record_sha256']): raise ValueError()
        return profile
    except Exception:
        raise PlatformError('inference_configuration_unverified',503) from None


def _load_counter(profile):
    counter=profile['counter'];source=_trusted_file(counter['path'],1024**2)
    if hashlib.sha256(source.read_bytes()).hexdigest()!=counter['source_sha256']:
        raise InferenceHalt('unverified_token_counter')
    name='task3_approved_counter_'+counter['source_sha256']
    spec=importlib.util.spec_from_file_location(name,source)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    spec.loader.exec_module(module)
    result=module.build_counter()
    if (result.verified is not True or result.profile_sha256!=counter['profile_sha256'] or
            not result.supports(profile['provider'],profile['model'])):
        raise InferenceHalt('unverified_token_counter')
    return result


class InferenceConfiguration:
    def __init__(self,environ,profile,backend_factory):
        self.environ,self.profile=environ,profile
        self.identity=content_sha256(profile)
        self.backend_factory=backend_factory
        self._counter,self._backend=None,None
        self.maximum_budget=ModelBudget(**profile['maximum_budget'])
        self.policy=InferencePolicy(enabled=self._enabled,authorized_owner_ids=profile['authorized_owner_ids'],
            maximum_budget=self.maximum_budget,allowed_models={(profile['provider'],profile['model'])},
            counter_profiles={profile['counter']['profile_sha256']})

    def _enabled(self):
        try:
            return self.environ.get('TASK3_PAID_ENABLED')=='1' and content_sha256(_read_profile(self.environ))==self.identity
        except Exception: return False

    def authorize_upload(self,actor,spec):
        if not self._enabled() or actor.role not in {'reviewer','admin'} or actor.user_id not in self.profile['authorized_owner_ids']:
            raise PlatformError('paid_inference_not_authorized',403)
        if spec.mode!='extract' or spec.budget is None or any(not 0<getattr(spec.budget,key)<=getattr(self.maximum_budget,key) for key in ('max_calls','max_input_tokens','max_output_tokens')):
            raise PlatformError('inference_budget_not_authorized',403)

    def transport_factory(self,jobs,lease,pulse):
        if not self._enabled(): raise InferenceHalt('paid_inference_disabled')
        try:
            if self._counter is None: self._counter=_load_counter(self.profile)
            if self._backend is None:
                key_name='DEEPSEEK_API_KEY' if self.profile['provider']=='deepseek' else 'QWEN_API_KEY'
                key=self.environ.get(key_name)
                if not key: raise InferenceHalt('inference_credentials_unavailable')
                self._backend=self.backend_factory(api_key=key,base_url=self.profile['base_url'],timeout=jobs.limits.model_seconds)
        except InferenceHalt: raise
        except Exception: raise InferenceHalt('unverified_token_counter') from None
        inner=LedgerTransport(LedgerStore(jobs,lease),self.policy,self._backend,self._counter,pulse=pulse)
        profile=self.profile
        class ConfiguredTransport:
            task_id=lease.task_id
            provider,model=profile['provider'],profile['model']
            def __call__(self,request):
                if (request.provider,request.model)!=(self.provider,self.model):
                    raise InferenceHalt('inference_model_forbidden')
                return inner(replace(request,parameters={**request.parameters,
                    'temperature':Decimal(profile['generation']['temperature']),'max_tokens':profile['generation']['max_tokens']}))
        return ConfiguredTransport()


def configured_inference(environ=None,*,backend_factory=None):
    environ=os.environ if environ is None else environ
    if environ.get('TASK3_PAID_ENABLED')!='1': return None
    profile=_read_profile(environ)
    key_name='DEEPSEEK_API_KEY' if profile['provider']=='deepseek' else 'QWEN_API_KEY'
    if not environ.get(key_name): raise PlatformError('inference_credentials_unavailable',503)
    return InferenceConfiguration(environ,profile,backend_factory or OpenAIBackend)


def safe_configuration():
    try: return configured_inference()
    except PlatformError: return None
