# v2.6 — shared history and measured ML lifecycle

History is shared across the single organisation, while submission ownership remains on reviews and audit entries. Late arrivals are retained without scoring and enter future history. A transactional migration merges identical legacy events and refuses conflicting IDs. The public v2.5 release-note section containing a local Windows path has been removed from the current tree; it remains visible in Git history unless separately rewritten.

The ML study generates 1,200 Sparkov profiles / 1,197 active accounts and 523,605 transactions, compares five feature groups, fits separate sigmoid calibration and reports account-bootstrap uncertainty and prevalence-relative AP. The full feature model's generalisation result is worse than the time-only ablation, so it is included as a shadow candidate, not an automatic replacement.

Newly arrived verified labels can train a candidate from saved feature snapshots. Shadow scoring records paired outcomes without changing live responses; a second administrator promotes only after declared quality gates. Activation, previous-version pointers and registered model artifacts survive restart and backup.

The workspace accepts richer transaction context and exposes shadow controls. A 60-second stepped GIF uses real local browser captures with synthetic records. Four primary guides now organise the documentation.

Validation: 121 Python tests and 7 JavaScript tests pass; local browser login, submission, review and monitoring pass. The load report measures the actual ingestion transaction on local Windows storage. Docker build and CI must be rerun on this branch after publication. The verified existing AWS deployment remains v2.5.0; no v2.6 cloud rollout or prospective shadow-quality claim is made.

See [Operations](OPERATIONS.md) before upgrading. Back up first, keep the existing volume/configuration, and do not combine this application upgrade with an EBS migration. After accepting v2.6 traffic, returning to v2.5 application code needs a reviewed data-recovery plan because the older code cannot see the new shared-history tables. Behavioral **model** rollback within v2.6 is a separate supported operation.
