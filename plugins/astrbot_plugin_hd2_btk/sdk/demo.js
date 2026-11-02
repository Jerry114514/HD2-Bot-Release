const sdk = require('./index.js');
const r = sdk.compute({ weapon: 'AR-23', enemy: '武斗虫', part: '头部' });
console.log(r.weapon, '打', r.enemy + '·' + r.part);
console.log('  BTK', r.btk, '次击发 / TTK', r.ttk == null ? '—' : r.ttk.toFixed(2) + ' s');
console.log('  路径', r.label, '| 单发', r.perShotDamage);
const st = sdk.scanStuck({ weapon: 'P/40-K' });
console.log('卡阈值组合', st.length, '个，例如', st[0] && (st[0].enemy + '·' + st[0].part));
