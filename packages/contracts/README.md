# Atlas contracts
`packages.contracts.validate` is the executable version-one record contract for all 17 entities in DATA_MODEL. `validate_graph` checks identity, explicit unresolved Engine references and dependency cycles, including capability prerequisites. General associations may cycle. `import_identity` provides tuple-scoped deterministic UUIDs for import upserts. Adapter persistence and evidence re-evaluation belong to later integration tasks.
Run `python -m unittest discover -s tests -v` from the repository root. The committed 200-node/400-edge fixture contains synthetic data only.
