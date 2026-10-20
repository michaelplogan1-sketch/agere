// Exercise the actual shipped bundle's event interpretation without a browser/GPU.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const ctx={AbortController};vm.createContext(ctx);vm.runInContext(fs.readFileSync('clinical_core/agere/assets/neural-field.bundle.js','utf8'),ctx);
const change=(a,b)=>JSON.parse(JSON.stringify(ctx.AgereNeural.activityChanges(a,b)));
const base={source_id:'a',answered:10,reusable_examples:2,corrections:1,records:100};
assert.deepEqual(change(null,base),{baseline:true,sourceChanged:false});
assert.deepEqual(change(base,base),{baseline:false,answers:0,examples:0,corrections:0,records:0});
assert.deepEqual(change(base,{...base,answered:11,reusable_examples:3,records:150}),{baseline:false,answers:1,examples:1,corrections:0,records:50});
assert.deepEqual(change(base,{...base,source_id:'b',answered:500}),{baseline:true,sourceChanged:true});
assert.equal(change(base,{...base,answered:3}).answers,0);
assert.equal(change({...base,answered:null},base).answers,0);
assert.equal(change(base,{...base,answered:null}).answers,0);
assert.equal(change(base,{...base,corrections:2}).examples,0);
console.log('8 activity delta checks passed');
