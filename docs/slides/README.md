# Workshop deck: open-ai-silicon-osi.pptx

**Scope, read first.** These files are copied unchanged from the sibling repository `../open-ai-silicon` (`docs/slides/`; byte-identical,
checked with `cmp`) as reference material (`provenance/SOURCES.md`, `README.md` last section). The deck describes that repository's designs
(`spm_ref`, `counter_wb`, `uart`, `ring_osc`, `pwm_dac`, `sigma_delta`, `aes128`, `bnn_mnist`, `cnn_lite`, `fcnn_mnist`, `cnn_fp16`, ...), none
of which exist under `designs/` here, and it reads its numbers from files of that repository (`build/v4/*.log`, `build/results/_flow_stages/cnn_lite/`,
`build/labs/`, `docs/SPEC.md`). It does NOT cover this repository's 25 designs (tiny AI engines, precision study, KV-cache family, SoC macros,
wrappers), and it is not regenerated here: run the commands below from a clone of `open-ai-silicon`, not from this tree, where the data files are missing.
For this repository's results see `README.md` and `designs/<d>/NOTES.md`.

`build_deck.js` writes `open-ai-silicon-osi.pptx` (16:9). Every number is read from the repository at build time
(`designs/<design>/output/metrics.json` and `resources.json`, `build/v4/*.log`, `build/results/_flow_stages/cnn_lite/*.json`);
the source of each number is in that slide's speaker notes.

## Regenerate (from a clean clone of open-ai-silicon)

    cd docs/slides
    npm install                      # pptxgenjs 4.0.1 into docs/slides/node_modules (do not commit it)
    NODE_PATH=$PWD/node_modules PPTX_APPLY_THEME=<path>/scripts/apply_theme.js node build_deck.js

Environment variable read by the generator: `PPTX_APPLY_THEME` (or `APPLY_THEME_JS`), the path of `apply_theme.js`
from the pptx tooling (the Claude `pptx` skill, `scripts/apply_theme.js`). That script finds `jszip` through pptxgenjs, so `NODE_PATH` must point at `docs/slides/node_modules` as shown.
It only writes the theme colours into the file. If it is not available, leave the variable unset: the deck is
still produced and a note is printed; the theme colours then show as Office defaults when someone restyles the deck,
slide appearance is unchanged because colours are set explicitly.

Node 22 and npm 10 were used.

## Images (embedded when present, labelled placeholders otherwise)

- Layout pictures: `build/results/<design>/layout.png`, made by `make results` (needs Docker, the PDK and the LibreLane image).
- Flow-stage pictures and timings: `build/results/_flow_stages/cnn_lite/` (PNG, `stages.json`, `tools.json`), made by
  `make flow-stages DESIGN=cnn_lite`.
- Gate-level and register checks shown on the checks slide: `build/v4/gl_all.log`, `gl_final.log`, `check_all.log`
  (from `make gl-all`, `make gl-all NETLIST=final`, `make check-all`).

## Check text fit (no PowerPoint needed)

    pip install python-pptx Pillow
    python3 check_fit.py open-ai-silicon-osi.pptx

It wraps every text box with Arial metrics (macOS font path; edit `D` in the script elsewhere) and lists boxes whose
text needs more height than the box has. Real PowerPoint rendering has not been checked.
