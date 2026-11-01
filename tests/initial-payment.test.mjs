import test from 'node:test';
import assert from 'node:assert/strict';
import {calculateInitialPayment} from '../web/initial-payment.mjs';

test('initial payment calculates monthly rent, returnable deposit and percent with cents', () => {
  const value = calculateInitialPayment({price:'80 000,50', deposit:'40000', commission:'50'});
  assert.equal(value.total, 160000.75);
  assert.equal(value.lines.find(row => row.key === 'commission').amount, 40000.25);
  assert(value.notes.some(note => note.includes('внутренний')));
});
test('unknown fees and deposits are never silently counted as zero', () => {
  const value = calculateInitialPayment({price:'65000', deposit:'', commission:''});
  assert.equal(value.total, null); assert.equal(value.subtotal, 65000);
  assert.deepEqual(value.missing, ['Залог','Комиссия']);
  assert.equal(calculateInitialPayment({price:'65000', deposit:0, commission:0}).total, 65000);
});
test('fixed commission, prepayment and independent oneoff fees are kept distinct', () => {
  const value = calculateInitialPayment({price:50000, prepayMonths:2, deposit:25000, commissionAmount:'10000', oneTimeFees:[{label:'Уборка при заселении',amount:'2000',currency:'RUB'},{label:'Комиссия',amount:'10000',currency:'RUB'}]});
  assert.equal(value.total,137000); assert.equal(value.lines.length,4);
  assert.equal(calculateInitialPayment({price:50000,deposit:0,commissionStatus:'none'}).total,50000);
});
test('ambiguous commission, daily rent, foreign currencies and invalid types cannot produce a final total', () => {
  for (const extra of [{commission:50,commissionAmount:4000},{pricePeriod:'day'},{pricePeriod:'unknown'},{priceCurrency:'unknown'},{priceCurrency:'USD'},{depositCurrency:'USD'},{commissionAmount:'100',commissionCurrency:'USD'},{prepayMonths:1.5},{price:[]},{deposit:-1},{commission:'NaN'},{commission:2001},{deposit:'1e3'}]) {
    assert.equal(calculateInitialPayment({price:80000, deposit:10000, commission:0,...extra}).total,null,JSON.stringify(extra));
  }
});
test('foreign fee amounts remain private evidence and never enter a ruble subtotal',()=>{
  const value=calculateInitialPayment({price:80000,deposit:1000,depositCurrency:'USD',commissionAmount:100,commissionCurrency:'USD'});
  assert.equal(value.total,null);assert.equal(value.subtotal,80000);
  assert.deepEqual(value.lines.map(row=>row.key),['rent']);
});
