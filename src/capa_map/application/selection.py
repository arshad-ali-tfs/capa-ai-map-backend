"""Deterministic sampling with an explicit, retained selection policy."""
from collections import defaultdict, deque
import hashlib


def select_records(frame,limit,strategy='stratified'):
    if limit<=0 or len(frame)<=limit:
        return frame.copy()
    if strategy=='source_order':
        return frame.head(limit).copy()
    if strategy=='recent':
        return frame.sort_values(['date_opened','pr_id'],ascending=[False,True],kind='stable').head(limit).copy()
    if strategy!='stratified':
        raise ValueError('Unknown selection strategy.')
    buckets=defaultdict(list)
    for index,row in frame.iterrows():
        key=(str(row.get('date_opened',''))[:4],str(row.get('site','')),str(row.get('criticality','')))
        buckets[key].append((str(row['pr_id']),index))
    by_year=defaultdict(list)
    for key,items in buckets.items():
        ordered=sorted(items,key=lambda item:hashlib.sha256(item[0].encode()).hexdigest())
        by_year[key[0]].append((key,deque(index for _,index in ordered)))
    years={year:deque(values for _,values in sorted(groups,key=lambda item:hashlib.sha256(repr(item[0]).encode()).hexdigest())) for year,groups in by_year.items()}
    selected=[]
    while len(selected)<limit:
        for year in sorted(years,reverse=True):
            groups=years[year]
            if not groups:
                continue
            group=groups.popleft()
            selected.append(group.popleft())
            if group:
                groups.append(group)
            if len(selected)==limit:
                break
    return frame.loc[selected].copy()
