import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const catalog=JSON.parse(await readFile(new URL('../data/moscow-universities.json',import.meta.url),'utf8'));
const sources=new Map(catalog.sources.map(row=>[row.id,row]));
const checkedIds=['markhi-main','rgust-main','gup-radio','mai-volokolamskoe','mephi-kashirskoe','mipt-dolgoprudny','mgsu-yaroslavskoe','stankin-vadkovsky','mtusi-aviamotornaya','miet-zelenograd','gubkin-leninsky','timiryazev-timiryazevskaya','pushkin-volgina','fa-leningradsky','fa-kibalchicha','fa-zlatoustinsky','sechenov-pirogovskaya'];
checkedIds.push('mgimo-vernadskogo','mpgu-vernadskogo88','gnesin-povarskaya','mgpu-selkhoz','guu-ryazansky');

test('all saved locations have an institution source independently of the admissions portal',()=>{
  for(const row of catalog.campuses){
    assert(row.sourceIds.some(id=>sources.get(id)?.evidenceType==='official-university'&&!new URL(sources.get(id).url).hostname.endsWith('studyinrussia.ru')),row.id);
  }
});

test('the twenty-two reviewed locations retain dated institution and object evidence',()=>{
  for(const id of checkedIds){
    const row=catalog.campuses.find(value=>value.id===id),evidence=row?.addressEvidence;
    assert(row,'existing stable campus ID: '+id);assert.equal(evidence?.type,'official-university');
    assert.match(evidence.checkedAt,/^\d{4}-\d{2}-\d{2}$/);assert(evidence.note.length>40);
    assert(['institution-page','institution-search-excerpt'].includes(evidence.method));
    for(const sourceId of evidence.sourceIds){
      const source=sources.get(sourceId);assert(source,'source '+sourceId);assert(row.sourceIds.includes(sourceId));
      assert.equal(source.institutionId,row.universityId);assert(source.campusIds.includes(row.id));
      assert.equal(source.evidenceType,'official-university');assert.equal(source.verifiedAt,evidence.checkedAt);
      assert.equal(new URL(source.url).protocol,'https:');assert(source.supports.includes('campus-address'));
      assert.notEqual(new URL(source.url).pathname,'/','a homepage is not object-address proof');
      if(evidence.method==='institution-search-excerpt'){
        assert.equal(source.retrievalMethod,'primary-search-excerpt');assert(source.directFetchStatus);
        assert.equal(source.sha256,undefined,'an indexed excerpt must not acquire an invented response hash');
      }
    }
    assert.equal(row.proximityVerified,false);assert.equal(row.latitude,undefined);assert.equal(row.longitude,undefined);
  }
});

test('administrative buildings and exact neighbouring address objects are not conflated',()=>{
  const get=id=>catalog.campuses.find(row=>row.id===id);
  assert.equal(get('sechenov-pirogovskaya').locationType,'administrative');
  assert.equal(get('sechenov-pirogovskaya').addressEvidence.scope,'administrative-address');
  assert.match(get('mai-volokolamskoe').addressEvidence.note,/административным/);
  assert.match(get('mipt-dolgoprudny').addressEvidence.note,/не юридический адрес/);
  assert.equal(get('fa-zlatoustinsky').addressRu,'Москва, Малый Златоустинский переулок, д. 7, стр. 1');
  assert.match(get('mtusi-aviamotornaya').addressEvidence.note,/координаты относятся к Народного Ополчения 32/);
  assert.match(get('rgust-main').addressEvidence.note,/учебные аудитории/);
  assert.equal(get('mgpu-selkhoz').addressRu,'Москва, 2-й Сельскохозяйственный проезд, д. 4, корп. 1');
  assert.equal(get('guu-ryazansky').locationType,'contact_address');
  assert.match(get('mpgu-vernadskogo88').addressEvidence.note,/сезонный график/);
});
