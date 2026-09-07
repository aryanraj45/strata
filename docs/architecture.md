# System architecture

## The problem this shape solves

The blockchain is a complete record of *what* moved and contains no record of
*who* moved it. Every tool that reads only the ledger inherits that limit.

But a transaction is broadcast across the peer-to-peer network before it is
confirmed, and at that moment it is carried by a machine with an IP address.
That observation is fleeting and local, and it contains no money. The ledger
contains money and no identity. Neither layer is evidence on its own, and they
share no key except approximate time — so the whole system is built around
joining them on that.

## Data flow

```text
  CSV / JSON / XML  +  GeoIP (.mmdb, local)
            |
            v
  [1] Ingest  (Rust)
      one record shape, country/ASN resolved, SHA-256 custody manifest
            |
            +--> tx_l1     ledger:  txid, addresses, amounts (integer satoshis)
            +--> net_l2    network: src_ip, ports, arrival time, arrival rank
                   |
                   v
  [2] Clustering  (Python)
      CoinJoins detected and excluded FIRST, then co-spending, then change
            |
            v  entities
  [3] Graph
      peeling chains recovered as paths -- clustering structurally cannot see
      these, because every hop spends a single input
            |
            v  entities + chains
  [4] Fusion                          <-- the contribution
      for each (entity, host): how often was that host first to relay this
      entity's transactions, against how often it leads traffic generally?
      exact binomial tail, Bonferroni-corrected
            |
            v  attributed leads
  [5] Detection
      RandomForest (known patterns) + IsolationForest (anything new)
      every score decomposed by SHAP
            |
            v
  [6] Explorer  (Streamlit)  — reports, never recalculates
```

## Stages

### 1 · Ingest — Rust

Reads CSV, JSON and XML into one record shape at roughly 40,000 records a
second. Resolves country and ASN from a GeoIP database that ships inside the
repository, recording per-field provenance so a value from the database is
never confused with one the record asserted about itself.

Amounts are parsed as integer satoshis directly from their decimal string. No
value ever passes through a float, because a float cannot represent 0.1 BTC
exactly and evidence that does not add up is not evidence.

Every source file is hashed into a chain-of-custody manifest. Malformed rows are
rejected and reported rather than crashing the run — a forensic tool must not
fall over on one bad line.

### 2 · Clustering — Python

Groups addresses spent together into entities, on the reasoning that unrelated
strangers do not co-sign one transaction.

The exception is CoinJoin, which is exactly strangers co-signing, so CoinJoins
are detected and excluded **before** any clustering happens. Getting this order
wrong merges innocent parties into a suspect's entity, and nothing errors.

Change-address identification is a scored guess, not a rule. It declines rather
than guessing when the signals disagree, and a change call moves cluster
boundaries only when the pairs it risks are few enough to be worth the 6% chance
it is wrong.

### 3 · Graph

Recovers peeling chains — a balance shedding a slice at each hop and carrying
the remainder onward. Clustering cannot see these at all: each hop spends a
single input, so there is never anything spent *together* to link.

### 4 · Fusion — the dual-layer correlation

For each entity, count how often each host was first to relay its transactions,
and compare that against how often the host leads traffic generally. The p-value
is an exact binomial tail, not a normal approximation, which is poor precisely
in the tail that matters here.

Corrected for multiple comparisons. Without that correction, testing hundreds of
host-and-entity pairs at a 0.01 threshold produces false positives *by
construction* — one in a hundred passes by chance.

### 5 · Detection

A RandomForest for known illicit patterns and an IsolationForest given no
labels, so a typology nobody labelled still surfaces instead of scoring zero.
28 features across six groups: volume, value, structure, timing, laundering,
network.

Trees, not a neural network — on tabular data of this size they are stronger,
they train in seconds on a CPU, and SHAP on a tree is *exact* rather than
approximate. The problem statement demands explainable leads.

### 6 · Explorer — Streamlit

Reads the store and presents it as an investigator would use it. It computes
nothing. A dashboard that quietly does its own arithmetic is a second,
unverified implementation of the system, and the two drift apart exactly when it
matters.

## Testing

Every stage has a `verify.py` beside it that scores its output against the
generator's ground truth, including negative controls. The most valuable result
in the project came from one: shuffling the entity labels destroys every real
association, so a sound method must then find nothing — and ours found 41 leads
until multiple comparisons were corrected for.

## Offline by construction

No stage makes an outbound request. The GeoIP database is committed; the
landing page's 3D library is vendored rather than pulled from a CDN. For an
intelligence service the question itself is the secret, and a lookup sent to a
foreign vendor leaks the investigation to the act of investigating.
