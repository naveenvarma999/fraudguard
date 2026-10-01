export const FEATURES = ['Amount', ...Array.from({length:28}, (_, i) => `V${i+1}`)];
export function parseCSV(text) {
  text = text.replace(/^\uFEFF/, '');
  const rows=[]; let row=[], value='', quoted=false, closed=false;
  for(let i=0;i<text.length;i++) {
    const c=text[i];
    if(quoted) { if(c==='"') { if(text[i+1]==='"'){value+='"';i++;}else {quoted=false;closed=true;} }else value+=c; continue; }
    if(c==='"') {if(value || closed)throw Error('Invalid CSV quotation.'); quoted=true;}
    else if(c===',' || c==='\n' || c==='\r') {
      row.push(value);value='';closed=false;
      if(c!==','){if(c==='\r' && text[i+1]==='\n')i++;if(row.some(v=>v.trim()))rows.push(row);row=[];}
    } else {if(closed)throw Error('Unexpected text after CSV quote.');value+=c;}
  }
  if(quoted)throw Error('Unclosed CSV quotation.');
  if(value || row.length){row.push(value);rows.push(row);}
  const headers=rows.shift()?.map(v=>v.trim());
  if(!headers?.length || new Set(headers).size!==headers.length)throw Error('CSV needs unique column headers.');
  return rows.map((values,i)=>{if(values.length!==headers.length)throw Error(`CSV row ${i+2} has the wrong number of columns.`);return Object.fromEntries(headers.map((h,j)=>[h,values[j]]));});
}
export function normalizeInput(input, csv=false) {
  let rows=Array.isArray(input)?input:input?.transactions??[input];
  if(!Array.isArray(rows)||!rows.length||rows.length>100)throw Error('Provide between 1 and 100 transactions.');
  const ids=new Set();
  rows=rows.map((r,i)=>{
    if(!r || typeof r!=='object'||Array.isArray(r))throw Error(`Row ${i+1} must be an object.`);
    const unknown=Object.keys(r).filter(k=>![...FEATURES,'transaction_id','Time','Class'].includes(k));
    if(unknown.length)throw Error(`Row ${i+1}: unexpected column ${unknown[0]}.`);
    const id=r.transaction_id??`ROW-${String(i+1).padStart(3,'0')}`;
    if(typeof id!=='string'||!/^[A-Za-z0-9_-]{1,64}$/.test(id)||ids.has(id))throw Error(`Row ${i+1}: use a unique transaction ID (letters, numbers, _ or -).`);
    ids.add(id);const out={transaction_id:id};
    for(const f of FEATURES){const raw=r[f];const n=csv&&typeof raw==='string'&&raw.trim()!==''?Number(raw):raw;
      if(typeof n!=='number'||!Number.isFinite(n)||n<(f==='Amount'?0:-10000)||n>(f==='Amount'?1e9:10000))throw Error(`Row ${i+1}: ${f} is missing or outside the allowed numeric range.`);
      out[f]=n;
    }return out;
  });
  if(new TextEncoder().encode(JSON.stringify({transactions:rows})).length>262144)throw Error('The request exceeds 256 KiB. Use fewer rows.');
  return rows;
}
export function resultsCSV(rows, response, source) {
  const escape=v=>'"'+String(v).replace(/^[=+@-]/,"'$&").replaceAll('"','""')+'"';
  return [['transaction_id','Amount','fraud_probability','decision','threshold','model_version','source'],...response.predictions.map(p=>[p.transaction_id,rows.find(r=>r.transaction_id===p.transaction_id)?.Amount,p.fraud_probability,p.decision,response.threshold,response.model_version,source])].map(r=>r.map(escape).join(',')).join('\r\n');
}
