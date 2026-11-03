// Private operator calculation. Never add this result to a client publication.
function paymentMoney(value) {
  if (value === null || value === undefined || value === '') return null;
  if (!['string', 'number'].includes(typeof value)) return null;
  const text = String(value).trim().replace(/[\s\u00a0\u202f]/gu, '').replace(',', '.');
  if (!/^\d+(?:\.\d{1,2})?$/u.test(text)) return null;
  const [whole, fraction = ''] = text.split('.');
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  return Number.isSafeInteger(cents) && cents <= 100_000_000_000 ? cents : null;
}

export function calculateInitialPayment(fields = {}) {
  const missing = [], warnings = [], lines = [];
  const period = fields.pricePeriod || 'month', currency = fields.priceCurrency || 'RUB';
  if (period === 'unknown') warnings.push('Период ставки неизвестен. Подтвердите месячную аренду перед расчётом.');
  else if (period !== 'month') warnings.push('Источник указывает не месячную аренду. Сначала проверьте период платежа.');
  if (currency !== 'RUB') warnings.push('Расчёт в рублях недоступен для валюты источника ' + currency + '.');
  const rent = paymentMoney(fields.price), deposit = paymentMoney(fields.deposit);
  const rentUnitValid=period==='month'&&currency==='RUB';
  const depositUnitValid=!(deposit>0&&fields.depositCurrency&&fields.depositCurrency!=='RUB');
  const commissionUnitValid=!(paymentMoney(fields.commissionAmount)>0&&fields.commissionCurrency&&fields.commissionCurrency!=='RUB');
  if (!depositUnitValid) warnings.push('Валюта залога — ' + fields.depositCurrency + '. Подтвердите сумму в рублях.');
  if (!commissionUnitValid) warnings.push('Валюта фиксированной комиссии — ' + fields.commissionCurrency + '. Подтвердите сумму в рублях.');
  const rawMonths = fields.prepayMonths;
  const months = rawMonths === '' || rawMonths === null || rawMonths === undefined ? 1 : Number(rawMonths);
  const validMonths = Number.isInteger(months) && months >= 1 && months <= 36;
  if (!validMonths) warnings.push('Предоплата должна составлять от 1 до 36 полных месяцев.');
  if (rent === null || rent <= 0) missing.push('Аренда');
  else if (validMonths&&rentUnitValid) lines.push({key:'rent', label:months === 1 ? 'Аренда за первый месяц' : 'Предоплата за ' + months + ' мес.', amount:rent * months / 100});
  if (deposit === null) missing.push('Залог');
  else if(depositUnitValid)lines.push({key:'deposit', label:'Возвратный залог', amount:deposit / 100});

  const percentText = String(fields.commission ?? '').trim().replace(',', '.');
  const percent = /^\d+(?:\.\d{1,4})?$/u.test(percentText) ? Number(percentText) : null;
  const amount = commissionUnitValid?paymentMoney(fields.commissionAmount):null;
  const percentKnown = percent !== null && percent <= 1000;
  const byPercent = percentKnown && rent !== null && rentUnitValid ? Math.round(rent * percent / 100) : null;
  let fee = commissionUnitValid?(amount ?? byPercent):null;
  if (amount !== null && byPercent !== null && amount !== byPercent) {
    warnings.push('Сумма комиссии и процент источника расходятся. Уточните условие перед расчётом.');
    fee = null;
  }
  if (fee === null && fields.commissionStatus === 'none' && !percentText && amount === null) fee = 0;
  if (fee === null) missing.push('Комиссия');
  else lines.push({key:'commission', label:'Комиссия' + (byPercent !== null && amount === null ? ' · ' + percent + '% месячной аренды' : ' · фиксированная сумма'), amount:fee / 100});

  if (Array.isArray(fields.oneTimeFees)) {
    for (const row of fields.oneTimeFees.slice(0, 30)) {
      if (!row || typeof row !== 'object') continue;
      const label = typeof row.label === 'string' ? row.label.slice(0, 150) : 'Разовый платёж';
      // A parser may retain this row as evidence as well as commissionAmount.
      if (/комисси|агентск|commission|中介|佣金/iu.test(label)) continue;
      const value = paymentMoney(row.amount);
      if (value === null || row.currency && row.currency!=='RUB') missing.push(label);
      else lines.push({key:'oneTime', label, amount:value / 100});
    }
  }
  const subtotalCents = lines.reduce((sum, row) => sum + Math.round(row.amount * 100), 0);
  if (!Number.isSafeInteger(subtotalCents)) warnings.push('Сумма платежей выходит за допустимый диапазон.');
  const complete = !missing.length && !warnings.length;
  return {lines, missing, warnings, complete, subtotal:subtotalCents / 100, total:complete ? subtotalCents / 100 : null,
    notes:['Коммунальные услуги, счётчики и интернет не включены: их суммы уточняются отдельно.',
      'Залог показан как платёж при заселении, а не как невозвратный расход.',
      'Это внутренний расчёт оператора. Комиссия не добавляется в китайский текст, PDF или клиентские файлы.']};
}

export function renderInitialPayment(mount, fields) {
  if (!mount) return;
  const result = calculateInitialPayment(fields), fragment = document.createDocumentFragment();
  const money = value => new Intl.NumberFormat('ru-RU', {style:'currency', currency:'RUB', maximumFractionDigits:2}).format(value);
  const title = document.createElement('h3'); title.textContent = 'Первый платёж · для оператора'; fragment.append(title);
  const list = document.createElement('dl');
  for (const row of result.lines) { const label = document.createElement('dt'), value = document.createElement('dd'); label.textContent = row.label; value.textContent = money(row.amount); list.append(label, value); }
  const totalLabel = document.createElement('dt'), total = document.createElement('dd');
  totalLabel.textContent = result.complete ? 'Всего при заселении' : 'Известная часть · не окончательный итог';
  total.textContent = money(result.subtotal); total.className = 'payment-total'; list.append(totalLabel, total); fragment.append(list);
  for (const text of [...result.missing.map(label => 'Неизвестно: ' + label), ...result.warnings, ...result.notes]) { const note = document.createElement('p'); note.className = 'small'; note.textContent = text; fragment.append(note); }
  mount.replaceChildren(fragment);
}
