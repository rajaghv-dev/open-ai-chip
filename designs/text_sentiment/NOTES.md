# text_sentiment: design notes

Numbers below are quoted from files in `output/` (metrics.json, resources.json, flow.log, reports/*) and from the RTL.
Paths are relative to this folder.

## What it is

A tiny sentence classifier. It reads four tokens from the vocabulary {PAD, GOOD, FINE, BAD}, looks each one up in a
small table of signed 3-bit numbers (PAD 0, GOOD +1, FINE 0, BAD -1, from `rtl/text_sentiment_rom.v`), adds them up,
and answers 1 ("positive") when the sum is greater than 0 (a sum of 0 counts as negative).
It replies with two 8-bit beats: `{6'b0, error, class}` then the signed score. Result arrives 1 cycle after the last
input beat (see `rtl/text_sentiment.v`).

It is AI in the same way a language model is: words become numbers through an embedding table, the numbers are
combined, and a threshold gives the label. This chip is that idea shrunk to a 4-entry table and one adder; see
[../../docs/WHY_AI.md](../../docs/WHY_AI.md), section 3.3.

## Architecture

```mermaid
flowchart LR
    subgraph IN["Input stream"]
        sv["s_valid"]
        sd["s_data 8 bits"]
        sl["s_last"]
        sr["s_ready out"]
    end
    subgraph CORE["text_sentiment"]
        bad["bad_item = OR of s_data 7 down to 2"]
        rom["ROM: text_sentiment_rom<br/>2-bit token to signed 3-bit embedding"]
        add["adder: acc + sign-extended embedding"]
        acc["acc 5 bits signed"]
        cnt["count 3 bits, saturates at 4"]
        err["error 1 bit"]
        fsm["state FSM: LOAD, OUT0, OUT1"]
        cls["class = acc greater than 0"]
        mux["output mux"]
    end
    subgraph OUT["Output stream"]
        mv["m_valid"]
        md["m_data 8 bits"]
        ml["m_last"]
        mr["m_ready in"]
    end
    sd -->|"bits 1:0"| rom --> add --> acc
    sd --> bad
    bad --> err
    bad -->|"block add"| add
    sv --> fsm
    sl --> fsm
    cnt --> err
    cnt -->|"in_frame = count less than 4"| add
    fsm --> sr
    fsm --> mv
    fsm --> ml
    mr --> fsm
    acc --> cls --> mux
    acc --> mux
    err --> mux
    fsm --> mux
    mux --> md
```

Plain words: while `state` is LOAD the design is ready (`s_ready`). Each accepted beat looks up the token, adds the
embedding to `acc`, and bumps `count`. On the beat marked `s_last` the FSM moves to OUT0 and offers beat 0
(error, class); after it is taken it offers beat 1 (score, `m_last`), then clears everything and returns to LOAD.

| Register | Width | Purpose (from `rtl/text_sentiment.v`) |
|---|---|---|
| `state` | 2 | FSM: 0 = ST_LOAD (accepting tokens), 1 = ST_OUT0 (offering class beat), 2 = ST_OUT1 (offering score beat) |
| `count` | 3 | Beats received in this frame; counts up only while `count < 4`, so it saturates at 4 |
| `acc` | 5 | Signed running sentiment sum, range -16..12, cannot overflow for four 3-bit signed terms |
| `error` | 1 | Sticky flag: a token above 3, a frame longer than 4 beats, or `s_last` before the 4th beat |

Flip-flop total by RTL: 2 + 3 + 5 + 1 = 11 bits. The ROM and the class compare are combinational (no flops).

Flip-flop total after synthesis: 12 `sky130_fd_sc_hd__dfxtp_2` (reports/synth_stat.rpt), and
`design__instance__count__class:sequential_cell` = 12 in output/metrics.json. So the surviving sequential cells are
12, one more than the 11 RTL bits. The synthesis log shows Yosys detected `state` as an FSM and extracted it
(`runs/RUN_2026-10-05_16-30-06/06-yosys-synthesis/yosys-synthesis.log`, "Found FSM state register"); the same log
says "mapping auto encoding to `one-hot` for this FSM": the 3-state `state` becomes 3 one-hot flops instead of 2
binary ones, so 11 - 2 + 3 = 12. `scripts/flow/check_signoff.py` counts the same 12 when it elaborates the RTL. All 12 flops are clocked by the 3 clock buffers (cts.rpt: Total number of Sinks: 12).

## Data flow

Example input: tokens `1 2 3 1` = GOOD FINE BAD GOOD, producer never stalls, consumer always ready.
Embeddings (ROM): GOOD +1, FINE 0, BAD -1, GOOD +1. Expected sum = +1, so class 1, score +1.
`python3 model/tiny_ai/golden.py text_sentiment 1 2 3 1` prints `beat0=0x01 beat1=0x01 latency=1`, matching.

"Edge N" is the Nth rising clock edge after reset is released; register values are shown after that edge.

| Cycle (after edge) | Input handshake | Output handshake | State | ROM lookup | count | acc | error | m_data |
|---|---|---|---|---|---|---|---|---|
| 0 (idle) | s_ready=1, s_valid=0 | m_valid=0 | LOAD | n/a | 0 | 0 | 0 | not used |
| 1 | beat 0 taken: GOOD, s_last=0 | m_valid=0 | LOAD | 1 -> +1 | 1 | +1 | 0 | not used |
| 2 | beat 1 taken: FINE, s_last=0 | m_valid=0 | LOAD | 2 -> 0 | 2 | +1 | 0 | not used |
| 3 | beat 2 taken: BAD, s_last=0 | m_valid=0 | LOAD | 3 -> -1 | 3 | 0 | 0 | not used |
| 4 | beat 3 taken: GOOD, s_last=1 | m_valid=0 | OUT0 | 1 -> +1 | 4 | +1 | 0 | not used |
| 5 | s_ready=0 | m_valid=1, m_ready=1: beat 0 taken | OUT1 | n/a | 4 | +1 | 0 | beat 0 was 0x01 (error 0, class 1) |
| 6 | s_ready=0 | m_valid=1, m_last=1, m_ready=1: beat 1 taken | LOAD | n/a | 0 | 0 | 0 | beat 1 was 0x01 (score +1) |

Notes: during cycle 4 (before edge 5) the outputs already show `m_valid=1` with `m_data = 0x01`; the "output
handshake" column lists the beat consumed at the following edge. `error` stays 0 because on the 4th beat
`count == 3`, so the "s_last too early" test is false. The result is visible 1 cycle after the last input beat
(spec latency 1, golden.py `latency=1`).

```mermaid
sequenceDiagram
    participant P as Producer
    participant D as text_sentiment
    participant C as Consumer
    Note over D: state LOAD, s_ready high
    P->>D: s_valid, s_data=1 GOOD
    Note over D: acc = +1, count = 1
    P->>D: s_valid, s_data=2 FINE
    Note over D: acc = +1, count = 2
    P->>D: s_valid, s_data=3 BAD
    Note over D: acc = 0, count = 3
    P->>D: s_valid, s_data=1 GOOD, s_last
    Note over D: acc = +1, count = 4, state OUT0, s_ready low
    D->>C: m_valid, m_data=0x01 error 0 class 1
    C->>D: m_ready
    D->>C: m_valid, m_data=0x01 score +1, m_last
    C->>D: m_ready
    Note over D: clear acc, count, error, back to LOAD
```

The testbench `tb/text_sentiment_tb.v` includes `../../shared/tb/stream_tb.vh` and runs every case of
`tb/vectors.hex` (269 cases per the file header) with random stalls on both sides.

## Layout (GDSII)

![layout](output/layout.png)

- Die: `design__die__bbox` = 0 0 80 80 um (die area 6400 um^2). Core: `design__core__bbox` = 5.52 10.88 74.06 68.0 um
  (core area 3915 um^2) (metrics.json). `design__rows` = 21 standard-cell rows, 3129 sites.
- Core utilization (`design__instance__utilization`) = 0.349 (34.9 percent) (same value as design__instance__utilization__stdcell).
  Synthesis-only utilization was 0.221 in floorplan.txt; the rest is clock, timing-repair and tap cells added later.
- In the picture: the large grey area is empty die; the rectangle in the middle is the core. Horizontal bands are the
  standard-cell rows; most of the area is decap and tap filler (green and orange cell outlines). The real logic is
  the small clusters of coloured wiring in between (metal 1 to metal 4, the magenta and blue lines).
- Pins sit on the die edges as thin lines running out of the core: a group on the left edge, a group on the right
  edge, a few at the top and several at the bottom (`design__io` = 26 in metrics.json, 24 signal pins per
  `floorplan__design__io` plus power; README lists 24 pins).
- Two wide vertical bands inside the core look like the vertical power straps, with a regular horizontal power rail
  at every cell row. The files I read do not state the strap pitch, so this is read from the picture.
- Fill: `design__instance__count__class:fill_cell` = 727 and tap cells = 57 (metrics.json). Total instances 927,
  of which 200 are real standard cells (`design__instance__count__stdcell`), area 1367.56 um^2.

## From RTL to GDSII: what each step did

The flow is LibreLane with the sky130A PDK and sky130_fd_sc_hd cells (config.json). Clock period 25 ns, die fixed at
80 x 80 um, routing up to met4. Run folder: `runs/RUN_2026-10-05_16-30-06`. Lint: 0 errors, 446 warnings
(flow.log; `design__lint_warning__count` in metrics.json), 0 inferred latches.

### Synthesis
Yosys turns the RTL into 83 gates and flops, area 867.082 um^2, of which sequential 255.245 um^2 (29.44 percent)
(reports/synth_stat.rpt). By type: 12 flops (dfxtp_2), 9 nor2, 6 each of a21oi, and2, nand2, xnor2, 3 each of
a21o, a31o, buf_2, mux2, o2bb2a, 5 inverters, 2 each of a32o, and3, nand2b, or2, or4, and 1 each of a221o, a22o,
a22oi, o211a, o21a, o221a, o22a, o31a. The CHECK pass "Found and reported 0 problems"
(reports/synth_checks.rpt); `synthesis__check_error__count` = 0 and unmapped cells = 0 (metrics.json).
Report: [output/reports/synth_stat.rpt](output/reports/synth_stat.rpt),
[output/reports/synth_checks.rpt](output/reports/synth_checks.rpt).

### Floorplan
Absolute sizing: die 0 0 80 80 um, core 5.52 10.88 74.06 68.0 um, core area 3915.005 um^2, 21 rows
(IFP-0102, IFP-0001 lines in floorplan.txt, but note that log first printed a 149-site row count and a slightly
different requested core box; the final bbox is the one in metrics.json). Instances 83, area 867.082 um^2, effective
utilization 0.221. No tie cells were needed (IFP-0030 inserted 0). Report:
[output/reports/floorplan.txt](output/reports/floorplan.txt).

### Placement
Global placement (placement_global.txt): target density 0.3397, 83 movable instances plus 99 fixed (tap and fill),
overflow went from 0.6328 at iteration 0 to 0.0990 at iteration 317, final HPWL 1566.6 um. Routability check:
total routing overflow 0, weighted congestion 0.7466 (target 1.01), no inflation. Detailed placement
(placement_detailed.txt): total/average/max displacement 0.0 u, HPWL 1958.8 u legalized then 1901.7 u after
optimizing (-2.9 percent), 48 instances mirrored. Timing repair after placement added buffers: 44
`timing_repair_buffer` cells, 321.558 um^2 (metrics.json). Reports:
[output/reports/placement_global.txt](output/reports/placement_global.txt),
[output/reports/placement_detailed.txt](output/reports/placement_detailed.txt).

### Clock tree
cts.rpt: 1 clock root, 3 buffers inserted (all `sky130_fd_sc_hd__clkbuf_16`), 3 clock subnets, 12 sinks (the 12
flops). Worst clock skew in metrics.json: setup 0.2512 ns, hold -0.2512 ns (`clock__skew__worst_*`); the same value
is dominated by the 0.25 ns clock uncertainty set in the SDC (flow log lines "Setting clock uncertainty to: 0.25").
Clock buffer area 75.072 um^2. Hold fixing inserted 6 hold buffers and 0 setup buffers
(`design__instance__count__hold_buffer`, `__setup_buffer`). Report: [output/reports/cts.rpt](output/reports/cts.rpt).

### Routing
Global routing (routing_global.txt): 142 routed nets, wirelength 3650 um, 744 vias, congestion 8.37 percent total
usage (met1 12.22, met2 13.11, met3 1.01, met4 0.00), 0 overflow. metrics.json reports
`global_route__wirelength` = 3739 and `global_route__vias` = 763 (measured after later repair, so slightly higher).
Detailed routing (routing_detailed.txt, `route__drc_errors__iter:N`): 4 passes with 32, 5, 10, then 0 violations
(metal spacing and shorts on met1). Final wirelength 2252 um (met1 1104, met2 999, met3 148, nothing on li1 or met4),
791 vias, all single-cut (`route__vias__multicut` = 0), longest wire 85.47 um (`route__wirelength__max`),
143 nets. Ten DRT-0349 warnings are LEF58 rules the router skips (flow.log, harmless). Reports:
[output/reports/routing_global.txt](output/reports/routing_global.txt),
[output/reports/routing_detailed.txt](output/reports/routing_detailed.txt).

### Timing
Clock 25 ns; input and output delay 5 ns. All 9 corners (nom, min, max, each at tt, ss, ff) meet timing
(reports/timing_summary.rpt): worst setup slack 14.7485 ns (corner max_ss_100C_1v60), worst hold slack 0.1078 ns
(corner min_ff_n40C_1v95), both 0 violations, TNS 0. Per nominal corner (setup / hold, ns):
tt_025C_1v80 17.5732 / 0.3376; ss_100C_1v60 14.7621 / 0.9517; ff_n40C_1v95 18.4957 / 0.1093.
Worst setup path (timing_paths_max_ss.rpt): starts at input port `s_data[3]` and ends at flop `_137_` D pin,
data arrival 10.388 ns, slack 14.748 ns. The path goes through two `or4_2` gates, which is the "token above 3"
check feeding the accumulator update. Worst hold path (timing_paths_min_ff.rpt): flop `_141_` back to itself,
slack 0.107764 ns. `timing__setup_r2r__ws` is Infinity in metrics.json: there is no register-to-register setup
path measured; all critical setup paths start at input ports. Reports:
[output/reports/timing_summary.rpt](output/reports/timing_summary.rpt),
[output/reports/timing_paths_max_ss.rpt](output/reports/timing_paths_max_ss.rpt),
[output/reports/timing_paths_min_ff.rpt](output/reports/timing_paths_min_ff.rpt).

### DRC
Design Rule Check compares the drawn shapes against the foundry's geometric rules (minimum spacing, width,
enclosure). Magic: COUNT 0 (reports/drc_magic.rpt; `magic__drc_error__count` = 0). KLayout: every rule in
reports/drc_klayout.json is 0 (`klayout__drc_error__count` = 0). XOR between the two GDS writers:
`design__xor_difference__count` = 0. Reports: [output/reports/drc_magic.rpt](output/reports/drc_magic.rpt),
[output/reports/drc_klayout.json](output/reports/drc_klayout.json).

### LVS
Layout Versus Schematic extracts the transistors and wires from the layout and checks that they are the same
circuit as the synthesized netlist. Result line in lvs_netgen.rpt: "Final result: Circuits match uniquely."
Device count 147 on both sides, net count 145 on both sides (780 parallel devices merged). All
`design__lvs_*` counts in metrics.json are 0. Report:
[output/reports/lvs_netgen.rpt](output/reports/lvs_netgen.rpt). Summary:
[output/reports/manufacturability.rpt](output/reports/manufacturability.rpt) (Antenna, LVS, DRC all Passed).

### Power / IR drop
irdrop.rpt (corner nom_tt_025C_1v80): vccd1 supply 1.80 V, average IR drop 1.44e-05 V, worst 1.31e-04 V
(0.01 percent); vssd1 worst bounce 1.16e-04 V. Power grid violations: 0 (metrics.json). Total power
`power__total` = 7.24e-05 W (internal 5.50e-05, switching 1.74e-05, leakage 4.11e-09). Report:
[output/reports/irdrop.rpt](output/reports/irdrop.rpt).

### Antenna, slew, capacitance
Antenna: `antenna__violating__nets` = 0, `route__antenna_violation__count` = 0; 13 diode cells were inserted
(`design__instance__count__class:antenna_cell`; the key `antenna_diodes_count` itself reads 0). Max slew, max cap
and max fanout violations are 0 in every corner (metrics.json). Two floating nets are noted
(`timing__drv__floating__nets` = 2) with 0 disconnected pins (`design__disconnected_pin__count`). Flow warnings: 1
type (ORD-0039 once; ODB-0220 2, STA-1140 6, RSZ-0020 1, DRT-0349 10 in the per-code counts), 0 errors.
Cell mix after routing: [output/reports/cell_usage.rpt](output/reports/cell_usage.rpt).

## Run time and memory

From output/resources.json (profile "tight": 2 CPUs, 8 GB limit):

- Total wall time: 45 s (`wall_s_total`). Exit code 0.
- Peak container memory: 601387008 bytes = 0.56 GB (`container_peak_mem_gb`); largest single-step process RSS
  530579456 bytes (`peak_rss_bytes_flow_stats_max`, the netgen LVS step 72).
- Three slowest steps: 46-openroad-detailedrouting 5.155 s, 35-openroad-cts 3.92 s,
  67-klayout-drc 2.458 s (57-openroad-stapostpnr is next at 2.403 s).

## Reproduce

```bash
make simulate DESIGN=text_sentiment        # RTL testbench against tb/vectors.hex
make flow-all DESIGN=text_sentiment        # simulate, gds, check, gate-level (synthesised and routed), collect
make collect DESIGN=text_sentiment         # refresh output/ (layout.png, metrics.json, reports/) from the latest run
python3 model/tiny_ai/golden.py text_sentiment 1 2 3 1   # reference answer: beat0=0x01 beat1=0x01 latency=1
```
