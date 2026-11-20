"""Suggested cluster groups; preserves detailed clusters and saved outputs."""
import re

THEMES = [
    'Analytical testing', 'Manufacturing process', 'Equipment and utilities',
    'Cleaning and contamination control', 'Packaging and labelling', 'Materials and components',
    'Documentation and data integrity', 'Training and procedural execution',
    'Environmental and microbiological control', 'Storage and transport',
    'Product complaints and adverse events', 'Other / needs classification',
]


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



def suggested_group(cluster, records):
    title = (cluster.get('analysisContext') or {}).get('title') or cluster['title']
    return parent_theme(title, records)
