# Engineering decision records

These records explain current code and tradeoffs. They are not claims of historical experiments that were never run.

## 001 — Establish timing correctness before distributed infrastructure

**Decision:** independent batch masks and an ordered in-memory processor implement the same `[t-window,t)` feature contract. Equal-time peers are excluded; late arrivals are rejected. Retries return the original result only when the payload matches.

**Reason:** correctness can be tested without hiding it behind message queues. Kafka and Redis would add durability and concurrency concerns, not establish point-in-time correctness themselves.

**Cost:** no production durability, distributed ownership or bounded retry retention. An account-partitioned consumer and persistent feature store are future work. Replay recovery is tested, not an availability SLA. See `behavioral.py` and `test_behavioral.py`.

## 002 — Measure entity generalization separately from chronology

**Decision:** held-out account hashes never enter model fitting/selection; chronological windows and label-arrival cutoffs also apply. Prior *unlabeled* transactions remain available for behavioral features. Report a separate reset-history first-event cohort.

**Reason:** unseen by the model does not mean unseen by the feature system. Conflating those cases overstates cold-start performance. No target encoding or fraud outcomes enter features.

**Cost:** held-out fraud counts are small, and synthetic behavior cannot prove real-bank generalization. Random-split comparisons are descriptive, not a guaranteed performance drop. See `behavioral_experiment.py`.

## 003 — Let selection data choose complexity; separate calibration claims

**Decision:** the existing ULB pipeline keeps the selected logistic model and sigmoid calibration on a separate chronological calibration window. The behavioral track compares logistic regression with histogram boosting using selection AP; it does not call uncalibrated scores calibrated probabilities.

**Reason:** sigmoid has fewer degrees of freedom than isotonic, a useful conservative choice when positive calibration examples are scarce. This is a design rationale, not a measured sigmoid-versus-isotonic victory. The new synthetic reference run selects boosting (selection AP 0.7925 versus 0.7723); that finding applies to this generator and seed only.

**Cost:** calibration must be re-evaluated before deploying a different dataset/model. A 5% selection quantile is a capacity target, not a promise of recall or live review volume. See `training.py` and both model reports.

## 004 — Bound authentication work and document hash migration

**Decision:** existing passwords use salted standard-library scrypt (`n=32768,r=8,p=1`), with two concurrent hash slots. The approximate scrypt working memory is 32 MiB per hash; the implementation sets a 64 MiB maximum. Authenticator secrets are separately encrypted.

**Reason:** memory-hard hashing and bounded parallel work fit this small host without another password-hashing dependency. This is not evidence that scrypt universally beats bcrypt or Argon2.

**Cost:** the stored format has salt and digest but no algorithm/cost version. Future cost/algorithm migration needs a versioned format and rehash-on-login path; benchmark latency and memory on the actual host first. MFA does not fix weak password hashing. See `auth.py`.

## 005 — Retry stateless scoring, not whole user submissions

**Decision:** the optional router picks the least-busy eligible worker, rotating ties, with two in-flight calls per worker, an eight-second transport timeout and a ten-second failure exclusion. Each worker is attempted at most once per score request. All unavailable/busy returns 503.

**Reason:** workers only calculate probabilities for an immutable model version; the coordinator alone saves results. Retrying internal scoring is therefore safe from duplicate database writes.

**Cost:** a client retry after a lost successful API response can duplicate a submission. Workers still share a host/volume and the coordinator is a single point of failure. This is process-level failover, not AWS load balancing or high availability. See `pool.py` and worker tests.
