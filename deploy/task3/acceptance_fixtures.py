"""PUBLIC SYNTHETIC acceptance data, never competition gold or real inference.

The fixture has explicit source identities, rounds, awards and complete rosters.
Consumers must still run the actual parsers/builders; do not insert hand-made
relationship facts or label this as real-model quality/usage evidence.
"""

BUYER='合成采购甲'
COMPANIES=('合成甲公司','合成乙公司','合成丙公司','合成丁公司')
CODES=dict(zip(COMPANIES,('913456789012345671','913456789012345672','913456789012345673','913456789012345674')))


def samples():
    result=[]
    for index,amount in ((1,'100.10'),(2,'200.20')):
        winner=COMPANIES[index-1]
        bidders=(COMPANIES[0],COMPANIES[1],COMPANIES[index+1])
        name='合成验收项目'+str(index);product='合成设备'+str(index)
        model='SYN-'+str(index);aid='t20990101_900'+str(index)
        rows=''.join(f'<tr><td>{company}</td><td>{90-number}</td><td>{CODES[company]}</td><td>{"是" if company==winner else "否"}</td></tr>'
            for number,company in enumerate(bidders))
        html=(f'<html><head><meta charset="utf-8"><title>{name}成交公告（合成验收）</title></head><body>'
            '<p>本页面为公开合成测试数据，不是实际采购记录。</p><p>轮次：第一轮</p>'
            f'<p>项目编号：SYNTHETIC-T3-{index}</p><p>项目名称：{name}</p>'
            f'<h6>采购人信息</h6><p>名称：{BUYER}</p><p>统一社会信用代码：913456789012345670</p>'
            '<h6>采购代理机构信息</h6><p>名称：合成代理机构</p><p>三、中标（成交）信息</p>'
            '<p>合同包1</p><table><tr><th>供应商名称</th><th>中标金额</th><th>统一社会信用代码</th></tr>'
            f'<tr><td>{winner}</td><td>{amount}元</td><td>{CODES[winner]}</td></tr></table>'
            '<p>合同包1 完整中标结果</p><p>评审信息 合同包1 全部投标人名单</p>'
            '<table><tr><th>投标人名称</th><th>综合得分</th><th>统一社会信用代码</th><th>是否中标</th></tr>'
            +rows+'</table><p>四、主要标的信息 合同包1</p>'
            f'<p>货物类（{winner}）</p><table><tr><th>货物名称</th><th>品目</th><th>品牌</th>'
            '<th>规格型号</th><th>数量</th><th>单价(元)</th><th>总价(元)</th></tr>'
            f'<tr><td>{product}</td><td>计算机设备</td><td>合成厂家</td><td>{model}</td>'
            f'<td>1台</td><td>{amount}</td><td>{amount}</td></tr></table></body></html>')
        entity={'product_name':product,'category':'计算机设备','brand':'合成厂家','spec_model':model,
            'unit_price':amount+'元','quantity':'1台','total_price':amount+'元'}
        extra={'project_name':name,'procurement_unit':BUYER,'winning_supplier':winner,
            'project_amount':amount+'元','bidders':list(bidders)}
        result.append({'announcement_id':aid,'html':html,'raw':{'announcement_id':aid,'success':True,
            'entities':[entity],'detail_entities':[],'detail_status':'no_attachment','extra_info':extra},
            'synthetic_only':True})
    return result


def synthetic_transport(task_id):
    """Operator test boundary only; never imported by production composition."""
    import json
    from models.inference import InferenceResponse
    by_id={item['announcement_id']:item for item in samples()}
    class SyntheticTransport:
        provider,model='deepseek','synthetic-not-a-real-provider-model'
        def __init__(self):self.task_id=task_id;self.calls=[]
        def __call__(self,request):
            if request.task_id!=self.task_id or request.announcement_id not in by_id or request.layer not in {'summary','extra'}:
                raise ValueError('synthetic_fixture_scope_mismatch')
            item=by_id[request.announcement_id]
            payload=item['raw']['extra_info'] if request.layer=='extra' else {'entities':item['raw']['entities']}
            self.calls.append({'announcement_id':request.announcement_id,'layer':request.layer})
            return InferenceResponse(json.dumps(payload,ensure_ascii=False),None,None,'synthetic-only')
    return SyntheticTransport()
