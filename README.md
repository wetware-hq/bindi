# Bindi

Bindi generates and curates protein binders for a target. A frontier agent fills a **campaign** policy at runtime (protein, modality, hotspots, how many designs to keep). Bindi runs [BindCraft2](https://github.com/PacesaLab/BindCraft2) to design, then writes a **gallery** directory of ranked, scored candidates for experimental follow-up.

## Resource shape

| Slot | Type | Role |
| --- | --- | --- |
| `campaign` | policy (`bindi.policy.v1`) | Target and design parameters |
| `gallery` | directory (`bindi.gallery.v1`) | Curated binders, ranked |

Capabilities: **generate**, **curate**.

```bash
bindi recruit   # JSON descriptor for worker recruitment
```

## Campaign policy

Shipped example (`campaigns/pdl1.json`):

```json
{
  "schema": "bindi.policy.v1",
  "bindcraft": {
    "target": "hPDL1",
    "modality": "binder",
    "number_of_final_designs": 10,
    "project_folder": "results/pdl1"
  },
  "curation": { "rank_on": ["i_pDAE"], "top_n": 10 },
  "gallery": "gallery"
}
```

Bare BindCraft2 JSON is also accepted; bindi wraps it automatically.

From a one-line natural-language intent (starter only — adjust paths and hotspots before a real run):

```bash
bindi stub "VHH binders for human PD-L1" --target hPDL1 --modality VHH --designs 5
```

## Commands

Install (no runtime dependencies beyond Python):

```bash
uv pip install -e .
```

| Command | Action |
| --- | --- |
| `bindi run campaigns/pdl1.json` | `bindcraft design` then build gallery |
| `bindi generate <policy>` | Design only |
| `bindi curate <policy>` | Gallery from an existing `project_folder` |
| `bindi run <policy> --dry-run` | Fixture pipeline without GPU |

Generation requires `bindcraft` on `PATH` (or set `BINDCRAFT_CMD`). Use `--dry-run` to test curation locally.

Gallery layout:

```text
gallery/
  index.json      manifest (bindi.gallery.v1)
  ranked.csv      shortlist table
  structures/     mmCIF files when present in the campaign folder
```

## Container

Package at the source for web workers:

```bash
# After building BindCraft2's image as bindcraft:local
docker build -f containers/Dockerfile -t bindi:local .
docker run --gpus all -v "$PWD:/work" -w /work bindi:local run campaigns/pdl1.json
```

## Python API

```python
from bindi import run, curate, load_policy

policy = load_policy("campaigns/pdl1.json")
outcome = run(policy, dry_run=True)
print(outcome["curate"]["gallery"])
```

## License

MIT. BindCraft2 is a separate dependency with its own license; see the [BindCraft2 repository](https://github.com/PacesaLab/BindCraft2).
