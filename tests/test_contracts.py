import copy, unittest
from packages.contracts import *
class Contracts(unittest.TestCase):
    def test_fixture(self):
        f=fixture(); self.assertEqual((len(f['nodes']),len(f['edges'])),(200,400)); validate_graph(f['nodes'],f['edges'])
    def test_every_entity_and_missing_fields(self):
        for kind,fields in FIELDS.items():
            record=dict(fixture()['nodes'][0],entityType=kind)
            for key in fields.split():
                record[key]=[] if key.endswith('Ids') or key=='alternatives' else {} if key in OBJECTS else 1 if key in ('sizeBytes','visionRevision') else '2026-10-20T00:00:00Z' if key.endswith('At') else 'example'
            record.update({k:next(iter(v)) for k,v in ENUMS.get(kind,{}).items()})
            if kind=='Source': record['uri']='synthetic:paper'
            validate(record)
            for field in fields.split():
                invalid=copy.deepcopy(record); del invalid[field]
                with self.assertRaises(ValueError): validate(invalid)
    def test_duplicate_imports(self):
        store={}
        for _ in range(2): store[import_identity('hq','Task',12)]={'title':'Build'}
        self.assertEqual(len(store),1)
        self.assertNotEqual(import_identity('hq','Task',12),import_identity('hq','Run',12))
    def test_cycles_and_unresolved(self):
        f=fixture(); a,b=f['nodes'][:2]; edges=f['edges'][:2]
        for e,source,target in zip(edges,[a,b],[b,a]): e.update(fromId=source['id'],toId=target['id'],relation='depends_on')
        with self.assertRaises(ValueError): validate_graph([a,b],edges)
        edges[1]['relation']='associated_with'; validate_graph([a,b],edges)
        edges[0].update(toId='hq:task:missing',status='unresolved'); validate_graph([a,b],edges)
        edges[0]['status']='proposed'
        with self.assertRaises(ValueError): validate_graph([a,b],edges)
    def test_invalid_identity_revision_and_ai(self):
        for key,value in [('id','bad'),('revision',0),('schemaVersion',2),('createdAt','yesterday'),('provenanceIds','bad')]:
            n=fixture()['nodes'][0]; n[key]=value
            with self.assertRaises((ValueError,KeyError)): validate(n)
        e=fixture()['edges'][0]; e['origin']='ai'
        with self.assertRaises(ValueError): validate(e)
