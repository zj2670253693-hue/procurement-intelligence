"""Explicit non-secret effective settings used by admission, child and worker."""
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
from .contracts import PlatformError
from .limits import Limits
from .paths import reject_link_ancestors

PARSER_KEYS=('PDF_MAX_TEXT_PAGES','PDF_RELEVANT_MAX_TEXT_PAGES','PDF_RELEVANT_NAME_PATTERN',
             'OCR_ENABLED','OCR_MAX_PAGES_PER_PDF','OCR_MIN_TEXT_CHARS','OCR_RENDER_SCALE')
MODEL_KEYS=('LLM_PROVIDER','QWEN_MODEL','QWEN_BASE_URL','DEEPSEEK_MODEL','DEEPSEEK_BASE_URL',
            'LLM_TEMPERATURE','LLM_MAX_TOKENS','LLM_TIMEOUT','MAX_TEXT_LENGTH_PER_REQUEST','TEXT_CHUNK_OVERLAP')


def _values(keys,environ):
    import config
    values={}
    for key in keys:
        value=environ.get(key,getattr(config,key,''))
        values[key]=str(value).lower() if isinstance(value,bool) else str(value)
    return values


def parser_settings(environ=None):
    return _values(PARSER_KEYS,os.environ if environ is None else environ)


def identity(environ=None):
    environ=os.environ if environ is None else environ
    profile=environ.get('TASK3_INFERENCE_PROFILE')
    digest=None
    if profile:
        path=reject_link_ancestors(Path(profile))
        if not Path(profile).is_absolute() or not path.is_file() or path.stat().st_size>64*1024:
            raise PlatformError('inference_profile_invalid',503)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
    return {'schema':1,'parser':parser_settings(environ),'model':_values(MODEL_KEYS,environ),
            'limits':asdict(Limits.from_env(environ)),'inference_profile_sha256':digest}
