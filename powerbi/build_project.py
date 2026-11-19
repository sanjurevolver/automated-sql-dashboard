"""Generate an editable native Power BI project from the pipeline data contract.

Run from the repository root: python powerbi/build_project.py
Close the project in Desktop before regenerating. Generated report files are
source-controlled; local Power BI data caches and PBIX packages are not.
"""
from pathlib import Path
import argparse
import json
import re
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'powerbi/BankingOperations'
SCHEMA = 'https://developer.microsoft.com/json-schemas/fabric/item/report/definition/'

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')

def literal(value):
    if isinstance(value, bool): text = str(value).lower()
    elif isinstance(value, (int,float)): text = str(value)+'D'
    else: text = "'"+str(value).replace("'", "''")+"'"
    return {'expr':{'Literal':{'Value':text}}}

def color(value):
    return {'solid':{'color':literal(value)}}

def field(table, name, measure=False):
    return {('Measure' if measure else 'Column'):{'Expression':{'SourceRef':{'Entity':table}},'Property':name}}

def projection(table, name, measure=False):
    return {'field':field(table,name,measure),'queryRef':f'{table}.{name}','nativeQueryRef':name}

def measure(name):
    return projection('Transactions', name, True)

def props(**kwargs):
    return [{'properties':kwargs}]

def visual(page, name, kind, x,y,w,h, roles=None, title=None, objects=None):
    config={'visualType':kind,'drillFilterOtherVisuals':True}
    if roles:
        config['query']={'queryState':{key:{'projections':val} for key,val in roles.items()}}
        if kind=='lineChart':
            config['query']['sortDefinition']={'sort':[{'field':roles['Category'][0]['field'],'direction':'Ascending'}]}
    config['visualContainerObjects']={
        'background':props(show=literal(True),color=color('#FFFFFF'),transparency=literal(0)),
        'border':props(show=literal(False)),
    }
    if title:
        config['visualContainerObjects']['title']=props(show=literal(True),text=literal(title),
            fontColor=color('#172B4D'),fontSize=literal(12),fontFamily=literal('Segoe UI Semibold'))
    if objects:config['objects']=objects
    result={'$schema':SCHEMA+'visualContainer/2.1.0/schema.json','name':name,
        'position':{'x':x,'y':y,'width':w,'height':h,'z':0,'tabOrder':0},'visual':config}
    write(OUT/'BankingOperations.Report/definition/pages'/page/'visuals'/name/'visual.json',result)
    return result

def textbox(page,name,text,x,y,w,h,size=14,color_value='#172B4D'):
    return visual(page,name,'textbox',x,y,w,h,objects={'general':props(paragraphs=[
        {'textRuns':[{'value':line,'textStyle':{'fontFamily':'Segoe UI','fontSize':f'{size}px','color':color_value}}]}
        for line in text.split('\n')])})

def slicer(page,name,table,column,x,w=230):
    v=visual(page,name,'slicer',x,104,w,62,{'Values':[projection(table,column)]},None,
        {'data':props(mode=literal('Dropdown')),'selection':props(singleSelect=literal(False))})
    v['visual']['syncGroup']={'groupName':table+'_'+column,'fieldChanges':True,'filterChanges':True}
    write(OUT/'BankingOperations.Report/definition/pages'/page/'visuals'/name/'visual.json',v)

def card(page,name,label,x,y=190,w=292):
    label_props={'fontSize':literal(28),'color':color('#133F51')}
    if label in ['Transaction Count','Active Accounts','Negative Balance Events']:
        label_props.update(labelDisplayUnits=literal(1),labelPrecision=literal(0))
    visual(page,name,'card',x,y,w,108,{'Values':[measure(label)]},objects={
        'labels':props(**label_props),
        'categoryLabels':props(show=literal(True),fontSize=literal(11),color=color('#506176'))})

TABLES={
 'Accounts':('dim_account.csv', [('Account ID','account_id','int64'),('District','district_id','int64'),
    ('Statement Frequency','frequency','string'),('Opened Date','opened_date','dateTime')]),
 'Calendar':('dim_date.csv',[('Date','date_key','dateTime'),('Year','year','int64'),('Month','month','int64'),
    ('Quarter','quarter','int64'),('Weekday','weekday','int64')]),
 'Operations':('dim_operation.csv',[('Operation','operation','string'),('Description','description','string')]),
 'Transactions':('fact_transaction.csv',[('Transaction ID','trans_id','int64'),('Account ID','account_id','int64'),
    ('Date','date_key','dateTime'),('Operation','operation','string'),('Direction','direction','string'),
    ('Amount CZK','amount','decimal'),('Signed Amount CZK','signed_amount','decimal'),
    ('Balance CZK','balance','decimal'),('Purpose','purpose','string'),('High Value','high_value','boolean')])}

MEASURES={
 'Transaction Count':('COUNTROWS(Transactions)','#,0'),
 'Active Accounts':('DISTINCTCOUNT(Transactions[Account ID])','#,0'),
 'Gross Volume CZK':('SUM(Transactions[Amount CZK])','#,0.00 "CZK"'),
 'Inflow CZK':('CALCULATE([Gross Volume CZK], Transactions[Direction] = "credit")','#,0.00 "CZK"'),
 'Outflow CZK':('CALCULATE([Gross Volume CZK], Transactions[Direction] = "debit")','#,0.00 "CZK"'),
 'Net Flow CZK':('SUM(Transactions[Signed Amount CZK])','#,0.00 "CZK"'),
 'Average Transaction CZK':('DIVIDE([Gross Volume CZK], [Transaction Count])','#,0.00 "CZK"'),
 'Volume Per Active Account CZK':('DIVIDE([Gross Volume CZK], [Active Accounts])','#,0.00 "CZK"'),
 'High Value Count':('COALESCE(CALCULATE([Transaction Count], Transactions[High Value] = TRUE()), 0)','#,0'),
 'High Value Share':('DIVIDE([High Value Count], [Transaction Count])','0.00%'),
 'Negative Balance Events':('COALESCE(CALCULATE([Transaction Count], Transactions[Balance CZK] < 0), 0)','#,0'),
 'Previous Month Volume CZK':('CALCULATE([Gross Volume CZK], DATEADD(\'Calendar\'[Date], -1, MONTH))','#,0.00 "CZK"'),
 'Volume MoM %':('DIVIDE([Gross Volume CZK] - [Previous Month Volume CZK], [Previous Month Volume CZK])','0.0%'),
 'Rolling 90D Volume CZK':('CALCULATE([Gross Volume CZK], DATESINPERIOD(\'Calendar\'[Date], MAX(\'Calendar\'[Date]), -90, DAY))','#,0.00 "CZK"'),
}

def semantic_model(project_root):
    expressions=[{'name':'ProjectRoot','kind':'m','expression':json.dumps(str(project_root))+ ' meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'},
        {'name':'ReportDirectory','kind':'m','expression':'let Manifest = Json.Document(File.Contents(ProjectRoot & "\\reports\\latest.json")) in Manifest[report_directory]'}]
    # M strings use literal backslashes, not JSON-style escaping.
    expressions[0]['expression']='"'+str(project_root).replace('"','""')+'" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'
    tables=[]
    for name,(file,columns) in TABLES.items():
        types={'int64':'Int64.Type','string':'type text','dateTime':'type date','decimal':'Currency.Type','boolean':'type logical'}
        conversion=', '.join('{"'+source+'", '+types[datatype]+'}' for _,source,datatype in columns)
        query=['let',f'    Source = Csv.Document(File.Contents(ReportDirectory & "\\{file}"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),',
            '    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),',
            '    Typed = Table.TransformColumnTypes(Headers, {'+conversion+'}, "en-US")', 'in','    Typed']
        table={'name':name,'columns':[],'partitions':[{'name':name,'mode':'import','source':{'type':'m','expression':query}}]}
        for display,source,datatype in columns:
            c={'name':display,'dataType':datatype,'sourceColumn':source,'summarizeBy':'none'}
            if datatype=='dateTime':c['formatString']='yyyy-MM-dd'
            if datatype=='decimal':c['formatString']='#,0.00 "CZK"'
            if name=='Calendar' and display=='Date':c['isKey']=True
            table['columns'].append(c)
        if name=='Calendar':
            table['dataCategory']='Time'
            table['columns'] += [
                {'name':'Month Start','dataType':'dateTime','type':'calculated','expression':'DATE(\'Calendar\'[Year], \'Calendar\'[Month], 1)','formatString':'MMM yyyy','summarizeBy':'none'},
                {'name':'Year Month','dataType':'string','type':'calculated','expression':'FORMAT(\'Calendar\'[Date], "yyyy-MM")','sortByColumn':'Month Start','summarizeBy':'none'},
                {'name':'Quarter Label','dataType':'string','type':'calculated','expression':'"Q" & \'Calendar\'[Quarter]','summarizeBy':'none'}]
            table['hierarchies']=[{'name':'Date hierarchy','levels':[{'name':col,'ordinal':i,'column':col} for i,col in enumerate(['Year','Quarter Label','Month Start','Date'])]}]
        if name=='Accounts':
            table['hierarchies']=[{'name':'District and Account','levels':[{'name':col,'ordinal':i,'column':col} for i,col in enumerate(['District','Account ID'])]}]
        if name=='Transactions':
            table['measures']=[{'name':n,'expression':ex,'formatString':fmt,'displayFolder':'Banking KPIs'} for n,(ex,fmt) in MEASURES.items()]
        tables.append(table)
    tables.append({'name':'Data Quality','columns':[{'name':'Metric','dataType':'string','sourceColumn':'Name','summarizeBy':'none'},
        {'name':'Value','dataType':'string','sourceColumn':'Value','summarizeBy':'none'}],
        'partitions':[{'name':'Data Quality','mode':'import','source':{'type':'m','expression':[
            'let','    Source = Json.Document(File.Contents(ReportDirectory & "\\quality.json")),',
            '    Rows = Record.ToTable(Source),','    Text = Table.TransformColumns(Rows, {{"Value", each Text.From(_, "en-US"), type text}})',
            'in','    Text']}}]})
    return {'name':'BankingOperations','compatibilityLevel':1567,'model':{'culture':'en-US',
        'defaultPowerBIDataSourceVersion':'powerBI_V3','sourceQueryCulture':'en-US',
        'dataAccessOptions':{'legacyRedirects':True,'returnErrorValuesAsNull':True},'expressions':expressions,
        'tables':tables,'relationships':[{'name':str(uuid.uuid5(uuid.NAMESPACE_URL,'banking-'+dim)),
            'fromTable':'Transactions','fromColumn':col,'toTable':dim,'toColumn':col,
            'fromCardinality':'many','toCardinality':'one','crossFilteringBehavior':'oneDirection'}
            for dim,col in [('Accounts','Account ID'),('Calendar','Date'),('Operations','Operation')]],
        'annotations':[{'name':'__PBI_TimeIntelligenceEnabled','value':'0'}]}}

def build(project_root=ROOT):
    report=OUT/'BankingOperations.Report'
    model=OUT/'BankingOperations.SemanticModel'
    has_tmdl=(model/'definition').exists()
    if has_tmdl and (model/'model.bim').exists():
        raise RuntimeError('Ambiguous model: preserve either model.bim or definition/, not both.')
    write(OUT/'BankingOperations.pbip',{'$schema':'https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json',
        'version':'1.0','artifacts':[{'report':{'path':'BankingOperations.Report'}}],'settings':{'enableAutoRecovery':True}})
    write(report/'definition.pbir',{'version':'4.0','datasetReference':{'byPath':{'path':'../BankingOperations.SemanticModel'}}})
    if has_tmdl:
        # Keep Desktop's validated native model, including lineage and manual edits.
        expressions=model/'definition/expressions.tmdl'
        text=expressions.read_text(encoding='utf-8-sig')
        replacement='expression ProjectRoot = "'+str(project_root).replace('"','""')+'" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'
        text,count=re.subn(r'^expression ProjectRoot = .*$',lambda _:replacement,text,flags=re.MULTILINE)
        if count!=1:raise ValueError('Expected exactly one ProjectRoot expression in the native model')
        expressions.write_text(text,encoding='utf-8')
        write(model/'definition.pbism',{'version':'4.2','settings':{}})
    else:
        write(model/'definition.pbism',{'version':'1.0','settings':{}})
        write(model/'model.bim',semantic_model(project_root))
    write(report/'definition/version.json',{'$schema':SCHEMA+'versionMetadata/1.0.0/schema.json','version':'2.0.0'})
    write(report/'definition/report.json',{'$schema':SCHEMA+'report/2.0.0/schema.json','themeCollection':{},
        'settings':{'defaultDrillFilterOtherVisuals':True,'defaultFilterActionIsDataFilter':True,'useEnhancedTooltips':True}})
    pages=[('overview','01  Executive Overview','Banking operations, in focus.'),
        ('accounts','02  Account Operations','Account activity & liquidity'),
        ('transactions','03  Transaction Review','Explore the ledger'),
        ('methodology','04  Quality & Methodology','Trust the numbers')]
    write(report/'definition/pages/pages.json',{'$schema':SCHEMA+'pagesMetadata/1.0.0/schema.json','pageOrder':[p[0] for p in pages],'activePageName':'overview'})
    for page,label,title in pages:
        write(report/'definition/pages'/page/'page.json',{'$schema':SCHEMA+'page/2.0.0/schema.json',
            'name':page,'displayName':label,'displayOption':'FitToPage','width':1280,'height':720,
            'objects':{'background':props(color=color('#F1F5F9'),transparency=literal(0))}})
        textbox(page,'title',title,24,8,1000,60,30)
        textbox(page,'subtitle','PKDD’99 BANKING  /  1993–1998  /  NOMINAL CZK',26,66,1100,34,12,'#527187')
        textbox(page,'footer','Ledger volume is not revenue. Net flow is not profit. Historical benchmark; no fraud predictions.',24,680,1228,40,11,'#5A6B80')
        if page!='methodology':
            slicer(page,'year_slicer','Calendar','Year',24)
            slicer(page,'district_slicer','Accounts','District',270)
            slicer(page,'direction_slicer','Transactions','Direction',516)
            slicer(page,'operation_slicer','Operations','Description',762,494)
    for i,label in enumerate(['Transaction Count','Active Accounts','Gross Volume CZK','Net Flow CZK']):
        card('overview','kpi'+str(i),label,24+i*314)
    visual('overview','cashflow','lineChart',24,318,760,345,
        {'Category':[projection('Calendar','Month Start')],'Y':[measure('Inflow CZK'),measure('Outflow CZK'),measure('Net Flow CZK')]},'Monthly ledger cash flows · CZK',
        {'categoryAxis':props(axisType=literal('Scalar'))})
    visual('overview','operations','clusteredBarChart',804,318,452,345,
        {'Category':[projection('Operations','Description')],'Y':[measure('Gross Volume CZK')]},'Volume by operation · CZK')
    visual('accounts','account_matrix','pivotTable',24,190,750,473,
        {'Rows':[projection('Accounts','District'),projection('Accounts','Account ID')],
         'Values':[measure('Transaction Count'),measure('Gross Volume CZK'),measure('Net Flow CZK'),measure('Negative Balance Events')]},
        'District → Account · expand rows to drill down')
    card('accounts','negative','Negative Balance Events',794,190,462)
    visual('accounts','per_account','lineChart',794,318,462,345,
        {'Category':[projection('Calendar','Month Start')],'Y':[measure('Volume Per Active Account CZK')]},'Volume per active account · CZK',
        {'categoryAxis':props(axisType=literal('Scalar'))})
    slicer('transactions','account_slicer','Accounts','Account ID',24,300)
    # Account selector sits below the synchronized filter row.
    path=report/'definition/pages/transactions/visuals/account_slicer/visual.json'
    v=json.loads(path.read_text());v['position']['y']=185;write(path,v)
    textbox('transactions','review_note','Filter to an account or year before inspecting individual rows. Amounts ≥100,000 CZK are review flags only.',344,185,912,62,13)
    visual('transactions','ledger','tableEx',24,264,1232,399,{'Values':[
        projection('Transactions',col) for col in ['Transaction ID','Account ID','Date','Direction','Operation','Amount CZK','Signed Amount CZK','Balance CZK','Purpose','High Value']]},'Transaction ledger · CZK')
    visual('methodology','quality','tableEx',24,112,660,548,
        {'Values':[projection('Data Quality','Metric'),projection('Data Quality','Value')]},'Latest successful ETL · quality controls')
    textbox('methodology','definitions',
        'WHAT THE KPIs MEAN\n\nGross volume: sum of absolute ledger amounts.\nNet flow: credits minus debits.\nActive accounts: distinct accounts in the current filter.\nNegative balance events: transactions with balance < 0.\n\nREVIEW FLAGS\n\n100,000 CZK is a fixed high-value threshold.\nNo transactions meet it in this historical snapshot.\nMissing operation codes are retained as UNKNOWN.\n\nREFRESH & INTERPRETATION\n\nRefresh reads reports/latest.json from the pipeline.\nThe source is a static historical banking benchmark.\nBalances are not additive across transactions.\nLarge amounts do not establish fraud.',
        708,112,548,548,16)
    print(OUT/'BankingOperations.pbip')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',type=Path,default=ROOT)
    args=parser.parse_args()
    build(args.project_root.resolve())
