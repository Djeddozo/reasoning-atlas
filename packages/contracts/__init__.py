"""Atlas version-one contracts and graph invariants; no HQ runtime dependency."""
from datetime import datetime
from uuid import UUID, uuid5, NAMESPACE_URL

FIELDS = {
 'KnowledgeItem': 'title body kind provenanceIds epistemicStatus',
 'Source': 'retrievedAt contentHash sourceType',
 'Claim': 'statement evidenceIds counterEvidenceIds status',
 'Experiment': 'hypothesisId method inputIds status result conclusion',
 'Goal': 'title horizon successCriteria status',
 'Project': 'title goalIds nodeIds status',
 'Skill': 'title domain proficiency evidenceIds',
 'Capability': 'title requiresIds evidenceIds proofLevel availability',
 'Resource': 'title resourceType attributes availability observedAt',
 'Task': 'title acceptanceCriteria owner status externalRef',
 'Run': 'taskId provider status checkpoint artifactIds',
 'Decision': 'question choice rationale alternatives sourceIds',
 'Memory': 'category text sourceIds confidenceLabel validFrom expiresAt',
 'VisionQuestion': 'question status visionRevision answerIds',
 'Answer': 'questionId text citationIds provider generatedAt status',
 'Attachment': 'contentHash mediaType sizeBytes localPath',
 'Edge': 'fromId toId relation provenanceIds status',
}
NULLABLE = {'retrievedAt','observedAt','expiresAt','proficiency','owner','externalRef','result','conclusion','provider'}
OBJECTS = {'attributes','externalRef'}
ENUMS = {'KnowledgeItem': {'kind': {'note','concept','question','hypothesis'}},
 'Claim': {'status': {'proposed','supported','disputed','retracted'}},
 'Task': {'status': {'draft','ready','in_progress','blocked','review','done'}},
 'Run': {'status': {'queued','running','interrupted','succeeded','failed','cancelled'}}}
def timestamp(value):
    if not isinstance(value,str) or not value.endswith('Z'): raise ValueError('Expected UTC timestamp')
    datetime.fromisoformat(value.replace('Z','+00:00'))
def validate(record):
    kind=record.get('entityType')
    if kind not in FIELDS: raise ValueError('Unknown entity type')
    UUID(record['id'])
    if record.get('schemaVersion') != 1: raise ValueError('Unsupported schema version')
    if type(record.get('revision')) is not int or record['revision'] < 1: raise ValueError('Invalid revision')
    for key in ('createdAt','updatedAt'): timestamp(record[key])
    if record['updatedAt'] < record['createdAt']: raise ValueError('Timestamp order')
    for key in FIELDS[kind].split():
        if key not in record: raise ValueError('Missing '+key)
        value=record[key]
        if value is None and key in NULLABLE: continue
        if key.endswith('Ids') or key == 'alternatives':
            if not isinstance(value,list) or not all(isinstance(v,str) for v in value): raise ValueError('Expected string list: '+key)
        elif key in OBJECTS:
            if not isinstance(value,dict): raise ValueError('Expected object: '+key)
        elif key in ('sizeBytes','visionRevision'):
            if type(value) is not int or value < 0: raise ValueError('Expected nonnegative integer')
        elif not isinstance(value,str): raise ValueError('Expected string: '+key)
        if key.endswith('At') and value is not None: timestamp(value)
    if kind == 'Source' and not (record.get('uri') or record.get('attachmentId')): raise ValueError('Source location required')
    for field, values in ENUMS.get(kind,{}).items():
        if record[field] not in values: raise ValueError('Invalid '+field)
    if kind == 'Edge' and record.get('origin') == 'ai':
        if record.get('reviewStatus') != 'proposed' or not record.get('explanation'): raise ValueError('AI relationship requires review and explanation')
    return record
def validate_graph(nodes, edges):
    records=nodes+edges
    for record in records: validate(record)
    if len({r['id'] for r in records}) != len(records): raise ValueError('Duplicate identity')
    ids={n['id'] for n in nodes}; dependencies={n:[] for n in ids}
    for edge in edges:
        for key in ('fromId','toId'):
            if edge[key] not in ids and not (edge[key].startswith('hq:') and edge['status']=='unresolved'): raise ValueError('Unresolved target must be explicit')
        if edge['relation']=='depends_on': dependencies.setdefault(edge['fromId'],[]).append(edge['toId'])
    for node in nodes:
        if node['entityType']=='Capability':
            for target in node['requiresIds']:
                if target not in ids: raise ValueError('Missing prerequisite')
                dependencies[node['id']].append(target)
    active=set(); done=set()
    def visit(node):
        if node in active: raise ValueError('Dependency cycle')
        if node in done: return
        active.add(node)
        for target in dependencies.get(node,[]): visit(target)
        active.remove(node); done.add(node)
    for node in dependencies: visit(node)
def import_identity(source_system, entity_type, external_id):
    """Stable tuple identity: repeated imports upsert, equal titles do not merge."""
    import json
    return str(uuid5(NAMESPACE_URL,json.dumps([source_system,entity_type,str(external_id)])))
def fixture():
    def base(kind, key):
        return dict(id=import_identity('synthetic',kind,key),entityType=kind,schemaVersion=1,createdAt='2026-10-20T00:00:00Z',updatedAt='2026-10-20T00:00:00Z',revision=1)
    nodes=[dict(base('KnowledgeItem',i),title=f'Research concept {i+1}',body='Synthetic research fixture.',kind='concept',provenanceIds=[],epistemicStatus='proposed') for i in range(200)]
    edges=[dict(base('Edge',i),fromId=nodes[i%200]['id'],toId=nodes[(i+1+i//200)%200]['id'],relation='associated_with',provenanceIds=[],status='proposed') for i in range(400)]
    return {'schemaVersion':1,'nodes':nodes,'edges':edges}
