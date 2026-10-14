"""Synchronous read-only FastAPI adapters, bounded and dataset pinned."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from .queries import RelationshipQueries
from .graph import relationship_graph


class QueryRequest(BaseModel):
    query_type: Literal['buyer_suppliers','buyer_bidders','supplier_peers','common_buyers','common_projects']
    dataset_id: str | None = Field(default=None,max_length=80)
    selector: str = Field(default='',max_length=200)
    selectors: list[str] = Field(default_factory=list,max_length=10)
    scope_level: Literal['project','event'] = 'project'
    page: int = Field(default=1,ge=1,le=10000)
    page_size: int = Field(default=30,ge=1,le=200)
    secondary_page: int = Field(default=1,ge=1,le=10000)
    secondary_page_size: int = Field(default=30,ge=1,le=200)


def create_router(repository_provider,*,prefix='/api/task2'):
    router=APIRouter(prefix=prefix,tags=['任务二关系分析'])
    def run(action):
        try: return action()
        except KeyError as exc: raise HTTPException(404,'指定数据版本或原文证据不存在') from exc
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
    def pinned(repo,requested):
        version=repo.resolve_version(requested)
        return version,RelationshipQueries(repo.load(version))

    @router.get('/status')
    def status(dataset_id: str | None=None,repo=Depends(repository_provider)):
        return run(lambda: repo.status(repo.resolve_version(dataset_id)))

    @router.get('/summary')
    def summary(dataset_id: str | None=None,repo=Depends(repository_provider)):
        def execute():
            version=repo.resolve_version(dataset_id)
            manifest,coverage=repo.metadata(version)
            small={k:v for k,v in coverage.items() if k.endswith('_counts') or k in ('announcement_status_count','known_amount_count','metric_note','new_model_calls')}
            return dict(dataset_id=version,status='completed',source_count=len(manifest['sources']),counts=manifest['counts'],coverage=small,new_model_calls=0)
        return run(execute)

    @router.get('/coverage')
    def coverage(kind: Literal['announcement_status','candidate_status','scope_status']='announcement_status',
                 dataset_id: str | None=None,page: int=Query(default=1,ge=1,le=10000),
                 page_size: int=Query(default=30,ge=1,le=200),repo=Depends(repository_provider)):
        def execute():
            start=(page-1)*page_size
            if start>=10000: raise ValueError('page offset exceeds 10000 result cap')
            version=repo.resolve_version(dataset_id)
            _,full=repo.metadata(version)
            values=full.get(kind,{})
            entries=sorted(values.items()) if isinstance(values,dict) else [(str(i),v) for i,v in enumerate(values)]
            return dict(dataset_id=version,kind=kind,total=len(entries),page=page,page_size=page_size,items=[dict(id=k,value=v) for k,v in entries[start:min(start+page_size,10000)]])
        return run(execute)

    @router.get('/organizations')
    def organizations(role: Literal['buyer','winner'],term: str=Query(default='',max_length=120),
                      dataset_id: str | None=Query(default=None,max_length=80),
                      limit: int=Query(default=50,ge=1,le=100),repo=Depends(repository_provider)):
        return run(lambda: pinned(repo,dataset_id)[1].choices(role,term,limit))

    @router.post('/query')
    def query(request: QueryRequest,repo=Depends(repository_provider)):
        def execute():
            _,q=pinned(repo,request.dataset_id)
            start=(request.page-1)*request.page_size
            secondary_start=(request.secondary_page-1)*request.secondary_page_size
            if start>=10000 or secondary_start>=10000: raise ValueError('page offset exceeds 10000 result cap')
            end=min(request.page*request.page_size,10000)
            result=getattr(q,request.query_type)(request.selectors if request.query_type.startswith('common')
                                               else request.selector,scope_level=request.scope_level,
                                               limit=10000 if request.query_type in ('buyer_bidders','supplier_peers') else end)
            if request.query_type=='supplier_peers':
                result['items']=result.pop('participants');result['total']=result['participants_total']
                result['nonwinning_participants_total']=len(result['nonwinning_participants'])
                result['nonwinning_participants']=result['nonwinning_participants'][secondary_start:secondary_start+request.secondary_page_size]
            if 'combinations' in result:
                result['combinations']=result['combinations'][secondary_start:secondary_start+request.secondary_page_size]
            result['items']=result.get('items',[])[start:end]
            result.update(page=request.page,page_size=request.page_size,
                          secondary_page=request.secondary_page,secondary_page_size=request.secondary_page_size,
                          result_cap=10000,result_truncated=result['total']>10000)
            def collect(value):
                if isinstance(value,dict):
                    return value.get('record_ids',[])+[rid for k,v in value.items() if k!='record_ids' for rid in collect(v)]
                if isinstance(value,list): return [rid for v in value for rid in collect(v)]
                return []
            result['graph']=relationship_graph(q,collect(result['items'])+collect(result.get('top5',[])))
            return result
        return run(execute)

    @router.get('/evidence/{evidence_id}')
    def evidence(evidence_id: str,dataset_id: str=Query(min_length=1,max_length=80),repo=Depends(repository_provider)):
        return run(lambda: repo.evidence(repo.resolve_version(dataset_id),evidence_id))
    return router
