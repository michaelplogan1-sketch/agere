# Connected Agere site

Owned snapshot of the designed site at 9cf1febd08551d886d578f68ba8b6aaa3560f33f. The original artifacts/agere-interactive-site checkout is read-only. No hosting identity or credentials are copied.

Run `npm ci` then `npm run build` here. The production bundle is tracked in clinical_core/agere/site_dist so frozen Python releases can serve it without Node. The existing Agere service serves it at / and /site/. All questions use the existing same-origin compose API. Saved answers open the existing evidence, correction and follow-up workspaces. A browser refresh loads the saved context; it does not submit another model request.

This is a local integration. The separately hosted Sites preview remains a presentation until an authorized hosted backend is available. No localhost URL is inserted into that hosted site. Existing illustrated care/rewards and dated comparison sections remain demonstrations, not verified live execution.
