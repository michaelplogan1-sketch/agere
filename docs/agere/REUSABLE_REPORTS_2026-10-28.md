# Source-bound reusable HTML and PDF reports

The ordinary saved-report mode previously displayed the final table without its
supporting medication records and whole-document findings. It also offered browser
printing when a PDF was requested. The shared renderer now carries those already
executed observations into a searchable patient dashboard and generates a real PDF
when the request names that format. Rendering makes no model request and adds no
clinical interpretation or independent acceptance. Analytical uncertainty remains
visible.

Install `requirements-reporting.txt` in the application environment. The PDF embeds
the complete saved context as JSON. HTML and PDF bytes have recorded hashes; format
downloads validate the current source, evidence bundle and artifact integrity.
Font selection uses installed Arial when available and ReportLab's packaged Vera
otherwise. PDF rendering is deterministic for the same context and dependencies.

`POST /api/stack/action-report` accepts the existing exact saved-report request:
`context_id`, `template`, and `request`. It creates a new presentation of the saved
answer without rerunning analysis. `GET /api/stack/action-deliverable` retains its
JSON response by default; `format=html` or `format=pdf` returns a recorded,
hash-verified format. Existing artifacts and analytical results remain preserved.

The focused checks exercise source invalidation, HTML escaping, complete context
attachment, PDF tampering, retained supporting observations and deterministic
rendering. The wider offline verification includes the existing conversation,
workflow, cost, startup and recovery contracts. Offline checks qualify engineering
behavior; they do not measure clinical accuracy or model superiority.

Release identities now include HTML assets alongside Python, JavaScript, JSON,
CSS and dependency requirements, closing the previously separate UI-shell pin.
