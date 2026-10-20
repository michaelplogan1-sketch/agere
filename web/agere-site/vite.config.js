import {defineConfig} from 'vite';
const builtAt=new Date().toISOString();
export default defineConfig({
 base: "/site/",
 build: {outDir:"../../clinical_core/agere/site_dist",emptyOutDir:true},
 define:{__SITE_BUILT_AT__:JSON.stringify(builtAt)},
 plugins:[{name:'agere-build-stamp',generateBundle(){this.emitFile({type:'asset',fileName:'build-meta.json',source:JSON.stringify({built_at:builtAt,timezone:'America/Chicago',evidence_reviewed:'2026-10-18'},null,2)})}}]
});
