# Birdview runtime distribution

Source: https://github.com/Qiuner/birdview
Commit: cdea5340f9710d71c9375e1ca99910c6f59c9704 (0.4.0), MIT.
Includes upstream scripts, schemas, references, assets and contract examples (pre-rendered demo HTML omitted).
Local adaptation: render.mjs/render-review.mjs read icons/ instead of node_modules/lucide-static/icons/.
Only used Lucide 1.41.0 icons are included; licenses in THIRD_PARTY_NOTICES.
package.json contains only renderer dependencies, pinned in package-lock.json.
Source TypeScript and development/site/test tooling are maintained upstream.
