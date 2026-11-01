// Reference only: not wired into Viewer/81. Same fixtures/ranges as Python.
'use strict';
const {forbidden_ranges:R}=require('./label-boundary.json');
const validLabel=s=>typeof s==='string'&&[...s].length>0&&[...s].length<=128&&s.trim()!==''&&![...s].some(c=>R.some(([lo,hi])=>lo<=c.codePointAt(0)&&c.codePointAt(0)<=hi));
module.exports={validLabel};
if(require.main===module){let b='';process.stdin.setEncoding('utf8');process.stdin.on('data',s=>b+=s);process.stdin.on('end',()=>{process.stdout.write(JSON.stringify(JSON.parse(b).map(validLabel)));});}
