# Vendored renderer assets

These JavaScript libraries are vendored (committed, not fetched at runtime) so that
`gristle viz` exports are fully self-contained single-file HTML — no CDN, no network,
no npm. All three are MIT-licensed.

| File | Library | Version | License | SHA-256 |
|------|---------|---------|---------|---------|
| `cytoscape.min.js` | [cytoscape](https://github.com/cytoscape/cytoscape.js) | 3.30.2 | MIT | `83e8c54a6bec655bfd81df07df605649c268af69aeca67a5ea2da54ea42dac81` |
| `dagre.min.js` | [dagre](https://github.com/dagrejs/dagre) | 0.8.5 | MIT | `62eb9787ccfdbdf4148d4d99d31dbf9ee4770eafee81e637d759b52aac22cd51` |
| `cytoscape-dagre.js` | [cytoscape-dagre](https://github.com/cytoscape/cytoscape.js-dagre) | 2.5.0 | MIT | `bf70fe402991dcbff33e05a7e4a5271c78020bb75e85d1c80ab7538e4157112e` |

## Re-vendor / upgrade

```bash
cd src/gristle/viz/assets
CY=3.30.2; DG=0.8.5; CYDG=2.5.0
curl -fsSL "https://cdn.jsdelivr.net/npm/cytoscape@${CY}/dist/cytoscape.min.js"   -o cytoscape.min.js
curl -fsSL "https://cdn.jsdelivr.net/npm/dagre@${DG}/dist/dagre.min.js"           -o dagre.min.js
curl -fsSL "https://cdn.jsdelivr.net/npm/cytoscape-dagre@${CYDG}/cytoscape-dagre.js" -o cytoscape-dagre.js
sha256sum *.js   # update the table above
```

After upgrading, bump the versions + hashes in this table and run `pytest tests/test_viz.py`.
The wheel ships these via `[tool.hatch.build.targets.wheel.force-include]` in `pyproject.toml`.
