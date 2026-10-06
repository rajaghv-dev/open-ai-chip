# Demo 8: proof that it is local, tied to this repo, and what context it was given

Three claims, each shown with something you can check yourself in a terminal. Runner: `examples/hermes_desktop/demos/proof_demo.py`
(`make demo-proof`, or `/demo-proof` in the chat for the chat-only version). Docs: docs/HERMES_DESKTOP.md section
"Proof: local, repo, context"; code: `tool_server/proof_tools.py`, `receipt_filter.py`, `memory_filter.py`, `desktop/app.py`.

## What you need

- `make hermes` has been run once (it starts Ollama, the tool server and Open WebUI and installs the filters). Nothing else.
- Time: about 50 s with `--pace 0`, plus 6 s per act at the default pace. Each turn is a saved Open WebUI chat titled
  "Demo 8 proof: ..." (tag `demo`); the transcript is `examples/hermes_desktop/demos/proof.md`.

## Run it

```
make demo-proof                                                       # or: bash scripts/hermes.sh demo proof
build/agent/venv/bin/python examples/hermes_desktop/demos/proof_demo.py --pace 6    # paced for an audience
```

Exit 0 means every check matched (verdict LOCAL, receipt sha256 equals `shasum -a 256`, receipt commit equals `git rev-parse HEAD`,
the memory entry count went up). Exit 1 names the check that failed.

## The steps

| # | what happens | what you see | what to say | snapshot |
|---|---|---|---|---|
| 1 | `proof_local` measures the stack now, then the same two facts are printed with plain `ollama ps` and `lsof` | `VERDICT: LOCAL`, every listener on 127.0.0.1 (ollama 11434, tool server 8770, Open WebUI 8080), `hermes3:8b 6.0 GB 100% GPU`, the offline and telemetry env read from the running Open WebUI process, `outbound ...: none` | The model, the UI and the tools all listen on loopback only, the model sits in GPU memory here, and no process of the stack has an outside connection right now. This is a measurement of this moment, not a promise; step 5 is the strict test. | `demos/proof.md` act 1 |
| 2 | A repo question goes through a real chat (`read_metrics` flip-flop count of `kv_attn_n8`); the answer carries a receipt | the answer, a source chip `designs/kv_attn_n8/output/metrics.json`, and under it the receipt: model digest, `127.0.0.1:11434`, repo commit, the tool and the file with its short sha256, context, speed | The receipt is written by code from the tool server's call log, not by the model. It names the file the number came from and its hash. | `demos/img/proof_receipt.png` |
| 3 | The demo runs `shasum -a 256 designs/kv_attn_n8/output/metrics.json` and `git rev-parse HEAD` and compares | `receipt file sha256 5ebfd66c vs shasum 5ebfd66c: MATCH`, `receipt commit 811c87d vs git rev-parse HEAD 811c87d: MATCH` | You can repeat both commands in any terminal; if someone edited the file or the checkout moved, the hashes would differ and the receipt would show `*` or a new commit. | `demos/proof.md` act 2 |
| 4 | `remember` saves "proof demo marker HHMMSS"; the question is asked again (`recall`) | the second receipt says `memory digest 8 entries` where the first said `7`, and its sha256 changed | The memory digest is read live from `build/agent/memory` on every turn by the memory filter; nothing is baked in. The demo removes its note again with `forget`. | `demos/proof.md` act 3 |
| 5 | `show_context` | the system prompt (chars, tokens estimate, sha256, "starts with tools/prompts/master_prompt.txt"), the tools prompt sha256, the memory digest it injected, the retrieved passages as `file:lines`, the tools and files of the turn | This is exactly what the last turn was sent. Verify the prompt by hand: `shasum -a 256 tools/prompts/master_prompt.txt`. | `demos/img/proof_context.png` (the "Context" window of the desktop app shows the same) |
| 6 | the last line suggests the airplane-mode check | `The strongest check is yours: turn Wi-Fi off ... and run demo 2` | Turn Wi-Fi off and run `make demo-kv`: it still works, because nothing here needs the network. The script never changes network settings. | none |

## Chat-only version

`/demo-proof` in Open WebUI runs steps 1, 2, 4 and 5 as chat turns (`demo_steps proof`); the receipt appears under each answer. The terminal
commands of step 3 are only in the runner.

## Honest limits

- `proof_local` looks at TCP sockets and processes of the stack at one moment (`lsof`, `ps`, `ollama ps`); it does not capture UDP or prove the
  absence of traffic later. The airplane-mode run is the strict test.
- The receipt proves which files the tool opened and what they hashed to, not that the 8B model quoted them correctly; read the answer against the file.
- `~tokens` are an estimate (4 characters per token); speed comes from Ollama's own eval counters.
