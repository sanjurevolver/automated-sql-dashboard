"""Catch broken model bindings and off-canvas visuals before opening Desktop."""
import json
import re
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]/'powerbi/BankingOperations'

def read_model_contract():
    """Extract names and contract properties from this project's Desktop-written TMDL.

    This intentionally is not a general TMDL parser. Desktop validates the full
    format; these tests catch broken visual references and contract regressions.
    """
    folder=PROJECT/'BankingOperations.SemanticModel'
    if (folder/'model.bim').exists():
        assert not (folder/'definition').exists(), 'Do not mix TMSL and TMDL'
        return json.loads((folder/'model.bim').read_text())['model']
    def unquote(value):return value.strip().strip("'").replace("''", "'")
    tables=[]
    for path in (folder/'definition/tables').glob('*.tmdl'):
        text=path.read_text(encoding='utf-8-sig')
        table={'name':unquote(re.search(r'^table (.+)$',text,re.M)[1]),'columns':[],'measures':[]}
        category=re.search(r'^\tdataCategory: (.+)$',text,re.M)
        if category:table['dataCategory']=category[1]
        for match in re.finditer(r'^\t(column|measure) (\x27(?:[^\x27]|\x27\x27)+\x27|[^\s=]+)(?: =[^\n]*)?\n((?:\t\t[^\n]*\n|\n)*)',text,re.M):
            kind,name,body=match.groups()
            item={'name':unquote(name)}
            for key,value in re.findall(r'^\t\t(\w+): (.+)$',body,re.M):item[key]=value
            if re.search(r'^\t\tisKey$',body,re.M):item['isKey']=True
            table['columns' if kind=='column' else 'measures'].append(item)
        tables.append(table)
    relationships=[]
    text=(folder/'definition/relationships.tmdl').read_text(encoding='utf-8-sig')
    for block in re.split(r'^relationship ',text,flags=re.M)[1:]:
        relation={'crossFilteringBehavior':'oneDirection'} # TMDL's omitted default.
        for side in ['from','to']:
            table,col=re.search(r'^\t'+side+r'Column: (.+?)\.(.+)$',block,re.M).groups()
            relation[side+'Table']=unquote(table);relation[side+'Column']=unquote(col)
        filtering=re.search(r'^\tcrossFilteringBehavior: (.+)$',block,re.M)
        if filtering:relation['crossFilteringBehavior']=filtering[1]
        relationships.append(relation)
    assert len(tables)==5 and len(relationships)==3
    return {'tables':tables,'relationships':relationships}

def test_report_bindings_and_layout():
    model=read_model_contract()
    tables={t['name']:t for t in model['tables']}
    for relation in model['relationships']:
        for side in ['from','to']:
            assert relation[side+'Column'] in {c['name'] for c in tables[relation[side+'Table']]['columns']}
    def inspect(value):
        if isinstance(value,dict):
            for kind,collection in [('Column','columns'),('Measure','measures')]:
                if kind in value:
                    ref=value[kind]
                    entity=ref['Expression']['SourceRef']['Entity']
                    assert ref['Property'] in {c['name'] for c in tables[entity].get(collection,[])}
            for child in value.values():inspect(child)
        elif isinstance(value,list):
            for child in value:inspect(child)
    pages=PROJECT/'BankingOperations.Report/definition/pages'
    metadata=json.loads((pages/'pages.json').read_text())
    assert metadata['activePageName'] in metadata['pageOrder']
    for page_id in metadata['pageOrder']:
        page=json.loads((pages/page_id/'page.json').read_text())
        for path in (pages/page_id/'visuals').glob('*/visual.json'):
            visual=json.loads(path.read_text())
            inspect(visual)
            p=visual['position']
            assert 0<=p['x']<p['x']+p['width']<=page['width']
            assert 0<=p['y']<p['y']+p['height']<=page['height']

def test_money_and_date_model_contract():
    model=read_model_contract()
    tables={t['name']:t for t in model['tables']}
    money={c['name']:c for c in tables['Transactions']['columns'] if 'CZK' in c['name']}
    assert money and all(c['dataType']=='decimal' and c['summarizeBy']=='none' for c in money.values())
    assert tables['Calendar']['dataCategory']=='Time'
    assert next(c for c in tables['Calendar']['columns'] if c['name']=='Date')['isKey']
    assert all(r['crossFilteringBehavior']=='oneDirection' for r in model['relationships'])

def test_regeneration_preserves_desktop_tmdl(tmp_path,monkeypatch):
    import shutil
    from powerbi import build_project
    source=PROJECT/'BankingOperations.SemanticModel/definition'
    assert source.exists()
    target=tmp_path/'BankingOperations.SemanticModel/definition'
    shutil.copytree(source,target)
    original=(target/'tables/Transactions.tmdl').read_bytes()
    monkeypatch.setattr(build_project,'OUT',tmp_path)
    build_project.build(tmp_path)
    assert (target/'tables/Transactions.tmdl').read_bytes()==original
    assert not (target.parent/'model.bim').exists()
    assert str(tmp_path) in (target/'expressions.tmdl').read_text()
