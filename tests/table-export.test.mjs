import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {TABLE_EXPORT_COLUMNS,projectTableRow,normalizeTableFilters,filterTableRows,tableExportValues,tableCsv} from '../web/table-export.mjs';

const card=(id,extra={})=>({id,title:'莫斯科房源',url:'https://www.cian.ru/rent/flat/123/?auth=PRIVATE_QUERY#private',district:'Лианозово',address:'Москва, Лесная улица, 12',metro:'Сокол',rooms:'2',area:'52',floor:'14',floors:'17',price:'80000',deposit:'40000',commission:'50',commissionStatus:'present',pricePeriod:'month',priceCurrency:'RUB',depositCurrency:'RUB',commissionCurrency:'RUB',prepayMonths:'1',workflow:{status:'ready',tasks:[{text:'PRIVATE_TASK'}]},source:'PRIVATE_HTML',notes:'PRIVATE_NOTES',phone:'+79991234567',contract:'PRIVATE_CONTRACT',...extra});
const column=key=>TABLE_EXPORT_COLUMNS.findIndex(row=>row[0]===key);

test('table export projects only approved operator columns while retaining separate payment units',()=>{
 const original=card('a'),before=structuredClone(original),row=projectTableRow(original);assert.deepEqual(original,before);assert.equal(row.url,'https://www.cian.ru/rent/flat/123/');
 assert.equal(row.price,80000);assert.equal(row.deposit,40000);assert.equal(row.commission,50);assert.equal(row.pricePeriod,'month');assert.equal(row.status,'ready');assert.equal(row.floor,14);
 assert.doesNotMatch(JSON.stringify(row),/PRIVATE|phone|contract|notes|tasks|source/);
 assert.equal(tableExportValues(row)[column('firstTotal')],160000);
});
test('filters use actual district room status and monthly ruble budget fields',()=>{
 const cards=[card('good'),card('cheap',{price:'50000'}),card('other',{district:'Хамовники'}),card('room',{rooms:'1'}),card('daily',{price:'3500',pricePeriod:'day'}),card('foreign',{price:'850',priceCurrency:'USD'}),card('unknown',{pricePeriod:'unknown'}),card('archived',{workflow:{status:'archive'}})];
 const result=filterTableRows(cards,{minRent:70000,maxRent:90000,district:'лиан',rooms:2,status:'ready'});assert.deepEqual(result.rows.map(row=>row.id),['good']);assert.equal(result.excluded.length,7);
 assert(result.excluded.find(row=>row.id==='daily').reasons.includes('месячная рублёвая ставка не подтверждена'));
 assert.throws(()=>normalizeTableFilters({minRent:90000,maxRent:70000}));assert.throws(()=>normalizeTableFilters({rooms:'1.5'}));
});
test('nested cards export the same actual facts without serializing their private envelope',()=>{
 const flat=card('a'),nested={id:'server-a',fields:flat,titles:['服务器标题'],body:'PRIVATE_BODY'};const row=projectTableRow(nested);assert.equal(row.title,'服务器标题');assert.equal(row.id,'server-a');assert.equal(row.price,80000);assert.doesNotMatch(JSON.stringify(row),/PRIVATE/);
});
test('unknown and foreign payment units leave final first payment blank instead of converting',()=>{
 for(const extra of [{pricePeriod:'day'},{priceCurrency:'unknown'},{depositCurrency:'USD'},{commission:'',commissionAmount:100,commissionCurrency:'USD'},{deposit:''},{commission:'',commissionStatus:'unknown'}]){
  const values=tableExportValues(projectTableRow(card('a',extra)));assert.equal(values[column('firstTotal')],null,JSON.stringify(extra));assert.equal(values[column('paymentStatus')],'Требует уточнения');
 }
 const foreign=tableExportValues(projectTableRow(card('a',{depositCurrency:'USD',deposit:1000,commission:'',commissionAmount:100,commissionCurrency:'USD'})));assert.equal(foreign[column('firstSubtotal')],80000);
});
test('zero deposits/fees, prepayment and known extra charges stay distinct',()=>{
 const values=tableExportValues(projectTableRow(card('a',{deposit:0,commission:0,prepayMonths:2,oneTimeFees:[{label:'Уборка',amount:'1500',currency:'RUB',source:'PRIVATE_SOURCE'}]})));assert.equal(values[column('firstTotal')],161500);assert.equal(values[column('otherFees')],1500);
 const unknown=tableExportValues(projectTableRow(card('a',{deposit:'',commission:''})));assert.equal(unknown[column('deposit')],null);assert.equal(unknown[column('commission')],null);
});
test('CSV neutralizes spreadsheet formulas and removes phone numbers without losing Chinese or zero',()=>{
 for(const title of ['=HYPERLINK("https://bad.example")',' +CMD','-cmd','@SUM(1)','\t=1+1','\u200b=1+1']){
  const row=projectTableRow(card('a',{title})),csv=tableCsv([row]);assert(csv.startsWith('\ufeff'));assert(csv.includes('"\''+title.replace(/"/g,'""')+'"'),title);assert.doesNotMatch(csv,/PRIVATE/);
 }
 const csv=tableCsv([projectTableRow(card('a',{title:'房源; "厨房" +7 (999) 123-45-67 和 +86 13812345678',deposit:0,commission:0}))]);assert.match(csv,/房源; ""厨房""/);assert.match(csv,/电话|телефон скрыт/);assert.doesNotMatch(csv,/999\)|13812345678/);assert(csv.includes('"0"'));
});
test('export rejects invalid numeric fields, duplicate IDs and unprojected secrets',()=>{
 for(const extra of [{price:-1},{deposit:'NaN'},{commission:1001},{area:{}},{rooms:1.5},{prepayMonths:0},{priceCurrency:'CNY'}])assert.throws(()=>projectTableRow(card('a',extra)),JSON.stringify(extra));
 assert.throws(()=>filterTableRows([card('a'),card('a')]));assert.throws(()=>tableCsv([{...projectTableRow(card('a')),source:'PRIVATE_HTML'}]));
 assert.throws(()=>filterTableRows(Array.from({length:5001},(_,i)=>card(String(i)))));
});
test('Python boundary computes the same monetary values as the browser for accepted projections',()=>{
 const rows=[card('a'),card('b',{deposit:0,commission:0,prepayMonths:2}),card('c',{price:'80000.50',deposit:'40000',commission:'50'}),card('d',{pricePeriod:'day'}),card('e',{commission:'',commissionAmount:'100',commissionCurrency:'USD'})].map(projectTableRow);
 const script='import sys,json;sys.path.insert(0,"cli");from table_export import normalize_row,initial_payment;print(json.dumps([{k:(None if v is None else str(v)) for k,v in initial_payment(normalize_row(r)).items()} for r in json.load(sys.stdin)]))';
 const python=spawnSync('python3',['-c',script],{input:JSON.stringify(rows),encoding:'utf8',cwd:new URL('..',import.meta.url)});assert.equal(python.status,0,python.stderr);const results=JSON.parse(python.stdout);
 rows.forEach((row,index)=>{const values=tableExportValues(row);for(const key of ['firstTotal','firstSubtotal','otherFees'])assert.equal(results[index][key]===null?null:Number(results[index][key]),values[column(key)]);assert.equal(results[index].paymentStatus,values[column('paymentStatus')]);});
});
