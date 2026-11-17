"""Public MAP presentation and record-review schemas used by OpenAPI."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Annotation(Contract):
    assessment: Literal['', 'Supports pattern', 'Mixed / unclear', 'Does not support', 'Insufficient information']
    note: str
    revision: int = Field(ge=0)
    savedAt: str | None


class AnnotationCollection(Contract):
    records: dict[str, Annotation]


class CapaRelationship(Contract):
    id: str
    description: str
    status: str
    recordIds: list[str]


class SourceSection(Contract):
    key: str
    label: str
    text: str


class FiveWh(Contract):
    who: str
    what: str
    when: str
    where: str
    why: str
    how: str


class DeviationRecord(Contract):
    id: str
    clusterId: str
    site: str
    division: str
    department: str
    year: str
    criticality: str
    status: str
    opened: str
    createdLabel: str
    drType: str
    building: str
    room: str
    suite: str
    productionArea: str
    productionLine: str
    shortDescription: str
    sourceField: str
    sourceNarrative: str
    sourceSections: list[SourceSection]
    fiveWh: FiveWh
    matchSignals: list[str]
    similarity: float | None
    warnings: list[str]
    annotation: Annotation
    volume: int = Field(gt=0)
    daysOpen: int | None
    monthCounts: dict[str, int]


class HistoricalClusterContext(Contract):
    title: str
    summary: str


class AnalysisClusterContext(HistoricalClusterContext):
    recordCount: int = Field(ge=1)
    runId: str = Field(pattern=r'^[a-f0-9]{32}$')


class Cluster(Contract):
    id: str
    title: str
    shortTitle: str
    subtitle: str
    match: float | None
    context: str
    contextSource: Literal['current_analysis', 'historical', 'unavailable']
    titleSource: Literal['current_analysis', 'historical', 'unavailable']
    analysisContext: AnalysisClusterContext | None
    historicalContext: HistoricalClusterContext | None
    capas: list[CapaRelationship]


class AnalysisCoverage(Contract):
    runId: str
    recordCount: int = Field(ge=0)
    sourceRecordCount: int | None = Field(ge=0)
    snapshotId: str | None
    sourceAvailable: bool
    sourceMode: str
    selection: Literal['configured', 'latest_completed']


class MapDataset(Contract):
    clusters: list[Cluster]
    records: list[DeviationRecord]
    months: list[str]
    refreshedAt: str
    analysis: AnalysisCoverage
