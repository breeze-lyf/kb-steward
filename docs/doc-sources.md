# Document sources

`agents/docsource.py` defines synchronous publishing adapters; curator runs network operations in a worker thread. `publish(prop, message, trailers)` returns `kind=merged` with a commit or `kind=pr` with URL and number. `poll()` emits merged/closed proposal events and upstream changes. `push_main(message)` commits owner-approved review metadata and publishes it directly.

Local Git merges proposal branches. GitHub creates a single commit above origin's base and opens a PR, keeping local main unchanged until polling sees publication.

Notion and Confluence are interface stubs. A page maps to a document; page properties map to front matter (`owner`, `last_reviewed`). Publishing maps to a page version or change request. Polling maps to `last_edited_time` (or the platform's version cursor). Implement authentication, stable IDs, conflict handling and persisted cursors before enabling either adapter.

To add an adapter, implement `DocSource`, add its configuration to `make_source`, normalize publication events to the existing contract, and test idempotency and delayed corrections offline. Keep credentials out of persisted state, URLs and exception text.
