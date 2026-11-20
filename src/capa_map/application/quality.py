"""Transparent coverage, provenance and reviewer-first pattern triage."""
from collections import Counter
import hashlib
import json
import re
from .curation import THEMES


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def parent_theme(title, records):
    text=title.lower()
    rules=[
        (10,r'lack of (?:efficacy|effect)|adverse|complaint|reported.*(?:damage|tablet)|product.*(?:appearance|defect)'),
        (8,r'environmental monitor|microbio|aseptic|sterility|bioburden|endotoxin'),
        (9,r'transport|shipment|shipping|storage|cold.chain|distribution'),
        (4,r'packag|labell|labeling|line clearance|mix.up|carton|blister'),
        (3,r'cleaning|contamination|foreign (?:matter|particle)|particulat'),
        (0,r'analyt|laborator|hplc|uplc|sst|assay|dissolution|reference standard|chromatog|stability'),
        (2,r'equipment|calibrat|utility|utilities|hvac|power|alarm|sensor|maintenance'),
        (5,r'material|component|supplier|incoming|raw ingredient'),
        (6,r'document|data (?:handling|integrity)|record|instruction|signature|traceab|procedure'),
        (7,r'train|operator|personnel|execution|human error'),
        (1,r'manufactur|process|granulat|encapsulat|compress|blend|yield|coating|filling'),
    ]
    for index,pattern in rules:
        if re.search(pattern,text):
            return THEMES[index]
    if records and all(row.get('drType')=='Product Complaint' for row in records):
        return THEMES[10]
    return THEMES[-1]


def titles_differ(historical, current):
    left,right=parent_theme(historical,[]),parent_theme(current,[])
    # Different wording alone is not an assignment defect. Flag a concrete
    # disagreement between recognized quality themes; unknown labels remain
    # unreviewed without a fabricated semantic-confidence score.
    return left!=right and THEMES[-1] not in {left,right}


def source_mentions(sections):
    result=[]
    seen=set()
    for section in sections:
        for kind,pattern in [('Equipment',r'\b(?:MFG|EQP?|HPLC|UPLC|QC|INS|PT)[-_ ]?\d{2,8}(?:[-_][A-Za-z0-9]+)*\b'),
                             ('Location',r'\b(?:room|suite|building|line)\s+[A-Za-z]?[-_]?\d+[A-Za-z0-9-]*\b')]:
            for match in re.finditer(pattern,section['text'],re.I):
                key=(kind,match[0].casefold())
                if key in seen:
                    continue
                seen.add(key)
                result.append({'kind':kind,'value':match[0],'field':section['key'],'start':match.start(),'end':match.end(),
                               'role':'Source mention; event involvement requires review'})
                if len(result)>=20:
                    return result
    return result


def enrich(records, clusters, payload, context, state):
    original_members={cluster['id']:{row['id'] for row in records if row['clusterId']==cluster['id']} for cluster in clusters}
    for row in records:
        row['originalClusterId']=row['clusterId']
        change=state['assignments'].get(row['id'])
        if change:
            row['clusterId']=change['clusterId']
        row['assignmentReview']=change
        row['sourceMentions']=source_mentions(row.get('sourceSections',[]))
    by_id={cluster['id']:cluster for cluster in clusters}
    for key,review in state['clusters'].items():
        if key not in by_id:
            cluster={'id':key,'title':review['title'],'shortTitle':review['title'],'subtitle':review['title'],
                     'match':None,'context':'This reviewer-created group has no regenerated AI summary.',
                     'contextSource':'unavailable','titleSource':'unavailable','analysisContext':None,'historicalContext':None,'warnings':[],'capas':[]}
            by_id[key]=cluster
            clusters.append(cluster)
    for cluster in clusters:
        key=cluster['id']
        members=[row for row in records if row['clusterId']==key]
        ids={row['id'] for row in members}
        cluster['membershipChanged']=ids!=original_members.get(key,set())
        cluster['parentTheme']=parent_theme(cluster['title'],members)
        cluster['reviewStatus']='needs_review'
        cluster['themeSource']='suggested'
        cluster['reviewedContext']=None
        review=state['clusters'].get(key)
        review_reasons=[]
        historical=cluster.get('historicalContext')
        current=cluster.get('analysisContext')
        different=historical and current and titles_differ(historical['title'],current['title'])
        if review:
            cluster['reviewedContext']={'title':review['title'],'summary':review['rationale']}
            cluster.update(title=review['title'],shortTitle=review['title'][:80],subtitle=review['title'],titleSource='reviewed',
                           parentTheme=review['theme'],reviewStatus=review['status'],themeSource='reviewed')
        if different and not (review and review['status']=='reviewed'):
            review_reasons.append('The catalogue label and this analysis description indicate different quality themes. Confirm the theme and member records; the analysis title is an unreviewed proposal.')
        if cluster['membershipChanged']:
            review_reasons.append('Reviewer membership changes are active. Saved AI context describes the original members and has not been regenerated.')
        cluster['warnings'].extend(review_reasons)
        cluster['capas']=[item for item in context['capas'] if ids.intersection(item['recordIds'])]
        for row in members:
            row['warnings'].extend(review_reasons)
            if row['annotation']['assessment'] in {'Mixed / unclear','Does not support','Insufficient information'}:
                row['warnings'].append('The saved reviewer assessment requires follow-up.')
    valid_dates=sorted(row['opened'][:10] for row in records if re.match(r'^\d{4}-\d{2}-\d{2}',row['opened']))
    years=dict(sorted(Counter(row['year'] for row in records if row['year']!='Not provided').items()))
    report=payload.get('filter_report',{})
    meta=payload.get('metadata',{})
    recipe={key:meta[key] for key in ['model','embedding_model','generation_parameters','llm_preprocessing_version',
        'bge_preprocessing_version','prompt_sha256','options','inputs','asset_sha256','selection_strategy'] if key in meta}
    original=[{'id':row['id'],'cluster':row.get('finalCluster'),'fields':row.get('fields'),
               'sourceHash':digest(row.get('source',{}))} for row in payload['records']]
    return {'coverage':{'openedFrom':valid_dates[0] if valid_dates else None,'openedTo':valid_dates[-1] if valid_dates else None,
                        'yearCounts':years,'eligibleDeviationCount':report.get('filtered_unique_rows'),
                        'sourceDeviationCount':context.get('population',{}).get('Deviation'),
                        'sourceCapaCount':context.get('population',{}).get('CAPA'),
                        'sourceImportedAt':context['metadata'].get('imported_at'),
                        'selectionMethod':report.get('selection_strategy','source_order' if report.get('requested_limit') else 'not_retained'),
                        'rules':report.get('rules',[]),'missingOpenedDates':len(records)-len(valid_dates)},
            'provenance':{'recipe':recipe,'recipeHash':digest(recipe),'outputFingerprint':digest(original),
                          'assetFingerprintsAvailable':bool(meta.get('asset_sha256')),
                          'extractionHashes':{row['id']:digest(row.get('fields',{})) for row in payload['records']}},
            'review':{**state,'themes':THEMES,'taxonomyVersion':'quality-themes-v1',
                      'scope':'Local investigator review overlay; original analysis and source records are retained.'}}
